"""Reference checker: turns the model's ``Analysis[Claim]`` into a
``CheckedAnalysis`` by resolving every reference against the parsed input.

What a kept label promises: the facts the claim cites exist in the input
(this pin is on that net, this ERC entry exists, …). It does not promise the
claim's conclusion follows from them. That is still the model's reasoning.

Rules for a claim with evidence other than ``none``:

1. Every citation resolves (pin on that net; pin has that name in the
   netlist, and the text uses it; ERC id exists; pin in the pin table;
   measurement id exists).
2. At least one citation is of the kind its label needs (schematic needs a
   pin_on_net or pin_name, and so on).
3. Every pin and net the text names is cited. This catches a claim that
   cites one pin but states a fact about two.

A claim that breaks a rule drops to ``none`` with ``downgraded=True`` and a
reason. Outside claims, unknown symptom, hypothesis and pin references are
removed or reported as ``Problem``s.
"""

import re
from dataclasses import dataclass

from app.features.debug.design import PIN_MENTION, Design, normalize_pin
from app.features.debug.schemas import (
    EVIDENCE_CITE_KINDS,
    Analysis,
    CheckedAnalysis,
    CheckedClaim,
    Cite,
    Claim,
    ErcEntry,
    Hypothesis,
    Problem,
    SetupChange,
)


@dataclass(frozen=True)
class EvidenceContext:
    """Everything a reference can resolve to.

    ``pin_data`` holds the REF.PIN refs the hand-written pin table covers."""

    design: Design
    erc: dict[str, ErcEntry]
    pin_data: frozenset[str]
    symptom_ids: frozenset[str]
    measurement_ids: frozenset[str]


def check(analysis: Analysis[Claim], ctx: EvidenceContext) -> CheckedAnalysis:
    """Check every reference in ``analysis``; see the module docstring."""
    problems: list[Problem] = []
    hypothesis_ids = {h.id for h in analysis.hypotheses}
    _report_duplicates([h.id for h in analysis.hypotheses], "hypothesis", problems)
    _report_duplicates(
        [s.id for h in analysis.hypotheses for s in h.confirm], "confirm step", problems
    )

    hypotheses = []
    for h in analysis.hypotheses:
        claims = [check_claim(c, ctx) for c in h.claims]
        explains = _known(h.explains, ctx.symptom_ids, h.id, "symptom", problems)
        not_explains = _known(h.does_not_explain, ctx.symptom_ids, h.id, "symptom", problems)
        confirm = []
        for step in h.confirm:
            problems += _check_setup(step.id, step.setup, ctx.design)
            readings = [
                r.model_copy(
                    update={
                        "supports": _known(
                            r.supports, hypothesis_ids, step.id, "hypothesis", problems
                        ),
                        "rules_out": _known(
                            r.rules_out, hypothesis_ids, step.id, "hypothesis", problems
                        ),
                    }
                )
                for r in step.readings
            ]
            confirm.append(step.model_copy(update={"readings": readings}))
        hypotheses.append(
            Hypothesis[CheckedClaim](
                **h.model_dump(exclude={"claims", "explains", "does_not_explain", "confirm"}),
                claims=claims,
                explains=explains,
                does_not_explain=not_explains,
                confirm=confirm,
            )
        )
    return CheckedAnalysis(
        hypotheses=sorted(hypotheses, key=lambda h: h.rank),
        open_questions=analysis.open_questions,
        glossary=analysis.glossary,
        problems=problems,
    )


def check_claim(claim: Claim, ctx: EvidenceContext) -> CheckedClaim:
    """Keep the claim's label if its references hold, otherwise downgrade it."""
    base = claim.model_dump()
    if claim.evidence == "none":
        return CheckedClaim(**base)
    reason = _claim_failure(claim, ctx)
    if reason is None:
        return CheckedClaim(**base)
    return CheckedClaim(
        **{**base, "evidence": "none"},
        downgraded=True,
        claimed_evidence=claim.evidence,
        reason=reason,
    )


def _claim_failure(claim: Claim, ctx: EvidenceContext) -> str | None:
    """The first rule the claim breaks, as a sentence for the UI, or None."""
    for cite in claim.cites:
        failure = _cite_failure(cite, ctx)
        if failure:
            return failure
        # A pin_name cite vouches for the name the netlist gives, so the
        # text must use that name, not one the model assumed.
        if cite.kind == "pin_name" and cite.pin:
            # KiCad marks active-low names with overbar markup: ~{RESET}.
            name = re.sub(r"[~{}]", "", ctx.design.pin_name(normalize_pin(cite.pin)))
            if not _names(name, claim.text):
                return f"Your schematic names {normalize_pin(cite.pin)} {name}, not as stated."

    needed = EVIDENCE_CITE_KINDS[claim.evidence]
    if not any(c.kind in needed for c in claim.cites):
        kinds = " or ".join(sorted(needed))
        return f"It is labelled {claim.evidence} but cites no {kinds} reference."

    cited_pins = {normalize_pin(c.pin) for c in claim.cites if c.pin}
    cited_nets = {
        ctx.design.resolve_net(c.net) for c in claim.cites if c.kind == "pin_on_net" and c.net
    }
    # An ERC entry vouches for the nets and parts its message and items name.
    erc_text = " ".join(
        f"{e.message} {' '.join(e.items)}"
        for c in claim.cites
        if c.kind == "erc" and c.id and (e := ctx.erc.get(c.id))
    )
    for pin in mentioned_pins(claim.text):
        if pin not in cited_pins:
            return f"It mentions {pin} but doesn't cite it."
    for short, full in mentioned_nets(claim.text, ctx.design).items():
        if full not in cited_nets and not _names(short, erc_text):
            return f"It mentions net {short} but doesn't cite it."
    return None


def _cite_failure(cite: Cite, ctx: EvidenceContext) -> str | None:
    """Why ``cite`` doesn't resolve, or None if it does."""
    design = ctx.design
    match cite.kind:
        case "pin_on_net":
            if not cite.pin or not cite.net:
                return "A schematic reference is missing its pin or net."
            pin = normalize_pin(cite.pin)
            actual = design.net_of(pin)
            if actual is None:
                return f"Pin {pin} isn't in your schematic."
            if design.resolve_net(cite.net) != actual:
                return f"{pin} is on {actual}, not {cite.net}, in your schematic."
        case "pin_name":
            if not cite.pin:
                return "A pin-name reference is missing its pin."
            pin = normalize_pin(cite.pin)
            if design.net_of(pin) is None:
                return f"Pin {pin} isn't in your schematic."
            if not design.pin_name(pin):
                return f"Your schematic gives {pin} no name."
        case "erc":
            if not cite.id or cite.id not in ctx.erc:
                return f"KiCad's checker has no entry {cite.id}."
        case "pin_function":
            if not cite.pin:
                return "A datasheet reference is missing its pin."
            pin = normalize_pin(cite.pin)
            if pin not in ctx.pin_data:
                return f"The pin table has no entry for {pin}."
        case "measurement":
            if not cite.id or cite.id not in ctx.measurement_ids:
                return f"No measurement {cite.id} was entered."
    return None


def mentioned_pins(text: str) -> set[str]:
    """REF.PIN refs named in ``text`` ('U2.9', 'U2 pin 9')."""
    return {f"{m.group(1)}.{m.group(2)}" for m in PIN_MENTION.finditer(text)}


def mentioned_nets(text: str, design: Design) -> dict[str, str]:
    """Nets named in ``text``, as short name -> full name. Only unambiguous
    short names count, so a bare 'VCC' shared by two sheets isn't guessed."""
    return {short: full for short, full in design.short_net_names().items() if _names(short, text)}


def _names(name: str, text: str) -> bool:
    """Whole-token match: '+3V3' matches in 'the +3V3 rail', not in '+3V30'."""
    return re.search(rf"(?<![\w+/-]){re.escape(name)}(?![\w+-])", text) is not None


def _known(
    ids: list[str], known: set[str] | frozenset[str], where: str, what: str, problems: list[Problem]
) -> list[str]:
    """``ids`` with unknown entries removed, each removal reported."""
    for unknown in [i for i in ids if i not in known]:
        problems.append(Problem(where=where, message=f"Removed unknown {what} {unknown}."))
    return [i for i in ids if i in known]


def _report_duplicates(ids: list[str], what: str, problems: list[Problem]) -> None:
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(Problem(where=dup, message=f"Two {what}s share the id {dup}."))


def _check_setup(step_id: str, setup: list[SetupChange], design: Design) -> list[Problem]:
    """Each setup change must name pins and nets that exist, and a
    disconnect must name the net the pin is actually on."""
    found = []
    for change in setup:
        pin = normalize_pin(change.pin)
        actual = design.net_of(pin)
        if actual is None:
            found.append(f"Setup names pin {pin}, which isn't in your schematic.")
        elif change.action == "disconnect":
            if not change.net or design.resolve_net(change.net) != actual:
                found.append(f"Setup disconnects {pin} from {change.net}, but it is on {actual}.")
        elif (change.net is None) == (change.to_pin is None):
            found.append(f"Setup connects {pin} to neither or both of a net and a pin.")
        elif change.net and design.resolve_net(change.net) is None:
            found.append(f"Setup connects {pin} to net {change.net}, which doesn't exist.")
        elif change.to_pin and design.net_of(normalize_pin(change.to_pin)) is None:
            found.append(f"Setup connects {pin} to {change.to_pin}, which isn't in your schematic.")
    return [Problem(where=step_id, message=m) for m in found]

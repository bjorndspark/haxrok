"""A microcontroller's pins against its datasheet's pin rules.

The rules come from the datasheet (``PinRuleFact``, read with the facts), or
from the hand-written pin table (``pins.PartPins.rules``) where no datasheet
gave any. Each is checked on the netlist alone:

- reserved (in-package flash or PSRAM, "do not connect"): the pin's net
  reaches another part. Flagged, verified.
- input only: the pin is the only thing that could drive an LED or a
  transistor on its net (directly or through one resistor). Flagged.
- strapping: the pin's level at reset, from the netlist with every switch
  and jumper open and capacitors ignored: tied (its net is ground or a
  rail) or pulled (resistors to ground or to a rail, all one way). Tied to
  the level the normal boot doesn't want is flagged; pulled to it is
  flagged when nothing else is on the net, and "may fail" (listed) when
  something is, since that may drive it. A load (another IC, an LED, a
  connector, a resistor to anything else) with no defined level is "may
  fail". A pin the normal boot doesn't depend on (``either``) isn't
  checked.
- strapping, RC: a pin left at its boot level (by external pulls toward
  it, or by its internal pull alone) whose rule carries timing
  (``pins.StrapTiming``: the datasheet's own numbers when it printed all
  three, else the pin table's): the internal pull and the pulls toward
  the boot level in parallel, against the capacitors straight from its net
  to the other level, τ = R × C, and the time t = τ·ln(1/(1 − f)) to cover
  the fraction f of VDD up to the input threshold. Past the hold time is
  flagged when the internal pull is alone: the chip and its pull are off
  until EN rises, so the cap charges from the other level then. With an
  external pull it's "may fail": that pull runs from the rail before EN
  rises and may precharge the cap, which the netlist doesn't show; with a
  load on the net too, since that may drive it.
- USB D+ / D-: the pin's net (directly or through one series resistor)
  reaches a pin named for the other line (a connector's D-), and none
  named for its own. Flagged: the lines are swapped.

A rule with a ``condition`` (a variant the part's value doesn't settle) is
never flagged, only listed with its condition. Results are
``Calculation``s (kind ``mcu_pin``), one per pin and rule, flagged first.
Pure functions, no I/O.
"""

import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from app.features.debug.calc import (
    format_farads,
    format_ohms,
    format_seconds,
    is_ground,
    named_voltage,
    parse_farads,
    parse_ohms,
)
from app.features.debug.design import Design, Node, is_ic
from app.features.debug.pins import PIN_TABLE, PartPins, StrapTiming, pin_order
from app.features.debug.schemas import (
    BootLevel,
    Calculation,
    PinRuleFact,
    PinRuleKind,
    PowerTree,
)


@dataclass(frozen=True)
class Rule:
    """One pin rule of one IC, from its datasheet or the pin table."""

    kind: PinRuleKind
    pins: tuple[str, ...]
    text: str
    source: str  # "datasheet p.12", or the pin table's document
    boot_level: BootLevel = "either"
    condition: str = ""
    timing: StrapTiming | None = None  # strapping only, for the RC check


def rules(
    design: Design, datasheet: Mapping[str, Sequence[PinRuleFact]] | None = None
) -> dict[str, list[Rule]]:
    """Every IC's pin rules: its datasheet's (``datasheet``, ref -> rules)
    where that gave any, else the pin table's."""
    out: dict[str, list[Rule]] = {}
    for ref, part in design.parts.items():
        if not is_ic(ref):
            continue
        entry = next(
            (e for e in PIN_TABLE if e.matches(part.value, part.lib_part, part.footprint)), None
        )
        if read := (datasheet or {}).get(ref):
            out[ref] = [_rule(f, f"datasheet p.{f.page}", entry) for f in read]
        elif entry is not None and entry.rules:
            out[ref] = [_rule(f, entry.source, entry) for f in entry.rules]
    return out


def _rule(fact: PinRuleFact, source: str, entry: PartPins | None) -> Rule:
    return Rule(
        kind=fact.kind,
        pins=tuple(fact.pins),
        text=fact.text,
        source=source,
        boot_level=fact.boot_level,
        condition=fact.condition,
        timing=_timing(fact, entry),
    )


def _timing(fact: PinRuleFact, entry: PartPins | None) -> StrapTiming | None:
    """A strapping rule's numbers for the RC check: its own when it gave all
    three, else the pin table's whole set. One source, never mixed."""
    if fact.kind != "strapping" or not fact.pins:
        return None
    return _own_timing(fact) or _table_timing(fact, entry)


def _own_timing(fact: PinRuleFact) -> StrapTiming | None:
    """The datasheet's numbers, when it printed the pull, the threshold and
    the hold time, and its pull is toward the boot level."""
    if fact.pull_kohms is None or fact.threshold is None or fact.hold_ms is None:
        return None
    if fact.internal_pull is None or fact.internal_pull != fact.boot_level:
        return None
    return StrapTiming(
        pull=fact.internal_pull,
        pull_ohms=fact.pull_kohms * 1e3,
        threshold=fact.threshold,
        hold_s=fact.hold_ms * 1e-3,
    )


def _table_timing(fact: PinRuleFact, entry: PartPins | None) -> StrapTiming | None:
    """The pin table's timing, when every pin the rule names has the same
    entry there and its pull is toward the rule's boot level."""
    if entry is None:
        return None
    found = {entry.strap_timing.get(p) for p in fact.pins}
    timing = found.pop() if len(found) == 1 else None
    return timing if timing is not None and timing.pull == fact.boot_level else None


@dataclass
class _Level:
    """What sets a pin's level at reset: a tie, pulls, and anything else
    on its net that may drive it."""

    tied: BootLevel | None = None
    pulls: dict[str, BootLevel] = field(default_factory=dict)  # resistor ref -> way
    loads: list[str] = field(default_factory=list)

    @property
    def level(self) -> BootLevel | None:
        """The level the netlist sets: the tie, else the pulls if they all
        pull one way; None when nothing (or a divider) sets it."""
        if self.tied:
            return self.tied
        ways = set(self.pulls.values())
        return ways.pop() if len(ways) == 1 else None


def check(
    design: Design, tree: PowerTree, rules: Mapping[str, Sequence[Rule]], first_id: int = 1
) -> list[Calculation]:
    """One calculation per IC pin used against its rule, flagged ones
    first, ids ``cf<first_id>``…; see the module docstring."""
    rails = {r.net for r in tree.rails if not is_ground(r.net)}
    out: list[Calculation] = []
    for ref in sorted(rules):
        if ref not in design.parts:
            continue
        for rule in rules[ref]:
            for pin in sorted(rule.pins, key=pin_order):
                pin_ref = f"{ref}.{pin}"
                net = design.net_of(pin_ref)
                if net is None:
                    continue
                found = _check_pin(design, rails, rule, pin_ref, net)
                if found is not None:
                    out.append(found)
    out.sort(key=lambda c: not c.flagged)
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(out, start=first_id)]


def _check_pin(
    design: Design, rails: Collection[str], rule: Rule, pin_ref: str, net: str
) -> Calculation | None:
    match rule.kind:
        case "reserved":
            others = sorted({n.ref for n in design.nets[net] if n.pin_ref != pin_ref})
            if not others and not _is_rail(net, rails):
                return None
            on = ", ".join(others) if others else net
            return _calc(
                design,
                rule,
                pin_ref,
                [],
                f"is reserved, but its net {net} reaches {on}.",
                "Leave it unconnected.",
            )
        case "input_only":
            driven = _driven_loads(design, net, pin_ref)
            if not driven:
                return None
            return _calc(
                design,
                rule,
                pin_ref,
                [],
                f"is input only, but it's the only pin that could drive "
                f"{', '.join(driven)} on {net}.",
                "Move the output to a pin that can drive.",
            )
        case "strapping":
            if rule.boot_level == "either":
                return None
            return _strapping(design, rails, rule, pin_ref, net)
        case "usb_d_plus" | "usb_d_minus":
            own, other = (_PLUS, _MINUS) if rule.kind == "usb_d_plus" else (_MINUS, _PLUS)
            ends = _usb_ends(design, net, pin_ref)
            wrong = [n.pin_ref for n in ends if other.fullmatch(n.function)]
            if not wrong or any(own.fullmatch(n.function) for n in ends):
                return None
            line = "D+" if rule.kind == "usb_d_plus" else "D-"
            names = ", ".join(f"{p} ({design.pin_name(p)})" for p in wrong)
            return _calc(
                design,
                rule,
                pin_ref,
                wrong,
                f"is the built-in USB's {line}, but its net {net} reaches {names}: "
                "D+ and D- are swapped.",
                f"Wire it to the connector's {line}.",
            )
    return None


def _strapping(
    design: Design, rails: Collection[str], rule: Rule, pin_ref: str, net: str
) -> Calculation | None:
    want = rule.boot_level
    at = _level(design, rails, net, pin_ref)
    level = at.level
    pulls = sorted(at.pulls)
    if at.tied and at.tied != want:
        what = f"is tied {at.tied} ({net})"
        flagged = True
    elif level and level != want:
        what = f"is pulled {level} by {', '.join(pulls)} ({net})"
        flagged = not at.loads
        what += f"; {', '.join(at.loads)} also on its net may override that" if at.loads else ""
    elif level is None and at.loads:
        how = f" ({', '.join(pulls)} pull both ways)" if pulls else ""
        what = f"has no defined level at reset{how}, and {', '.join(at.loads)} on {net} may set it"
        flagged = False
    elif rule.timing is not None and not at.tied and all(w == want for w in at.pulls.values()):
        return _rc(design, rails, rule, rule.timing, pin_ref, net, at)
    else:
        return None
    resistors = [f"{r}.{p}" for r in pulls for p in ("1", "2")]
    fix = f"It must be {want} at reset for the normal boot from flash; " + (
        f"pull it {'up' if want == 'high' else 'down'} with a resistor"
        if flagged
        else "check what drives it during reset"
    )
    return _calc(
        design,
        rule,
        pin_ref,
        resistors,
        f"is a strapping pin and {what}.",
        fix + ".",
        unsure="" if flagged else "the netlist doesn't show the level during reset",
    )


def _rc(
    design: Design,
    rails: Collection[str],
    rule: Rule,
    timing: StrapTiming,
    pin_ref: str,
    net: str,
    at: _Level,
) -> Calculation | None:
    """A strapping pin at its boot level, against the time its pulls take
    to bring it there from the other level; None when it's in time or the
    netlist lacks a value (module docstring, "strapping, RC")."""
    want = rule.boot_level
    pulls = sorted(at.pulls)  # all toward ``want``: the caller checked
    away = is_ground if want == "high" else (lambda far: _is_rail(far, rails))
    caps = [
        n.ref
        for n in design.nets[net]
        if _head(n.ref) == "C" and (far := _other_end(design, n.ref, net)) and away(far)
    ]
    ohms = [parse_ohms(design.parts[r].value) or 0.0 for r in pulls]
    farads = [parse_farads(design.parts[c].value) or 0.0 for c in caps]
    if not caps or not all(ohms) or not all(farads):
        return None  # no cap, or a value the netlist doesn't give
    c_all = sum(farads)
    r_all = 1 / (1 / timing.pull_ohms + sum(1 / r for r in ohms))
    span = timing.threshold if want == "high" else 1 - timing.threshold
    per_tau = math.log(1 / (1 - span))  # the crossing time in units of τ
    tau = r_all * c_all
    t = tau * per_tau
    if t <= timing.hold_s:
        return None
    way, low_high, other, toward = (
        ("up", "low", "GND", "the supply")
        if want == "high"
        else ("down", "high", "the supply", "GND")
    )
    vth = f"{'VIH' if want == 'high' else 'VIL'} ({timing.threshold:g} × VDD)"
    by = f"its internal pull-{way} ({format_ohms(timing.pull_ohms)})" + "".join(
        f" and {r} ({format_ohms(o)})" for r, o in zip(pulls, ohms, strict=True)
    )
    held = ", ".join(f"{c} ({format_farads(f)})" for c, f in zip(caps, farads, strict=True))
    # What brings it there in time: the pull in all, an added resistor beside
    # the internal pull alone, or the most capacitance the pulls charge in time.
    r_need = timing.hold_s / (c_all * per_tau)
    added = 1 / (1 / r_need - 1 / timing.pull_ohms)  # beside the internal pull alone
    stronger = (
        f"a pull-{way} under {format_ohms(_round3(added))} to {toward}"
        if not pulls
        else f"stronger pull-{way}s ({format_ohms(_round3(r_need))} in all, with the internal one)"
    )
    c_max = format_farads(_round3(timing.hold_s / (r_all * per_tau)))
    smaller = f"shrink {caps[0]} to under {c_max}" if len(caps) == 1 else f"under {c_max} in all"
    if pulls:
        unsure = (
            f"{', '.join(pulls)} run{'s' if len(pulls) == 1 else ''} from the rail before EN "
            "rises, and an EN delay much longer than τ charges the pin before the chip starts"
        )
    elif at.loads:
        unsure = f"{', '.join(at.loads)} on {net} may drive it during reset"
    else:
        unsure = ""
    return _calc(
        design,
        rule,
        pin_ref,
        [f"{ref}.{p}" for ref in (*caps, *pulls) for p in ("1", "2")],
        f"is a strapping pin brought {want} by {by}, and {held} to {other} holds it "
        f"{low_high} until then: τ = {format_seconds(tau)}, so it reaches {vth} "
        f"{format_seconds(t)} after EN rises, past the {format_seconds(timing.hold_s)} "
        "hold time in which the chip latches it.",
        f"It must be {want} at reset for the normal boot from flash; add {stronger}, or {smaller}.",
        unsure=unsure,
        value=round(t * 1e3, 3),
        unit="ms",
        expected=timing.hold_s * 1e3,
    )


def _round3(x: float) -> float:
    """``x`` to three significant figures, for a limit in the text."""
    return float(f"{x:.3g}")


def _calc(
    design: Design,
    rule: Rule,
    pin_ref: str,
    others: list[str],
    what: str,
    fix: str,
    unsure: str = "",
    value: float | None = None,
    unit: str = "",
    expected: float | None = None,
) -> Calculation:
    ref = pin_ref.split(".")[0]
    name = design.pin_name(pin_ref)
    named = f"{pin_ref} ({name})" if name else pin_ref
    flagged = not unsure and not rule.condition
    text = (
        f"{ref} ({design.parts[ref].value}): {named} {what} {fix} "
        f'Rule ({rule.source}): "{rule.text}"'
    )
    if rule.condition:
        text += f" Not flagged: it applies {rule.condition}; check that it applies to this part."
    elif unsure:
        text += f" May fail, not verified: {unsure}."
    return Calculation(
        id="",
        kind="mcu_pin",
        ic=ref,
        pins=[pin_ref, *others],
        value=value,
        unit=unit,
        expected=expected,
        flagged=flagged,
        text=text,
    )


# --- netlist reading -----------------------------------------------------------------

# USB data line names on other parts' pins: a connector's D+ / DP1 / D_P,
# a bridge's UD+ or USB_DP; the minus side alike.
_PLUS = re.compile(r"(U|USB[_-]?)?D(\+|P|_P)\d?", re.IGNORECASE)
_MINUS = re.compile(r"(U|USB[_-]?)?D(-|−|M|N|_M|_N)\d?", re.IGNORECASE)

# Parts that don't drive a pin at reset: a switch or jumper is taken as open,
# a capacitor sets no DC level, a test point is a pad.
_QUIET = re.compile(r"^(SW|JP|C|TP)\d", re.IGNORECASE)
_SWITCH_LIB = re.compile(r"SW|SPDT|SPST|JUMPER", re.IGNORECASE)


def _head(ref: str) -> str:
    return ref.rstrip("0123456789")


def _is_rail(net: str, rails: Collection[str]) -> bool:
    return net in rails or (named_voltage(net) or 0) > 0


def _quiet(design: Design, ref: str) -> bool:
    part = design.parts.get(ref)
    return _QUIET.match(ref) is not None or (
        part is not None and _SWITCH_LIB.search(part.lib_part) is not None
    )


def _other_end(design: Design, ref: str, net: str) -> str | None:
    """A two-pin part's net at its other end."""
    nets = [
        other
        for other, nodes in design.nets.items()
        if other != net and any(n.ref == ref for n in nodes)
    ]
    return nets[0] if len(nets) == 1 else None


def _level(design: Design, rails: Collection[str], net: str, pin_ref: str) -> _Level:
    """What sets ``net``'s level at reset, seen from ``pin_ref``."""
    at = _Level()
    if is_ground(net):
        at.tied = "low"
        return at
    if _is_rail(net, rails):
        at.tied = "high"
        return at
    for node in design.nets[net]:
        if node.pin_ref == pin_ref or _quiet(design, node.ref) or node.ref in at.pulls:
            continue
        if _head(node.ref) == "R" and (far := _other_end(design, node.ref, net)):
            if is_ground(far):
                at.pulls[node.ref] = "low"
                continue
            if _is_rail(far, rails):
                at.pulls[node.ref] = "high"
                continue
            # A resistor to a button or a pad only is still open at reset.
            if all(n.ref == node.ref or _quiet(design, n.ref) for n in design.nets[far]):
                continue
        if node.ref not in at.loads:
            at.loads.append(node.ref)
    return at


def _driven_loads(design: Design, net: str, pin_ref: str) -> list[str]:
    """LEDs and transistors on ``net`` (or one resistor on) that only
    ``pin_ref`` could drive; [] when another IC or a connector is there."""
    nets = [net]
    for node in design.nets[net]:
        far = _other_end(design, node.ref, net) if _head(node.ref) == "R" else None
        if far and not is_ground(far) and (named_voltage(far) or 0) <= 0:
            nets.append(far)
    nodes = [n for on in nets for n in design.nets[on] if n.pin_ref != pin_ref]
    if any(is_ic(n.ref) or _head(n.ref) in {"J", "P", "CN"} for n in nodes):
        return []
    return sorted({n.ref for n in nodes if _head(n.ref) == "Q" or _is_led(design, n.ref)})


def _is_led(design: Design, ref: str) -> bool:
    part = design.parts.get(ref)
    return (
        part is not None
        and _head(ref) in {"D", "LED"}
        and part.lib_part.rsplit(":", 1)[-1].upper().startswith("LED")
    )


def _usb_ends(design: Design, net: str, pin_ref: str) -> list[Node]:
    """Other parts' pins on ``net``, and one series resistor on."""
    ends = [n for n in design.nets[net] if n.pin_ref != pin_ref]
    for node in list(ends):
        if _head(node.ref) == "R" and (far := _other_end(design, node.ref, net)):
            ends += [n for n in design.nets[far] if n.ref != node.ref]
    return [n for n in ends if _head(n.ref) not in {"R", "C"}]

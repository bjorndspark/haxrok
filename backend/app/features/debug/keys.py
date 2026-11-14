"""Each finding's stable key and the fingerprints of the parts it names
(plan 011 step 1), so a later review can tell the same finding on a new
revision, and whether the circuit it is about changed.

A finding's id (``cf3``, ``wf1``, ``h2``) is its place in one run's list, so
it can't be the key. The backend's findings get an exact key from what they
are about: a calculation's kind and the pins of its part, a wiring finding's
pin, a pin warning's part. A model issue is free text, so its key is the set
of parts its verified cites name; a later step matches two such sets
loosely, never as "the same".

A part's fingerprint hashes its value, footprint and, for each of its pins,
the net it is on. A small net counts by its other pins (refs and values),
never by name, since KiCad's auto names (``Net-(C12-Pad1)``) change when
parts are renumbered. A ground net or one with more than ``_BIG_NET`` pins
counts by name only, so a part added anywhere on GND or a rail doesn't
change every part on it.

Pure functions over the design and the run's findings; the review records
the keys (``record``'s ``keys`` line) and nothing reads them back yet.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence

from app.features.debug.calc import is_ground
from app.features.debug.design import Design, Node, normalize_pin
from app.features.debug.schemas import (
    Calculation,
    CheckedAnalysis,
    CheckedClaim,
    FindingKey,
    PinWarning,
    WiringFinding,
)

# A net with more pins than this is a rail or a bus: it counts by name.
_BIG_NET = 8

# Calculations made once per part, whose pins list what the finding found
# (a connector's unprotected lines) rather than what it is about.
_ONE_PER_PART = frozenset({"part_rating", "connector_esd"})

# Cite kinds whose ``pin`` names a part.
_PIN_CITES = frozenset({"pin_on_net", "pin_name", "pin_function"})


def build(
    design: Design,
    analysis: CheckedAnalysis,
    wiring: Sequence[WiringFinding],
    warnings: Sequence[PinWarning],
    calculations: Sequence[Calculation],
) -> list[FindingKey]:
    """Every finding's key: the calculations, wiring findings and pin
    warnings, then the model's issues, each in its list's order."""
    prints = fingerprints(design)

    def keyed(source: str, id: str, key: str, refs: Iterable[str]) -> FindingKey:
        parts = {ref: prints[ref] for ref in sorted(set(refs)) if ref in prints}
        return FindingKey.model_validate({"source": source, "id": id, "key": key, "parts": parts})

    found = [keyed("calculation", c.id, calculation_key(c), _calc_parts(c)) for c in calculations]
    found += [keyed("wiring", w.id, f"wiring:{w.pin}", _wiring_parts(w)) for w in wiring]
    found += [keyed("pin_warning", w.id, f"pin_convention:{w.ref}", [w.ref]) for w in warnings]
    by_id = {c.id: c for c in calculations}
    for h in analysis.hypotheses:
        refs = sorted(_issue_parts(h.claims, by_id) & prints.keys())
        found.append(keyed("issue", h.id, "issue:" + ",".join(refs), refs))
    return found


def calculation_key(calc: Calculation) -> str:
    """``<kind>:`` and the pins of the part it is about (``pin_voltage:U3.7``),
    or the part alone when it has no pins listed or is checked once per part
    (``part_rating:C12``). The other parts' pins (a divider's resistors)
    aren't in it: changing them changes the circuit, not the finding."""
    own = sorted(p for p in calc.pins if _ref(p) == calc.ic)
    if calc.kind in _ONE_PER_PART or not own:
        return f"{calc.kind}:{calc.ic}"
    return f"{calc.kind}:{','.join(own)}"


def fingerprints(design: Design) -> dict[str, str]:
    """Each part's fingerprint: 16 hex digits of a SHA-256 over its value,
    footprint and the nets its pins are on (see the module docstring)."""
    pins: dict[str, list[tuple[str, str]]] = {ref: [] for ref in design.parts}
    for name, nodes in design.nets.items():
        for node in nodes:
            if node.ref in pins:
                pins[node.ref].append((node.pin, _net(design, name, nodes, node)))
    return {
        ref: hashlib.sha256(
            json.dumps([part.value, part.footprint, sorted(pins[ref])]).encode()
        ).hexdigest()[:16]
        for ref, part in design.parts.items()
    }


def _net(design: Design, name: str, nodes: Sequence[Node], node: Node) -> str:
    """How one pin's net counts in its part's fingerprint."""
    if is_ground(name) or len(nodes) > _BIG_NET:
        return f"net {name}"
    others = sorted(
        f"{n.pin_ref}={design.parts[n.ref].value if n.ref in design.parts else ''}"
        for n in nodes
        if n != node
    )
    return " ".join(others)


def _calc_parts(calc: Calculation) -> list[str]:
    return [calc.ic, *(_ref(p) for p in calc.pins)]


def _wiring_parts(finding: WiringFinding) -> list[str]:
    return [_ref(p) for p in [finding.pin, *finding.conflicts, *finding.inductors]]


def _issue_parts(claims: Sequence[CheckedClaim], by_id: dict[str, Calculation]) -> set[str]:
    """The parts a hypothesis's verified claims cite: each cited pin's part,
    and the parts of each cited calculation. A downgraded claim's cites
    don't count; nor do nets (GND would name half the board), ERC entries
    or datasheet facts."""
    parts: set[str] = set()
    for claim in claims:
        if claim.downgraded:
            continue
        for cite in claim.cites:
            if cite.kind in _PIN_CITES and cite.pin:
                parts.add(_ref(normalize_pin(cite.pin)))
            elif cite.kind == "reference" and cite.id in by_id:
                parts.update(_calc_parts(by_id[cite.id]))
    return parts


def _ref(pin_ref: str) -> str:
    """``U2.9`` -> ``U2``."""
    return pin_ref.split(".", 1)[0]

"""Each finding's stable key and the fingerprints of the pins it names
(plan 011 steps 1 and 3), so a later review can tell the same finding on a
new revision, and whether the circuit it is about changed.

A finding's id (``cf3``, ``wf1``, ``h2``) is its place in one run's list, so
it can't be the key. The backend's findings get an exact key from what they
are about: a calculation's kind and the pins of its part, a wiring finding's
pin, a pin warning's part. A model issue is free text, so its key is the set
of pins its verified cites name; a later review matches two such sets
loosely, never as "the same".

Each finding carries a ``pins`` map: every pin it names, with that pin's
fingerprint. A finding about a whole part (``part_rating``,
``connector_esd``, ``pin_convention``) names all that part's pins. A pin's
fingerprint hashes its part's value and footprint and the net the pin is
on. A small net counts by its other pins (refs and values), never by name,
since KiCad's auto names (``Net-(C12-Pad1)``) change when parts are
renumbered. A ground net or one with more than ``_BIG_NET`` pins counts by
name only, so a part added anywhere on GND or a rail doesn't change every
pin on it. An edit on one pin of an IC leaves its other pins' fingerprints
alone.

Pure functions over the design and the run's findings; the review records
the keys (``record``'s ``keys`` line) and ``review_file`` compares them.
"""

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence

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
    by_part: dict[str, list[str]] = {}
    for pin in prints:
        by_part.setdefault(_ref(pin), []).append(pin)

    def keyed(source: str, id: str, key: str, pins: Iterable[str]) -> FindingKey:
        named = {p: prints[p] for p in sorted(set(pins)) if p in prints}
        return FindingKey.model_validate({"source": source, "id": id, "key": key, "pins": named})

    def whole(ref: str) -> list[str]:
        return by_part.get(ref, [])

    found = [
        keyed("calculation", c.id, calculation_key(c), _calc_pins(c, whole)) for c in calculations
    ]
    found += [keyed("wiring", w.id, f"wiring:{w.pin}", [w.pin]) for w in wiring]
    found += [keyed("pin_warning", w.id, f"pin_convention:{w.ref}", whole(w.ref)) for w in warnings]
    by_id = {c.id: c for c in calculations}
    for h in analysis.hypotheses:
        pins = sorted(_issue_pins(h.claims, by_id) & prints.keys())
        found.append(keyed("issue", h.id, "issue:" + ",".join(pins), pins))
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
    """Each pin's fingerprint (``U2.9`` -> 16 hex digits of a SHA-256 over
    its part's value and footprint and the net the pin is on; see the
    module docstring). Pins of parts the netlist doesn't list are left out."""
    prints: dict[str, str] = {}
    for name, nodes in design.nets.items():
        for node in nodes:
            part = design.parts.get(node.ref)
            if part is None:
                continue
            seen = json.dumps([part.value, part.footprint, _net(design, name, nodes, node)])
            prints[node.pin_ref] = hashlib.sha256(seen.encode()).hexdigest()[:16]
    return prints


def _net(design: Design, name: str, nodes: Sequence[Node], node: Node) -> str:
    """How one pin's net counts in its fingerprint."""
    if is_ground(name) or len(nodes) > _BIG_NET:
        return f"net {name}"
    others = sorted(
        f"{n.pin_ref}={design.parts[n.ref].value if n.ref in design.parts else ''}"
        for n in nodes
        if n != node
    )
    return " ".join(others)


def _calc_pins(calc: Calculation, whole: Callable[[str], list[str]]) -> list[str]:
    """The pins a calculation names: all its part's pins when it is about the
    whole part (checked once per part, or listing no pins), else its pins."""
    if calc.kind in _ONE_PER_PART or not calc.pins:
        return [*whole(calc.ic), *calc.pins]
    return list(calc.pins)


def _issue_pins(claims: Sequence[CheckedClaim], by_id: dict[str, Calculation]) -> set[str]:
    """The pins a hypothesis's verified claims cite: each cited pin, and the
    pins each cited calculation lists (none when it lists none). A
    downgraded claim's cites don't count; nor do nets (GND would name half
    the board), ERC entries or datasheet facts."""
    pins: set[str] = set()
    for claim in claims:
        if claim.downgraded:
            continue
        for cite in claim.cites:
            if cite.kind in _PIN_CITES and cite.pin:
                pins.add(normalize_pin(cite.pin))
            elif cite.kind == "reference" and cite.id in by_id:
                pins.update(by_id[cite.id].pins)
    return pins


def _ref(pin_ref: str) -> str:
    """``U2.9`` -> ``U2``."""
    return pin_ref.split(".", 1)[0]

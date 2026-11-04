"""A small wiring diagram per hypothesis, built from the pins and nets it
cites: the IC it is mostly about, those pins, the nets they are on and what
else sits on each net. The UI draws it; the backend builds it because only
the backend has the netlist.

Drawn from the netlist, never from the model's words, so a diagram can't
show a connection the schematic doesn't have. Pins wired against their
datasheet function are marked, so the UI can draw them in red: those the
pin-table check found, and those a flagged UART or enable-pin calculation
names.
"""

from collections import Counter

from app.features.debug.design import Design, normalize_pin
from app.features.debug.evidence import mentioned_pins
from app.features.debug.pins import is_ic, is_inductor
from app.features.debug.schemas import (
    Calculation,
    CheckedClaim,
    Diagram,
    DiagramMember,
    DiagramNet,
    DiagramPin,
    Hypothesis,
    WiringFinding,
)

# Enough to show a switch node, its rail and the pins around them; more
# rows stop being a glance.
MAX_PINS = 6
MAX_MEMBERS = 4

# Calculations that are wiring faults, not values: each pin they name is
# wired against its function (TX on TX, EN on its own output). The UI's
# "miswired" badge is the same pair.
_MISWIRED_KINDS = {"uart", "enable_pin"}


def miswired_pins(findings: list[WiringFinding], calculations: list[Calculation]) -> set[str]:
    """REF.PINs a check proved wired against their function, to draw red."""
    pins = {f.pin for f in findings}
    for c in calculations:
        if c.flagged and c.kind in _MISWIRED_KINDS:
            pins.update(c.pins)
    return pins


def build(h: Hypothesis[CheckedClaim], design: Design, miswired: set[str]) -> Diagram | None:
    """The diagram for ``h``, or None when it cites no IC pin. ``miswired``
    (from ``miswired_pins``) are drawn red."""
    cited = _cited_pins(h)
    cited_nets = {
        net
        for c in h.claims
        for cite in c.cites
        if cite.kind == "pin_on_net" and cite.net and (net := design.resolve_net(cite.net))
    }
    ic_counts = Counter(p.split(".")[0] for p in cited if is_ic(p.split(".")[0]))
    if not ic_counts:
        return None
    ic = ic_counts.most_common(1)[0][0]

    pins = [p for p in cited if p.startswith(f"{ic}.")]
    # Pins of the IC that share a net with a cited pin belong in the
    # picture: L2's net is interesting because VOUT is on it too.
    shown_nets = {design.net_of(p) for p in pins} | cited_nets
    for net in sorted(n for n in shown_nets if n):
        pins += [n.pin_ref for n in design.nets[net] if n.ref == ic and n.pin_ref not in pins]
    # Grouped by net, the flagged pin's net first, so each net is one bus.
    flagged_nets = {design.net_of(p) for p in pins if p in miswired}
    pins = sorted(
        dict.fromkeys(pins),
        key=lambda p: (design.net_of(p) not in flagged_nets, _net_order(design, p)),
    )[:MAX_PINS]

    nets: list[DiagramNet] = []
    for net in dict.fromkeys(design.net_of(p) for p in pins):
        if net is None:
            continue
        others = [n for n in design.nets[net] if n.ref != ic]
        # A small net is listed whole (the ILIM resistor matters); on a
        # rail, only inductors and other ICs are worth naming.
        notable = others
        if len(others) > MAX_MEMBERS:
            notable = [n for n in others if is_inductor(design, n.ref) or is_ic(n.ref)]
        notable.sort(key=lambda n: (not is_inductor(design, n.ref), n.ref, n.pin))
        members = [
            DiagramMember(
                pin=n.pin_ref,
                name=n.function,
                inductor=is_inductor(design, n.ref),
            )
            for n in notable[:MAX_MEMBERS]
        ]
        nets.append(DiagramNet(name=net, members=members, more=len(others) - len(members)))

    part = design.parts.get(ic)
    return Diagram(
        ic=ic,
        value=part.value if part else "",
        pins=[
            DiagramPin(
                pin=p,
                name=design.pin_name(p),
                net=design.net_of(p) or "",
                flagged=p in miswired,
            )
            for p in pins
        ],
        nets=nets,
    )


def _cited_pins(h: Hypothesis[CheckedClaim]) -> list[str]:
    """Pins the claims cite or name, in order of first mention."""
    found: list[str] = []
    for c in h.claims:
        found += [normalize_pin(cite.pin) for cite in c.cites if cite.pin]
        found += sorted(mentioned_pins(c.text))
    return list(dict.fromkeys(found))


def _net_order(design: Design, pin: str) -> tuple[str, int]:
    """Group pins by net, then by pin number."""
    number = pin.split(".", 1)[1]
    return design.net_of(pin) or "", int(number) if number.isdigit() else 0

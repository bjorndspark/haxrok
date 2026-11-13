"""Signal lines from a connector to an IC pin with no ESD part on them.

A connector is a part whose designator (J, P, CN, CON, USB) or symbol
library ("Connector…") says so. Each of its pins on a signal net (not
ground, not a power-tree rail, not a supply or shield pin) is followed
through series parts (resistors, ferrites, chokes, fuses, jumpers) to the
IC pins it reaches. The line is protected when any net on that path has a
TVS or ESD part: a diode (not an LED) from it to ground or a rail, or a
part whose name says it's one (USBLC6, TPD…, PRTR…, PESD…, D_TVS, KiCad's
Power_Protection library). A series resistor alone is not protection.

- **External kinds** (USB, RJ45, SMA/U.FL, barrel, audio, SD/SIM card),
  known by symbol, value or footprint: an unprotected line is listed as
  "may fail", one calculation per connector. An IC's own ESD rating
  doesn't drop it (on-chip HBM protection is for handling, not a user
  touching a port), but a datasheet fact that gives one is quoted.
- **Other connectors** (pin headers, JST, …): the schematic can't say
  whether they leave the box, so their unprotected lines are listed as
  context, one calculation per connector, for the model to judge from
  names and labels.

Results are ``Calculation``s (kind ``connector_esd``, never flagged),
numbered after the others. Pure functions, no I/O.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.features.debug.calc import is_ground
from app.features.debug.design import Design, is_ic, short_net_name
from app.features.debug.facts import Fact
from app.features.debug.schemas import Calculation, PowerTree

_CONNECTOR_HEADS = {"J", "P", "CN", "CON", "USB"}
# Kinds a user plugs things into from outside the box, by symbol, value or
# footprint. Checked in this order: "USB" first, since a USB symbol may
# also say "Receptacle".
_EXTERNAL = [
    ("USB", re.compile(r"USB", re.IGNORECASE)),
    ("RJ45", re.compile(r"RJ-?45|8P8C|Ethernet|MagJack", re.IGNORECASE)),
    ("RF", re.compile(r"(?<![A-Z])SMA(?![A-Z])|U\.?FL|IPEX|MHF|Coax|BNC", re.IGNORECASE)),
    ("barrel", re.compile(r"Barrel|DC[_-]?Jack", re.IGNORECASE)),
    ("audio", re.compile(r"Audio|TRR?S|Jack[_-]?3\.5|PJ-?3\d\d", re.IGNORECASE)),
    (
        "card",
        re.compile(
            r"(?<![A-Z])(?:micro)?SD(?:[_-]?Card)?(?![A-Z])|TF[_-]?Card|SIM[_-]?(?:Card|Holder)",
            re.I,
        ),
    ),
]
# A part that is an ESD or TVS device by its symbol, value or part number.
_PROTECTION = re.compile(
    r"Power_Protection|D_TVS|TVS|ESD|USBLC|TPD\d|PRTR|PESD|SRV05|RCLAMP|IP42\d\d|SP05\d"
    r"|NUP\d|SM[AB]J|SMF\d|ULC\d",
    re.IGNORECASE,
)
# A pin that is a supply, ground or shield by its name, not a signal.
_POWER_PIN = re.compile(
    r"^(?:V(?:BUS|CC|DD|IN|SS|EE|BAT)\w*|GND\w*|\w*SHIELD\w*|\w*SHELL\w*|AGND|PGND|EP|EXP)$",
    re.IGNORECASE,
)
# Two-pin (or choke) parts a signal passes straight through.
_SERIES_HEADS = {"R", "FB", "L", "F", "JP", "FL"}
_LISTED_PER_HEADER = 6


@dataclass(frozen=True)
class _Line:
    """One connector signal net and where it goes."""

    pin: str  # the connector's pin, "J1.A6"
    name: str  # its pin name or net, "D+"
    net: str
    ics: tuple[str, ...]  # IC pins reached, "U3.27(GPIO19/USB_D+)"
    via: tuple[str, ...]  # series parts passed on the way
    protected: bool


def check(
    design: Design, tree: PowerTree, facts: Sequence[Fact] = (), first_id: int = 1
) -> list[Calculation]:
    """One calculation per connector with a signal line that reaches an IC
    pin and has no ESD part on it, ids ``cf<first_id>``…, external kinds
    first; see the module docstring. ``facts`` are the datasheets', for an
    IC's own ESD rating."""
    rails = {r.net for r in tree.rails}
    part_nets: dict[str, list[str]] = {}
    for net, nodes in design.nets.items():
        for n in nodes:
            part_nets.setdefault(n.ref, []).append(net)
    external: list[Calculation] = []
    headers: list[Calculation] = []
    for ref in sorted(design.parts):
        if not _is_connector(design, ref):
            continue
        open_lines = [line for line in _lines(design, rails, part_nets, ref) if not line.protected]
        if not open_lines:
            continue
        kind = connector_kind(design, ref)
        if kind is not None:
            external.append(_external(design, ref, kind, open_lines, facts))
        else:
            headers.append(_header(design, ref, open_lines))
    out = external + headers
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(out, start=first_id)]


def connector_kind(design: Design, ref: str) -> str | None:
    """The external kind of a connector (USB, RJ45, RF, barrel, audio,
    card) from its symbol, value or footprint; None for a header or any
    other connector."""
    part = design.parts[ref]
    text = f"{part.lib_part} {part.value} {part.footprint}"
    if ref.rstrip("0123456789") == "USB":
        return "USB"
    return next((kind for kind, pattern in _EXTERNAL if pattern.search(text)), None)


def _is_connector(design: Design, ref: str) -> bool:
    """A connector by its ref or library; a test point is a probe pad, not a
    connector, even in KiCad's Connector library."""
    head = ref.rstrip("0123456789")
    lib, _, name = design.parts[ref].lib_part.partition(":")
    if head == ref or head == "TP" or "TestPoint" in name:
        return False
    return head in _CONNECTOR_HEADS or lib.startswith("Connector")


def _lines(
    design: Design, rails: set[str], part_nets: dict[str, list[str]], ref: str
) -> list[_Line]:
    """The connector's signal nets, one line each (a USB-C's A6 and B6
    share one), with what they reach."""
    seen: set[str] = set()
    lines: list[_Line] = []
    for net in part_nets.get(ref, []):
        mine = [n for n in design.nets[net] if n.ref == ref]
        if not mine or net in seen or not _signal(net, rails, mine[0].function):
            continue
        seen.add(net)
        reached, via = _walk(design, rails, part_nets, net, ref)
        ics = tuple(
            f"{n.pin_ref}({n.function})" if n.function else n.pin_ref
            for m in sorted(reached)
            for n in design.nets[m]
            if is_ic(n.ref) and not _is_protection(design, n.ref) and not _power_pin(n.function)
        )
        if not ics:
            continue
        protected = any(
            _protects(design, rails, part_nets, n.ref, m) for m in reached for n in design.nets[m]
        )
        name = mine[0].function or short_net_name(net)
        lines.append(_Line(mine[0].pin_ref, name, net, ics, tuple(sorted(via)), protected))
    return lines


def _walk(
    design: Design, rails: set[str], part_nets: dict[str, list[str]], start: str, connector: str
) -> tuple[set[str], set[str]]:
    """The nets reached from ``start`` through series parts, and those
    parts. A resistor to ground or a rail is a pull, not a series part, so
    the walk never enters a rail. A choke passes to all its signal nets:
    the pair it carries is then checked as one line."""
    reached, via, todo = {start}, set(), [start]
    while todo:
        net = todo.pop()
        for node in design.nets[net]:
            if node.ref == connector or node.ref.rstrip("0123456789") not in _SERIES_HEADS:
                continue
            for other in part_nets[node.ref]:
                if other in reached or is_ground(other) or other in rails:
                    continue
                reached.add(other)
                via.add(node.ref)
                todo.append(other)
    return reached, via


def _signal(net: str, rails: set[str], pin_name: str) -> bool:
    return (
        not is_ground(net)
        and net not in rails
        and not net.startswith("unconnected-")
        and not _power_pin(pin_name)
    )


def _power_pin(name: str) -> bool:
    return bool(name) and _POWER_PIN.match(name) is not None


def _is_protection(design: Design, ref: str) -> bool:
    part = design.parts.get(ref)
    if part is None:
        return False
    return _PROTECTION.search(f"{part.lib_part} {part.value} {part.mpn}") is not None


def _protects(
    design: Design, rails: set[str], part_nets: dict[str, list[str]], ref: str, net: str
) -> bool:
    """Whether ``ref``, which has a pin on ``net``, is an ESD part for it: a
    named TVS or ESD array, or a plain diode (not an LED) whose other pin
    is on ground or a rail."""
    if _is_protection(design, ref):
        return True
    part = design.parts[ref]
    if ref.rstrip("0123456789") != "D" or "LED" in f"{part.lib_part} {part.value}".upper():
        return False
    return any(is_ground(m) or m in rails for m in part_nets[ref] if m != net)


def _route(line: _Line) -> str:
    through = f" through {', '.join(line.via)}" if line.via else ""
    where = f"{line.pin}, net {short_net_name(line.net)}"
    return f"{line.name} ({where}) reaches {', '.join(line.ics)}{through}"


def _chip_ratings(lines: list[_Line], facts: Sequence[Fact]) -> str:
    """The reached ICs' own ESD ratings from their datasheet facts, quoted."""
    ics = {pin.split(".", 1)[0] for line in lines for pin in line.ics}
    rated = [f for f in facts if f.ref in ics and re.search(r"ESD|HBM|electrostatic", f.body, re.I)]
    if not rated:
        return ""
    quoted = "; ".join(f"{f.ref}: {f.body} ({f.id})" for f in rated)
    return f" The chip's own rating ({quoted}) covers handling, not a user touching the port."


def _external(
    design: Design, ref: str, kind: str, lines: list[_Line], facts: Sequence[Fact]
) -> Calculation:
    part = design.parts[ref]
    routes = "; ".join(_route(line) for line in lines)
    text = (
        f"{ref} ({part.value}), a {kind} connector a user plugs into: {routes}, with no TVS "
        "or ESD part on the line. A discharge at the port (IEC 61000-4-2: 8 kV contact) "
        "goes straight into the IC pin; on-chip protection (about 2 kV HBM) is for handling. "
        "May fail: a port that works, then dies after being plugged in. Fix: an ESD array "
        "(e.g. USBLC6-2 for USB 2.0) next to the connector."
    )
    return _calc(ref, lines, text + _chip_ratings(lines, facts))


def _header(design: Design, ref: str, lines: list[_Line]) -> Calculation:
    part = design.parts[ref]
    shown = lines[:_LISTED_PER_HEADER]
    more = len(lines) - len(shown)
    routes = "; ".join(_route(line) for line in shown) + (f"; and {more} more" if more else "")
    text = (
        f"{ref} ({part.value}), a header or internal connector: {routes}, with no ESD "
        "part. Context, not a finding: the schematic doesn't say whether it leaves the box. "
        "Only if its names or labels say it does (an external I/O or debug port) is it a "
        "may-fail risk; an internal or factory-only header is fine."
    )
    return _calc(ref, shown, text)


def _calc(ref: str, lines: list[_Line], text: str) -> Calculation:
    pins = [line.pin for line in lines] + [p.split("(", 1)[0] for line in lines for p in line.ics]
    return Calculation(
        id="",
        kind="connector_esd",
        ic=ref,
        pins=list(dict.fromkeys(pins)),
        value=None,
        unit="",
        expected=None,
        flagged=False,
        text=text,
    )

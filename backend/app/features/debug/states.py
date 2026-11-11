"""Operating states: which rails come up from which sources, and where a
rail that's down is fed backwards.

A state is a combination of the power tree's sources (USB only, battery
only, both): each non-empty one up to 3 sources, else each source alone and
all of them. In each, a rail is up when a part making it has an input up
and, for an IC, its enable lets it run:

- an enable tied to a rail (EN to VIN) or pulled up to one is high only
  while that rail is up; tied or pulled to ground it's low; driven by
  another IC's pin it's that IC's to set, so on while that IC is powered;
  with nothing on it, it's unknown and taken as on (noted);
- an active-low enable (``~{EN}``, ``SHDN``) runs while it's low.

A charger doesn't bring its battery net up: without a battery it isn't a
supply. A power mux or load switch is taken to block reverse current, as
integrated ones do; diodes conduct one way, and so does a load-sharing FET
(its gate turns it off once the other source is up, and its body diode
blocks the reverse path). Fuses, ferrites, 0 Ω links and
slide switches conduct both ways, so a rail up on their output side feeds
their input backwards.

Back-powering through signals, in each state, is "may fail", never
verified: the netlist can't show whether the live side drives high while
the other side is off.

- an IC whose supply rails are all down, with a signal pin on a net that a
  powered IC drives or that's pulled up to a live rail: its ESD diode feeds
  the dead rail;
- a pull-up to a rail that's down, on a net a powered IC drives: the line
  idles low and the live pin feeds the dead rail through the resistor.

A rail that's down in some state is information, not a finding. Pure
functions over the parsed design and its power tree, no I/O.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from app.features.debug.calc import is_ground, named_voltage
from app.features.debug.design import Design, Node, is_ic, short_net_name
from app.features.debug.roles import name_role
from app.features.debug.schemas import BackPower, OperatingState, PowerPart, PowerTree

# Every combination up to this many sources; with more, each alone and all.
MAX_COMBINED = 3

# Enable inputs, by normalized name. SHDN is a shutdown input: it runs low,
# and its inverted form (~{SHDN}) runs high.
_ENABLE = re.compile(r"(EN\d*|CE|ENABLE|CHIPEN|ON|SHDN)")
# Pins that sense or take a rail by design (VBUS detect, battery sense), not
# signals an ESD diode would conduct through.
_SENSE = re.compile(r"VBUS|VBAT|VSYS|SENSE|DET|VIN|VREF", re.IGNORECASE)
_POWER_ROLES = frozenset({"supply_in", "supply_out", "ground", "switch", "feedback", "enable"})
# Pass parts that conduct one way: diodes, and FETs, whose body diode
# blocks the reverse path once the gate turns them off.
_ONE_WAY = frozenset({"D", "Q"})


@dataclass(frozen=True)
class _Enable:
    pin: str  # "U4.3"
    name: str  # "EN"
    net: str
    runs_high: bool


@dataclass(frozen=True)
class _Level:
    """An enable net's level in one state: high, low, driven by an IC, or
    nothing on it; and in words, for the state's ``off`` line."""

    level: str  # "high" | "low" | "driven" | "floating"
    why: str


def walk(design: Design, tree: PowerTree) -> list[OperatingState]:
    """Every operating state of ``tree``; see the module docstring."""
    sources = [r.net for r in tree.rails if r.source is not None]
    if not sources:
        return []
    rails = [r.net for r in tree.rails]
    ic_rails: dict[str, set[str]] = {}
    for r in tree.rails:
        for ref in r.loads:
            ic_rails.setdefault(ref, set()).add(r.net)
    enables = {p.ref: _enables(design, p.ref) for p in tree.parts if is_ic(p.ref)}
    states: list[OperatingState] = []
    for chosen in _combinations(sources):
        up = _up(design, tree.parts, set(chosen), set(rails), enables, ic_rails)
        powered = _powered(ic_rails, up)
        states.append(
            OperatingState(
                sources=list(chosen),
                up=[n for n in rails if n in up],
                down=[n for n in rails if n not in up],
                off=_held_off(design, tree.parts, up, set(rails), enables, powered),
                back_powered=[
                    *_reverse(design, tree.parts, up),
                    *_signals(design, set(rails), up, ic_rails, powered),
                ],
            )
        )
    return states


def state_name(state: OperatingState) -> str:
    """ "VBUS only", "VBUS + VBAT"."""
    names = [short_net_name(n) for n in state.sources]
    return f"{names[0]} only" if len(names) == 1 else " + ".join(names)


def format_states(tree: PowerTree) -> list[str]:
    """The prompt's lines for the operating states, after the power tree's
    rails; none when there are no states."""
    if not tree.states:
        return []
    lines = ["Operating states (which rails are up from which sources):"]
    for s in tree.states:
        line = f"- {state_name(s)}: up {', '.join(map(short_net_name, s.up)) or 'nothing'}"
        if s.down:
            line += f"; down {', '.join(map(short_net_name, s.down))}"
        lines.append(line)
        lines += [f"  - {text}" for text in s.off]
        lines += [f"  - may fail, not verified: {b.text}" for b in s.back_powered]
    return lines


# --- the walk ----------------------------------------------------------------


def _combinations(sources: Sequence[str]) -> list[tuple[str, ...]]:
    if len(sources) > MAX_COMBINED:
        return [(s,) for s in sources] + [tuple(sources)]
    return [c for k in range(1, len(sources) + 1) for c in combinations(sources, k)]


def _up(
    design: Design,
    parts: Sequence[PowerPart],
    chosen: set[str],
    rails: set[str],
    enables: Mapping[str, list[_Enable]],
    ic_rails: Mapping[str, set[str]],
) -> set[str]:
    """The rails up from ``chosen``, grown until nothing changes: a part's
    output comes up once an input is up and its enable lets it run."""
    up = set(chosen)
    changed = True
    while changed:
        changed = False
        powered = _powered(ic_rails, up)
        for p in parts:
            if p.kind == "charger" or p.output in up or not set(p.inputs) & up:
                continue
            if all(_runs(design, e, up, rails, powered) for e in enables.get(p.ref, [])):
                up.add(p.output)
                changed = True
    return up


def _powered(ic_rails: Mapping[str, set[str]], up: set[str]) -> set[str]:
    """ICs with every supply rail up. One with only some up (a module's
    header symbol on the battery and +3V3) is taken as neither driving its
    pins nor dead."""
    return {ref for ref, on in ic_rails.items() if on <= up}


def _runs(design: Design, e: _Enable, up: set[str], rails: set[str], powered: set[str]) -> bool:
    level = _level(design, e, up, rails, powered).level
    if level in {"driven", "floating"}:
        return True
    return (level == "high") == e.runs_high


def _held_off(
    design: Design,
    parts: Sequence[PowerPart],
    up: set[str],
    rails: set[str],
    enables: Mapping[str, list[_Enable]],
    powered: set[str],
) -> list[str]:
    """One line per IC whose input is up but whose enable holds it off, and
    per enable taken as on with nothing on it."""
    lines: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if p.ref in seen or not set(p.inputs) & up or p.ref not in enables:
            continue
        seen.add(p.ref)
        for e in enables[p.ref]:
            lv = _level(design, e, up, rails, powered)
            if lv.level == "floating":
                lines.append(f"{p.ref} taken as on: {e.pin} ({e.name}) {lv.why}")
            elif lv.level != "driven" and (lv.level == "high") != e.runs_high:
                lines.append(f"{p.ref} held off: {e.pin} ({e.name}) {lv.why}")
    return lines


def _enables(design: Design, ref: str) -> list[_Enable]:
    """``ref``'s enable pins. ``~{EN}``, ``/EN`` and ``nEN`` are active low;
    ``SHDN`` is active low too, so ``~{SHDN}`` runs high."""
    found = []
    for net, nodes in design.nets.items():
        for n in nodes:
            if n.ref != ref:
                continue
            fn = n.function.removesuffix(f"_{n.pin}")
            key = re.sub(r"[^A-Z0-9]", "", fn.upper())
            inverted = fn.startswith(("~", "/", "!"))
            if not _ENABLE.fullmatch(key) and key.startswith("N") and _ENABLE.fullmatch(key[1:]):
                key, inverted = key[1:], True
            if _ENABLE.fullmatch(key):
                found.append(_Enable(n.pin_ref, fn, net, (key != "SHDN") != inverted))
    return found


def _level(design: Design, e: _Enable, up: set[str], rails: set[str], powered: set[str]) -> _Level:
    net = e.net
    if is_ground(net):
        return _Level("low", "is tied to ground")
    if net in rails or named_voltage(net) is not None:
        state = "up" if net in up or net not in rails else "down"
        return _Level(
            "high" if state == "up" else "low", f"is tied to {short_net_name(net)}, {state}"
        )
    ref = e.pin.split(".")[0]
    drivers = sorted(
        {n.ref for n in design.nets.get(net, []) if n.ref != ref and is_ic(n.ref)} & powered
    )
    if drivers:
        return _Level("driven", f"is driven by {', '.join(drivers)}")
    ups, downs = [], []
    for r, other in _resistors(design, net):
        if is_ground(other):
            downs.append(r)
        elif other in rails or named_voltage(other) is not None:
            ups.append((r, other))
    live = [(r, o) for r, o in ups if o in up or o not in rails]
    if live:
        r, o = live[0]
        return _Level("high", f"is pulled up to {short_net_name(o)} by {r}, up")
    if ups:
        r, o = ups[0]
        return _Level("low", f"is pulled up to {short_net_name(o)} by {r}, down")
    if downs:
        return _Level("low", f"is pulled to ground by {downs[0]}")
    return _Level("floating", "has nothing driving it; check whether it has an internal pull")


# --- back-powering -------------------------------------------------------------


def _reverse(design: Design, parts: Sequence[PowerPart], up: set[str]) -> list[BackPower]:
    """Rails down in this state that a two-way pass part feeds backwards
    from its output."""
    found: list[BackPower] = []
    for p in parts:
        if p.kind != "pass" or _head(p.ref) in _ONE_WAY or p.output not in up:
            continue
        for net in p.inputs:
            if net in up:
                continue
            value = design.parts[p.ref].value if p.ref in design.parts else ""
            closed = " while it's closed" if _head(p.ref) == "SW" else ""
            found.append(
                BackPower(
                    rail=net,
                    via=[p.ref],
                    text=(
                        f"{short_net_name(net)} is down, but {p.ref} ({value}) conducts both "
                        f"ways{closed}, so {short_net_name(p.output)} feeds it backwards"
                    ),
                )
            )
    return found


def _signals(
    design: Design,
    rails: set[str],
    up: set[str],
    ic_rails: Mapping[str, set[str]],
    powered: set[str],
) -> list[BackPower]:
    """Signal nets joining a powered side to a rail that's down: through an
    unpowered IC's pins (their ESD diodes; one entry per IC), or through a
    pull-up to that rail (one per resistor)."""
    pins: dict[str, list[str]] = {}  # dead IC -> "U6.4 on SDA (from U1.8)"
    via: dict[str, list[str]] = {}
    pull_ups: list[BackPower] = []
    for net, nodes in sorted(design.nets.items()):
        if net in rails or is_ground(net) or named_voltage(net) is not None:
            continue
        resistors = _resistors(design, net)
        if any(is_ground(other) for _, other in resistors):
            continue  # a divider or a pull-down: the pins don't float on the dead side
        live = [n.pin_ref for n in nodes if n.ref in powered and _signal(n)]
        drivers = live + [r for r, o in resistors if o in rails and o in up]
        short = short_net_name(net)
        for n in nodes:
            on = ic_rails.get(n.ref, set())
            if drivers and on and not on & up and _signal(n):
                pins.setdefault(n.ref, []).append(f"{n.pin_ref} on {short} (from {drivers[0]})")
                via.setdefault(n.ref, []).append(n.pin_ref)
        for r, o in resistors:
            if live and o in rails and o not in up:
                pull_ups.append(
                    BackPower(
                        rail=o,
                        via=[r, *live],
                        text=(
                            f"{r} pulls {short} up to {short_net_name(o)}, which is down: "
                            f"{', '.join(live)} driving it high feeds {short_net_name(o)} "
                            f"through {r}"
                        ),
                    )
                )
    found = []
    for ref in sorted(pins, key=_ref_key):
        dead = sorted(ic_rails[ref])
        names = ", ".join(map(short_net_name, dead))
        shown = pins[ref][:3] + ([f"{len(pins[ref]) - 3} more"] if len(pins[ref]) > 3 else [])
        found.append(
            BackPower(
                rail=dead[0],
                via=via[ref],
                text=(
                    f"{ref}'s supply {names} is down, but its pins are driven: "
                    f"{'; '.join(shown)}. Their ESD diodes can back-power {names}"
                ),
            )
        )
    return found + pull_ups


def _ref_key(ref: str) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def _signal(n: Node) -> bool:
    """A pin an ESD diode conducts through: not power, ground, an enable or
    a sense input."""
    if not is_ic(n.ref):
        return False
    fn = n.function.removesuffix(f"_{n.pin}")
    return name_role(fn, n.pin) not in _POWER_ROLES and not _SENSE.search(fn)


def _resistors(design: Design, net: str) -> list[tuple[str, str]]:
    """(ref, the net at its other end) for each two-pin resistor on ``net``."""
    found = []
    for n in design.nets.get(net, []):
        if _head(n.ref) != "R":
            continue
        ends = [
            other
            for other, nodes in design.nets.items()
            for m in nodes
            if m.ref == n.ref and m.pin != n.pin
        ]
        if len(ends) == 1:
            found.append((n.ref, ends[0]))
    return found


def _head(ref: str) -> str:
    m = re.match(r"[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""

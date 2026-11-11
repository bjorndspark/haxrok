"""Simulates each regulator's circuit with its vendor SPICE model, and
checks the waveform against the datasheet limits (plan 004 step 6).

For each IC whose model loads (``spice_convert.check``), ``plan`` cuts its
circuit out of the netlist: the regulator's pin nets, with the passives
(R, C, L, D) whose both ends are on them or on ground. The input is a
source ramped up at power-on, the ICs on the output are replaced by a
resistor drawing the datasheet's rated output current, and a reset RC on
the output to another IC's pin is kept and probed. Each analysis the user
picked becomes one deck:

- ``startup``: the rail settles within the output accuracy, doesn't
  overshoot the downstream ICs' maximum supply, and a reset RC releases
  only after the rail is in band;
- ``load_step``: from 10 % to the rated current once the rail is up; the
  droop stays above the downstream ICs' minimum supply and the rail
  recovers into band;
- ``dropout``: the input swept down until the output leaves its band;
  flagged when that happens within 10 % of the nominal input. Only for a
  regulator whose datasheet gives a dropout voltage.

The limits come from the datasheet facts (``facts.DatasheetReading.limits``):
the regulator's rated current, accuracy (±5 % without one), start-up time,
dropout and typical input; the downstream ICs' supply range (nominal ±10 %
without one). A regulator that can't be set up this way (no rated current,
a model pin with no board pin, an enable driven by another IC) yields a
``skipped`` simulation that says why.

``simulate`` runs the decks (two at once) and yields each verdict as it
finishes. A run that times out or fails is ``timed_out`` or ``failed``,
with ngspice's error; it never fails the request.
"""

import asyncio
import re
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field, replace

from app.core.config import settings
from app.core.logging import get_logger
from app.features.debug.application import parse_henries
from app.features.debug.calc import (
    format_seconds,
    is_ground,
    named_voltage,
    parse_farads,
    parse_ohms,
)
from app.features.debug.design import Design, is_inductor
from app.features.debug.facts import DatasheetReading
from app.features.debug.schemas import (
    LimitFact,
    SimAnalysis,
    SimLevel,
    SimRamp,
    Simulation,
    SimVerdict,
    SpiceModelStatus,
)
from app.features.debug.spice import RESET_RELEASE, ModelRunner, Run, one_line, time_unit
from app.features.debug.spice_convert import MODES, ModelFiles, model_files, ngspice_error

logger = get_logger(__name__)

_PARALLEL = 2  # vendor-model runs at once, per request: each takes minutes
_RAMP = 50e-6  # the input's power-on ramp
_STEP = 0.25e-6  # print step, and the grid ``linearize`` puts the samples on
_WINDOW_NO_FACTS = 1e-3  # start-up window when the datasheet gives no times
_WINDOW_MAX = 5e-3
_LOAD_STEP_AFTER = 150e-6  # simulated after the load step
_SWEEP = 500e-6  # the dropout sweep's length
_LIGHT = 0.1  # the load step's light load, as a share of the rated current
_POINTS = 240  # samples sent to the UI (a min/max envelope)
_DECK_CHARS = 4000  # Simulation.deck's limit
_DEFAULT_ACCURACY = 0.05
_DEFAULT_SUPPLY = 0.10  # downstream supply range without facts: nominal ±10 %
_DROPOUT_HEADROOM = 0.10  # flagged when regulation is lost this close to nominal

# Pin names, normalized to uppercase letters and digits.
_IN_PIN = re.compile(r"P?V?IN\d*|VI")
_OUT_PIN = re.compile(r"V?OUT\d*|VO")
_SW_PIN = re.compile(r"LX\d*|SW\d*")
_ENABLE_PIN = re.compile(r"(EN|CE|SHDN|ON|ENABLE)\d*")
_GROUND_PIN = re.compile(r"(A|D|P|S)?GND\d*|VSS\d*|EP|E?PAD|THERMALPAD")
_IC_REF = re.compile(r"(U|IC)\d+", re.IGNORECASE)

# Facts, matched on LimitFact.parameter.
_CURRENT = re.compile(r"(output|load)\s+current|^I_?OUT\b|^IO\b", re.IGNORECASE)
_NOT_RATED = re.compile(r"sink|sourc|limit|short|leak|quiescent|shut|switch|protect", re.IGNORECASE)
_ACCURACY = re.compile(r"(output|feedback)\s+voltage.*(accura|toleran)", re.IGNORECASE)
_DROPOUT = re.compile(r"dropout", re.IGNORECASE)
_STARTUP = re.compile(
    r"start-?up|soft-?start|ramp\s+time|^t_?ramp|^tss|delay.*\b(en|enable)\b",
    re.IGNORECASE,
)
_NOT_STARTUP = re.compile(r"power[\s-]*good|\bPG\b", re.IGNORECASE)
_INPUT = re.compile(r"input\s+voltage|^V_?IN\b", re.IGNORECASE)
_VIN_CONDITION = re.compile(r"V_?IN\s*=\s*(\d+(?:\.\d+)?)\s*V\b", re.IGNORECASE)
_SUPPLY = re.compile(r"supply|\bV_?DD|\bV_?CC|IOVDD|operating\s+voltage", re.IGNORECASE)
_NOT_SUPPLY = re.compile(
    r"threshold|high|low|differ|variation|absolute|ripple|current|reset|\bPOR\b|\bBOR\b"
    r"|rejection",
    re.IGNORECASE,
)

# Units as LimitFact gives them, uppercased with µ as U.
_SCALE = {"": 1.0, "M": 1e-3, "U": 1e-6, "N": 1e-9}


@dataclass(frozen=True)
class Ratings:
    """A regulator's numbers from its datasheet facts (SI units)."""

    current: float | None = None  # rated output current, A
    current_page: int | None = None  # the datasheet page it's on
    accuracy: float = _DEFAULT_ACCURACY  # output accuracy, as a fraction
    accuracy_cited: bool = False
    dropout: float | None = None  # V
    startup: float | None = None  # summed start-up times, s
    vin: float | None = None  # typical input, V


@dataclass(frozen=True)
class Limits:
    """What one deck's waveform is checked against."""

    ref: str
    vout: float
    vin: float
    lo: float
    hi: float
    accuracy: str  # "±1 %", or "±5 %, not cited"
    over: float  # overshoot ceiling
    over_label: str
    droop: float  # load-step floor
    droop_label: str
    t_event: float  # the load step or the sweep's start; the start-up window
    stop: float
    dropout: float | None = None
    vin_end: float | None = None
    reset: str | None = None  # the reset pin the RC drives, "U16.7 (NRST)"


@dataclass(frozen=True)
class Job:
    """One deck to run, with what to check its waveform against."""

    ref: str
    analysis: SimAnalysis
    title: str
    deck: str
    limits: Limits
    files: dict[str, bytes] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Skip:
    """A regulator whose model loads but that isn't simulated, and why."""

    ref: str
    title: str
    reason: str


@dataclass(frozen=True)
class _Cut:
    """The regulator's circuit as deck lines, with the nodes named."""

    vin: float
    vout: float
    current: float  # the rated output current the load draws
    nodes: dict[str, str]  # board net -> deck node
    lines: list[str]  # the regulator instance and the kept passives
    reset: str | None  # "U16.7 (NRST)" when a reset RC is kept (node ``rst``)


# --- datasheet facts -------------------------------------------------------


def scaled(value: float | None, unit: str, kind: str) -> float | None:
    """``value`` in SI units when ``unit`` is of ``kind`` ("A", "V", "s")."""
    u = unit.strip().replace("μ", "U").replace("µ", "U").upper()
    if value is None or not u.endswith(kind.upper()):
        return None
    scale = _SCALE.get(u[: -len(kind)])
    return None if scale is None else value * scale


def ratings(limits: Sequence[LimitFact]) -> Ratings:
    """The regulator's rated current, accuracy, dropout, start-up time and
    typical input, as its datasheet facts give them."""
    current = None
    current_page = None
    accuracy = None
    dropout = None
    startup = 0.0
    timed = False
    vin = None
    conditions: Counter[float] = Counter()
    for lim in limits:
        p = lim.parameter
        for m in _VIN_CONDITION.finditer(lim.conditions):
            conditions[float(m.group(1))] += 1
        if current is None and _CURRENT.search(p) and not _NOT_RATED.search(p):
            # a rating may be only a guaranteed minimum ("min 2 A")
            rated = next((b for b in (lim.max, lim.typ, lim.min) if b is not None), None)
            current = scaled(rated, lim.unit, "A")
            current_page = lim.page
        elif accuracy is None and _ACCURACY.search(p) and lim.unit.strip() == "%":
            bounds = [abs(b) for b in (lim.min, lim.max) if b is not None]
            accuracy = max(bounds) / 100 if bounds else None
        elif dropout is None and _DROPOUT.search(p):
            dropout = scaled(lim.typ if lim.typ is not None else lim.max, lim.unit, "V")
        elif _STARTUP.search(p) and not _NOT_STARTUP.search(p):
            t = scaled(lim.typ if lim.typ is not None else lim.max, lim.unit, "s")
            if t is not None:
                startup += t
                timed = True
        elif vin is None and _INPUT.search(p):
            vin = scaled(lim.typ, lim.unit, "V")
    if vin is None and conditions:
        vin = conditions.most_common(1)[0][0]
    return Ratings(
        current=current if current and current > 0 else None,
        current_page=current_page if current and current > 0 else None,
        accuracy=accuracy or _DEFAULT_ACCURACY,
        accuracy_cited=accuracy is not None,
        dropout=dropout,
        startup=startup if timed else None,
        vin=vin,
    )


def regulator_of(design: Design, net: str) -> str | None:
    """The regulator feeding ``net``: an IC with an input pin and either an
    output pin (by its symbol's pin name) on ``net`` (an LDO) or a switch
    pin that reaches ``net`` through an inductor (a buck). None for a rail
    from USB, a battery or a connector."""
    nodes = design.nets.get(net, [])
    outputs = [n for n in nodes if _is_ic(n.ref) and _OUT_PIN.fullmatch(_norm(n.function))]
    # A buck's output is the inductor's far end: LX/SW -> L -> the rail.
    for coil in (n for n in nodes if is_inductor(design, n.ref)):
        for _, sw_net in _pins_of(design, coil.ref):
            outputs += [
                n
                for n in design.nets.get(sw_net, [])
                if sw_net != net and _is_ic(n.ref) and _SW_PIN.fullmatch(_norm(n.function))
            ]
    for node in outputs:
        names = (
            _norm(design.pin_name(f"{node.ref}.{pin}")) for pin, _ in _pins_of(design, node.ref)
        )
        if any(_IN_PIN.fullmatch(n) for n in names):
            return node.ref
    return None


def ramp(ref: str, reading: DatasheetReading) -> SimRamp:
    """How regulator ``ref``'s rail comes up, for plan 003's enable/reset
    RC run: its start-up time and output accuracy from the same datasheet
    facts the vendor-model run reads. No start-up time (or an implausible
    one, over 1 s) leaves ``t_s`` None and the run a step."""
    rate = ratings(reading.limits.get(ref, []))
    t_s = rate.startup if rate.startup is not None and 0 < rate.startup <= 1.0 else None
    cited = rate.accuracy_cited and rate.accuracy < 0.5
    return SimRamp(
        regulator=ref[:40],
        t_s=t_s,
        accuracy=rate.accuracy if cited else _DEFAULT_ACCURACY,
        accuracy_cited=cited,
    )


def supply_range(limits: Sequence[LimitFact], nominal: float) -> tuple[float, float] | None:
    """An IC's operating supply range (V) that contains ``nominal``, the
    widest when its facts give several (VDD, VDDA)."""
    best: tuple[float, float] | None = None
    for lim in limits:
        if not _SUPPLY.search(lim.parameter) or _NOT_SUPPLY.search(lim.parameter):
            continue
        lo, hi = scaled(lim.min, lim.unit, "V"), scaled(lim.max, lim.unit, "V")
        if lo is None or hi is None or lo <= 0 or not lo <= nominal <= hi:
            continue
        if best is None or hi - lo > best[1] - best[0]:
            best = (lo, hi)
    return best


# --- the circuit -------------------------------------------------------------


def _norm(pin: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", pin.upper())


def _head(ref: str) -> str:
    m = re.match(r"[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""


def _is_ic(ref: str) -> bool:
    return _IC_REF.fullmatch(ref) is not None


def _element(letter: str, ref: str) -> str:
    """A SPICE element name for a board part: its ref when that's already
    one ("R7"), else the ref made safe behind the letter."""
    if re.fullmatch(r"[A-Za-z0-9_]+", ref) and ref.upper().startswith(letter):
        return ref
    return f"{letter}_{re.sub(r'[^A-Za-z0-9_]', '_', ref)}"


def _pins_of(design: Design, ref: str) -> list[tuple[str, str]]:
    """A part's (pin number, net), in netlist order."""
    return [(n.pin, net) for net, nodes in design.nets.items() for n in nodes if n.ref == ref]


def _value(letter: str, value: str) -> float | None:
    if letter == "R":
        return parse_ohms(value)
    if letter == "C":
        return parse_farads(value)
    if letter == "L":
        return parse_henries(value)
    return None


def _model_pin(board: dict[str, str], pin: str) -> str:
    """A model pin's normalized name as the board names it: vendor models
    number a function's repeated pins ("VIN_0", "VIN_1") where the symbol
    repeats "VIN"."""
    name = _norm(pin)
    if name not in board and (m := re.fullmatch(r"(.+?)_\d+", pin)):
        base = _norm(m.group(1))
        if base in board:
            return base
    return name


def _nets_of(board: dict[str, str], names: Sequence[str], pattern: re.Pattern[str]) -> set[str]:
    """The board nets of the model pins whose names match ``pattern``."""
    return {board[p] for p in names if pattern.fullmatch(p) and p in board}


def _cut(
    design: Design, ref: str, m: ModelFiles, status: SpiceModelStatus, rate: Ratings
) -> _Cut | str | None:
    """The regulator's circuit, or why it can't be simulated; None when the
    model isn't a single-rail regulator's: no input and output pin, or input
    or output pins on more than one net (a driver's IN1/IN2, OUT1/OUT2)."""
    board: dict[str, str] = {}
    for pin, on in _pins_of(design, ref):
        board.setdefault(_norm(design.pin_name(f"{ref}.{pin}")), on)
    names = [_model_pin(board, p) for p in m.pins]
    pin_in = next((p for p in names if _IN_PIN.fullmatch(p)), None)
    pin_out = next((p for p in names if _OUT_PIN.fullmatch(p)), None)
    if pin_in is None or pin_out is None or pin_in not in board or pin_out not in board:
        logger.info("spice circuits: %s's model %s has no input and output pin", ref, m.subckt)
        return None
    if len(_nets_of(board, names, _IN_PIN)) > 1 or len(_nets_of(board, names, _OUT_PIN)) > 1:
        logger.info(
            "spice circuits: %s's model %s has inputs or outputs on several nets; not a regulator",
            ref,
            m.subckt,
        )
        return None
    net_in, net_out = board[pin_in], board[pin_out]
    vout = named_voltage(net_out)
    if vout is None:
        return f"its output net {net_out} doesn't name a voltage"
    vin = named_voltage(net_in) or rate.vin
    if vin is None:
        return (
            f"its input net {net_in} doesn't name a voltage and the datasheet gives no "
            "typical input"
        )
    if "set by the schematic" in status.reason:
        return "its model needs parameters the schematic sets"
    if rate.current is None:
        return "the datasheet facts give no rated output current"
    nodes: dict[str, str] = {net_in: "vin", net_out: "out"}
    pins_used: list[str] = []
    for p in names:
        net = board.get(p)
        if net is None:
            if _GROUND_PIN.fullmatch(p):
                pins_used.append("0")
                continue
            return f"its model's pin {p} has no pin of that name on {ref}"
        if is_ground(net):
            nodes[net] = "0"
        if net not in nodes:
            nodes[net] = f"n{sum(1 for v in nodes.values() if v.startswith('n')) + 1}"
        if _ENABLE_PIN.fullmatch(p) and nodes[net] not in ("vin", "out", "0"):
            others = sorted({n.ref for n in design.nets[net] if n.ref != ref and _is_ic(n.ref)})
            if others:
                return f"its {p} is driven by {', '.join(others)}"
        pins_used.append(nodes[net])
    for net in design.nets:
        if is_ground(net):
            nodes[net] = "0"
    lines = [f"X{re.sub(r'[^A-Za-z0-9_]', '_', ref)} {' '.join(pins_used)} {m.subckt}"]
    reset = None
    for part in design.parts.values():
        letter = _head(part.ref)
        if letter not in ("R", "C", "L", "D") or part.ref == ref:
            continue
        ends = _pins_of(design, part.ref)
        if len(ends) != 2 or not all(net in nodes for _, net in ends):
            continue
        a, b = (nodes[net] for _, net in ends)
        if a == b:
            continue
        name = _element(letter, part.ref)
        if letter == "D":
            k, an = _diode_ends(design, part.ref, ends, nodes)
            lines.append(f"{name} {an} {k} hx_d")
            continue
        value = _value(letter, part.value)
        if value is None:
            logger.info(
                "spice circuits: %s's value %r doesn't parse; left out", part.ref, part.value
            )
            continue
        lines.append(f"{name} {a} {b} {value:g}")
    rc = _reset_rc(design, ref, net_out, nodes)
    if rc is not None:
        reset, extra = rc
        lines += extra
    if any(ln.split()[0].upper().startswith("D") for ln in lines[1:]):
        lines.append(".model hx_d D")
    return _Cut(vin, vout, rate.current, nodes, lines, reset)


def _diode_ends(
    design: Design, ref: str, ends: list[tuple[str, str]], nodes: dict[str, str]
) -> tuple[str, str]:
    """(cathode, anode) nodes: by pin name (K, A), else KiCad's pin 1 = K."""
    named = {_norm(design.pin_name(f"{ref}.{pin}")): nodes[net] for pin, net in ends}
    if "K" in named and "A" in named:
        return named["K"], named["A"]
    by_pin = dict(ends)
    k = by_pin.get("1", ends[0][1])
    an = by_pin.get("2", ends[1][1])
    return nodes[k], nodes[an]


def _reset_rc(
    design: Design, ref: str, net_out: str, nodes: dict[str, str]
) -> tuple[str, list[str]] | None:
    """A resistor from the output to another IC's pin with a capacitor to
    ground: (that pin, the two deck lines, on node ``rst``)."""
    for net, members in design.nets.items():
        if net in nodes:
            continue
        ic_pins = [n for n in members if n.ref != ref and _is_ic(n.ref)]
        if not ic_pins:
            continue
        r = c = None
        for n in members:
            other = [x for x in _pins_of(design, n.ref) if x[1] != net]
            if len(_pins_of(design, n.ref)) != 2 or len(other) != 1:
                continue
            letter = _head(n.ref)
            if letter == "R" and other[0][1] == net_out:
                r = (n.ref, parse_ohms(design.parts[n.ref].value))
            elif letter == "C" and is_ground(other[0][1]):
                c = (n.ref, parse_farads(design.parts[n.ref].value))
        if r and c and r[1] and c[1]:
            pin = ic_pins[0]
            label = f"{pin.pin_ref}" + (f" ({pin.function})" if pin.function else "")
            return label, [
                f"{_element('R', r[0])} out rst {r[1]:g}",
                f"{_element('C', c[0])} rst 0 {c[1]:g}",
            ]
    return None


# --- decks -------------------------------------------------------------------


def _window(rate: Ratings) -> float:
    """The start-up window: the ramp, then the datasheet's start-up time
    with a margin, so it ends soon after the rail settles."""
    if rate.startup is None:
        return _WINDOW_NO_FACTS
    return min(_RAMP + 1.1 * rate.startup, _WINDOW_MAX)


def _deck(
    title: str, name: str, cut: _Cut, sources: list[str], stop: float, probes: list[str]
) -> str:
    comments = [f"* {node}: {one_line(net)}" for net, node in cut.nodes.items() if node != "0"]
    leaks = [
        f"Rleak_{node} {node} 0 1G"
        for node in sorted(
            {n for n in cut.nodes.values() if n not in ("0", "vin")}
            | ({"rst"} if cut.reset else set())
        )
    ]
    return "\n".join(
        [
            one_line(title),
            *comments,
            f'.include "{name}"',
            ".options klu reltol=0.003",
            *sources,
            *cut.lines,
            *leaks,
            ".control",
            f"save {' '.join(f'v({p})' for p in probes)}",
            f"tran {_STEP:g} {stop:g}",
            "linearize",
            f"wrdata out.txt {' '.join(f'v({p})' for p in probes)}",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )


def plan(
    design: Design,
    status: SpiceModelStatus,
    m: ModelFiles,
    reading: DatasheetReading,
    analyses: Sequence[SimAnalysis],
) -> list[Job] | Skip | None:
    """The decks for one regulator, or why it's skipped; None when the
    model isn't a regulator's. Pure; nothing runs yet."""
    ref = status.ref
    rate = ratings(reading.limits.get(ref, []))
    cut = _cut(design, ref, m, status, rate)
    title = f"{ref} ({status.value[:60]})"
    if cut is None:
        return None
    if isinstance(cut, str):
        return Skip(ref, title, cut)
    vout, vin, current = cut.vout, cut.vin, cut.current
    lo, hi = vout * (1 - rate.accuracy), vout * (1 + rate.accuracy)
    pct = f"±{rate.accuracy * 100:g} %" + ("" if rate.accuracy_cited else ", not cited")
    over, over_label, droop, droop_label = _supply_limits(design, ref, cut, reading)
    window = _window(rate)
    probes = ["out", "vin"] + (["rst"] if cut.reset else [])
    base = Limits(
        ref,
        vout,
        vin,
        lo,
        hi,
        pct,
        over,
        over_label,
        droop,
        droop_label,
        window,
        window,
        dropout=rate.dropout,
        reset=cut.reset,
    )
    jobs: list[Job] = []
    for analysis in analyses:
        if analysis == "startup":
            limits = base
            sources = [f"Vin vin 0 PWL(0 0 {_RAMP:g} {vin:g})", f"Rload out 0 {vout / current:g}"]
            what = "start-up"
        elif analysis == "load_step":
            t1 = window
            limits = replace(base, t_event=t1, stop=t1 + _LOAD_STEP_AFTER)
            sources = [
                f"Vin vin 0 PWL(0 0 {_RAMP:g} {vin:g})",
                f"Rload out 0 {vout / (_LIGHT * current):g}",
                f"Istep out 0 PWL(0 0 {t1:g} 0 {t1 + 1e-6:g} {(1 - _LIGHT) * current:g})",
            ]
            what = f"load step to {current * 1e3:g} mA"
        else:
            if rate.dropout is None:
                continue
            t1 = window
            vend = vout * (1 - rate.accuracy) * 0.9
            limits = replace(base, t_event=t1, stop=t1 + _SWEEP, vin_end=vend)
            sources = [
                f"Vin vin 0 PWL(0 0 {_RAMP:g} {vin:g} {t1:g} {vin:g} {t1 + _SWEEP:g} {vend:g})",
                f"Rload out 0 {vout / current:g}",
            ]
            what = "dropout"
        full = f"{title} {what}, vendor model"
        deck = _deck(full, m.name, cut, sources, limits.stop, probes)
        jobs.append(Job(ref, analysis, full, deck, limits, m.files))
    return jobs


def _supply_limits(
    design: Design, ref: str, cut: _Cut, reading: DatasheetReading
) -> tuple[float, str, float, str]:
    """(overshoot ceiling, label, droop floor, label): the tightest supply
    range of the ICs on the output, from their facts, else nominal ±10 %."""
    net_out = next(net for net, node in cut.nodes.items() if node == "out")
    ranges = []
    for other in sorted({n.ref for n in design.nets[net_out] if n.ref != ref and _is_ic(n.ref)}):
        got = supply_range(reading.limits.get(other, []), cut.vout)
        if got is not None:
            ranges.append((other, got))
    vout = cut.vout
    if not ranges:
        pct = f"{_DEFAULT_SUPPLY * 100:g} %"
        return (
            vout * (1 + _DEFAULT_SUPPLY),
            f"nominal +{pct}",
            vout * (1 - _DEFAULT_SUPPLY),
            f"nominal −{pct}",
        )
    top_ref, (_, top) = min(ranges, key=lambda r: r[1][1])
    bottom_ref, (bottom, _) = max(ranges, key=lambda r: r[1][0])
    return top, f"{top_ref} supply max", bottom, f"{bottom_ref} supply min"


# --- verdicts ----------------------------------------------------------------


Rows = list[tuple[float, ...]]


def parse_columns(text: str, n: int) -> Rows:
    """``wrdata`` rows of ``n`` vectors as (t, v1, …, vn): ngspice writes a
    time column before each vector's. Lines that aren't numbers are
    skipped. Raises ValueError with fewer than two rows."""
    rows = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 2 * n:
            continue
        try:
            rows.append((float(cols[0]), *(float(cols[2 * i + 1]) for i in range(n))))
        except ValueError:
            continue
    if len(rows) < 2:
        raise ValueError("no samples")
    return rows


def _crossing(rows: Rows, col: int, level: float, after: float = 0.0) -> float | None:
    """First time column ``col`` rises to ``level`` after ``after``."""
    prev = None
    for row in rows:
        if row[0] < after:
            continue
        if row[col] >= level:
            if prev is None or prev[col] >= level:
                return row[0]
            t0, v0, t1, v1 = prev[0], prev[col], row[0], row[col]
            return t0 + (level - v0) * (t1 - t0) / (v1 - v0)
        prev = row
    return None


def _mean(rows: Rows, col: int, start: float, end: float) -> float | None:
    vals = [r[col] for r in rows if start <= r[0] <= end]
    return sum(vals) / len(vals) if vals else None


def _final(rows: Rows, start: float) -> float:
    """The mean output from ``start`` to the end (the last sample if none)."""
    got = _mean(rows, 1, start, rows[-1][0])
    return rows[-1][1] if got is None else got


def _v(x: float) -> str:
    return f"{x:.3f} V"


def _t(seconds: float) -> str:
    return format_seconds(seconds)


def verdict(analysis: SimAnalysis, lim: Limits, rows: Rows) -> tuple[SimVerdict, str]:
    """Whether the waveform (t, v(out), v(vin)[, v(rst)]) stays within
    ``lim``, and a few sentences saying so with the numbers."""
    end = rows[-1][0]
    band = f"{_v(lim.lo)}–{_v(lim.hi)} ({lim.accuracy})"
    wrong: list[str] = []
    if analysis == "startup":
        final = _final(rows, 0.9 * end)
        peak = max(r[1] for r in rows)
        t_band = _crossing(rows, 1, lim.lo)
        if t_band is None:
            wrong.append(f"never reaches {_v(lim.lo)} in {_t(end)}")
        if not lim.lo <= final <= lim.hi:
            wrong.append(f"settles at {_v(final)}, outside {band}")
        if peak > lim.over:
            wrong.append(f"peaks at {_v(peak)}, above {_v(lim.over)} ({lim.over_label})")
        if lim.reset and len(rows[0]) > 3:
            t_rst = _crossing(rows, 3, RESET_RELEASE * lim.vout)
            if t_rst is not None and (t_band is None or t_rst < t_band):
                when = f"before the rail reaches {_v(lim.lo)}" + (
                    f" at {_t(t_band)}" if t_band is not None else ""
                )
                wrong.append(f"releases {lim.reset} at {_t(t_rst)}, {when}")
        facts = f"Reaches {_v(lim.lo)} at {_t(t_band)}" if t_band is not None else "Rises"
        said = (
            f"{facts}, settles at {_v(final)} and peaks at {_v(peak)}; "
            f"band {band}, ceiling {_v(lim.over)} ({lim.over_label})."
        )
    elif analysis == "load_step":
        t1 = lim.t_event
        before = _mean(rows, 1, 0.9 * t1, t1)
        after = [r for r in rows if r[0] >= t1]
        vmin = min((r[1] for r in after), default=rows[-1][1])
        final = _final(rows, end - 0.1 * (end - t1))
        out_of_band = [r[0] for r in after if not lim.lo <= r[1] <= lim.hi]
        if before is None or not lim.lo <= before <= lim.hi:
            got = "no samples" if before is None else _v(before)
            wrong.append(f"isn't in band before the step ({got})")
        if vmin < lim.droop:
            wrong.append(f"droops to {_v(vmin)}, below {_v(lim.droop)} ({lim.droop_label})")
        if not lim.lo <= final <= lim.hi:
            wrong.append(f"ends at {_v(final)}, outside {band}")
        recovery = (
            f"back in band {_t(out_of_band[-1] - t1)} after the step"
            if out_of_band and lim.lo <= final <= lim.hi
            else "never leaves the band"
            if not out_of_band
            else "doesn't recover"
        )
        said = (
            f"Droops to {_v(vmin)} at the step, {recovery}, ends at {_v(final)}; "
            f"band {band}, floor {_v(lim.droop)} ({lim.droop_label})."
        )
    else:
        t1 = lim.t_event
        drop = next((r for r in rows if r[0] >= t1 and r[1] < lim.lo), None)
        datasheet = (
            f" The datasheet's dropout puts it near {_v(lim.vout + lim.dropout)}."
            if lim.dropout is not None
            else ""
        )
        if drop is None:
            end_vin = lim.vin_end if lim.vin_end is not None else rows[-1][2]
            said = f"Stays in band ({band}) down to {_v(end_vin)} in.{datasheet}"
        else:
            headroom = lim.vin - drop[2]
            if headroom < _DROPOUT_HEADROOM * lim.vin:
                wrong.append(
                    f"leaves its band at {_v(drop[2])} in, only {_v(headroom)} below the "
                    f"nominal {_v(lim.vin)}"
                )
            said = (
                f"Leaves its band ({band}) at {_v(drop[2])} in, {_v(headroom)} below the "
                f"nominal {_v(lim.vin)}.{datasheet}"
            )
    if wrong:
        return "flagged", f"{lim.ref}'s output {'; '.join(wrong)}. {said}"
    return "passed", said


def _envelope(rows: Rows, points: int = _POINTS) -> Rows:
    """At most ``points`` samples that keep each stretch's min and max, so
    an overshoot or a droop survives the thinning."""
    if len(rows) <= points:
        return rows
    size = len(rows) / (points // 2)
    kept: Rows = []
    for i in range(points // 2):
        chunk = rows[int(i * size) : int((i + 1) * size)]
        if not chunk:
            continue
        lo = min(chunk, key=lambda r: r[1])
        hi = max(chunk, key=lambda r: r[1])
        kept += sorted({lo, hi}, key=lambda r: r[0])
    if kept[-1] != rows[-1]:
        kept.append(rows[-1])
    return kept


def _levels(analysis: SimAnalysis, lim: Limits) -> list[SimLevel]:
    levels = [
        SimLevel(v=round(lim.lo, 4), label=f"band low ({lim.accuracy})", danger=False),
        SimLevel(v=round(lim.hi, 4), label=f"band high ({lim.accuracy})", danger=False),
    ]
    if analysis == "startup":
        levels.append(SimLevel(v=round(lim.over, 4), label=lim.over_label, danger=True))
    elif analysis == "load_step":
        levels.append(SimLevel(v=round(lim.droop, 4), label=lim.droop_label, danger=True))
    return levels


def _short_deck(deck: str) -> str:
    return deck if len(deck) <= _DECK_CHARS else deck[: _DECK_CHARS - 12] + "\n* (cut)\n"


def result(sim_id: str, job: Job, run: Run) -> Simulation:
    """The Simulation for one finished (or stopped) run."""
    unit, scale = time_unit(job.limits.stop)
    got: SimVerdict
    rows: Rows = []
    if run.code is None:
        minutes = settings.NGSPICE_MODEL_TIMEOUT_S / 60
        got, text = "timed_out", f"ngspice was stopped after {minutes:g} min, before it finished."
    else:
        error = ""
        if run.code == 0 and run.data:
            try:
                rows = parse_columns(run.data, 3 if job.limits.reset else 2)
            except ValueError as e:
                error = f"its output was unreadable ({e})"
        if rows:
            got, text = verdict(job.analysis, job.limits, rows)
        else:
            error = error or ngspice_error(run.log) or f"ngspice exited with {run.code}"
            got, text = "failed", f"The run failed: {error}"
    thin = _envelope(rows) if rows else []
    return Simulation(
        id=sim_id,
        ref=job.ref,
        analysis=job.analysis,
        verdict=got,
        title=job.title[:200],
        deck=_short_deck(job.deck),
        t=[round(r[0] * scale, 3) for r in thin],
        t_unit=unit,
        v=[round(r[1], 4) for r in thin],
        levels=_levels(job.analysis, job.limits),
        text=text[:2000],
    )


def skipped(sim_id: str, skip: Skip) -> Simulation:
    """The Simulation for a regulator that isn't simulated, saying why."""
    return Simulation(
        id=sim_id,
        ref=skip.ref,
        verdict="skipped",
        title=f"{skip.title}, vendor model"[:200],
        deck="",
        t=[],
        t_unit="µs",
        v=[],
        levels=[],
        text=f"Not simulated: {skip.reason}."[:2000],
    )


# --- running -----------------------------------------------------------------


def _runnable(statuses: Iterable[SpiceModelStatus]) -> list[SpiceModelStatus]:
    return [s for s in statuses if s.loads and s.status in MODES and s.subckt]


async def simulate(
    statuses: Sequence[SpiceModelStatus],
    upload: dict[str, bytes],
    design: Design,
    reading: DatasheetReading,
    analyses: Sequence[SimAnalysis],
    runner: ModelRunner,
) -> AsyncIterator[Simulation]:
    """Plan each loading model's decks and run them (``_PARALLEL`` at once),
    yielding the skipped ones first, then each verdict as it finishes.
    Closing the iterator early cancels the runs still going."""
    if not analyses:
        return
    jobs: list[tuple[str, Job]] = []
    n = 0
    for s in _runnable(statuses):
        m = await asyncio.to_thread(model_files, s, upload)
        if not isinstance(m, ModelFiles):
            logger.info("spice circuits: %s's model isn't ready: %s", s.ref, m[1])
            continue
        got = plan(design, s, m, reading, analyses)
        if got is None:
            continue
        if isinstance(got, Skip):
            n += 1
            logger.info("spice circuits: %s skipped: %s", s.ref, got.reason)
            yield skipped(f"vm{n}", got)
            continue
        for job in got:
            n += 1
            jobs.append((f"vm{n}", job))
    gate = asyncio.Semaphore(_PARALLEL)

    async def one(sim_id: str, job: Job) -> Simulation | None:
        async with gate:
            run = await runner.transient(job.deck, job.files)
        if run is None:
            return None  # ngspice isn't installed
        sim = result(sim_id, job, run)
        logger.info("spice circuits: %s %s %s", job.ref, job.analysis, sim.verdict)
        return sim

    tasks = [asyncio.create_task(one(i, j)) for i, j in jobs]
    try:
        for done in asyncio.as_completed(tasks):
            if (sim := await done) is not None:
                yield sim
    finally:
        for t in tasks:
            t.cancel()

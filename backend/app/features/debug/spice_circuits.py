"""Simulates each regulator's circuit with its vendor SPICE model, and
checks the waveform against the datasheet limits (plan 004 step 6).

For each IC whose model loads (``spice_convert.check``), ``plan`` cuts its
circuit out of the netlist: the regulator's pin nets and the chain from
its source (``chain``, plan 006 step 2), with the passives (R, C, L, D)
whose both ends are on them or on ground. The chain follows the power
tree upstream through muxes, load switches and pass parts to one source:
the battery when one feeds it, else USB, else an input, else an upstream
regulator's rail, each an ideal source at the middle of its range. A mux
or load switch on it becomes an ideal switch, a fuse or ferrite a wire,
both labelled. The source ramps up at power-on, the ICs on the output are
replaced by a resistor drawing the rail's budget (plan 005 step 5; the
datasheet's rated output current without one), and a reset RC on the
output to another IC's pin is kept and probed. Each analysis the user
picked becomes one deck:

- ``startup``: the load switches on once the rail passes the downstream
  ICs' minimum supply; the rail settles within the output accuracy,
  doesn't overshoot the downstream ICs' maximum supply, and a reset RC
  releases only after the rail is in band;
- ``load_step``: from 10 % to the load once the rail is up; the
  droop stays above the downstream ICs' minimum supply and the rail
  recovers into band;
- ``dropout``: the input swept down until the output leaves its band;
  flagged when that happens within 10 % of the nominal input. Only for a
  regulator whose datasheet gives a dropout voltage.

Ammeters (zero-volt sources) sit on the source and on each switch-node pin
(LX, SW, a buck-boost's L1/L2; plan 006 step 1), and their peaks are
measured in every deck (MAX and MIN; the larger by magnitude): a burst
into or out of a switch-node pin above the datasheet's switch current
limit (2× the rated current without one, labelled not cited) is flagged,
whatever the voltages do. A PG pin on its own net is probed and its rise
reported. A model with a switch-node pin gets the switching solver
settings (gear, relaxed tolerances, ``uic``).

The limits come from the datasheet facts (``facts.DatasheetReading.limits``):
the regulator's rated current, accuracy (±5 % without one), start-up time,
dropout and typical input; the downstream ICs' supply range (nominal ±10 %
without one). A regulator that can't be set up this way (no rated current,
a model pin with no board pin, an enable driven by another IC) yields a
``skipped`` simulation that says why.

Start-up runs at corners (plan 006 step 4): the source's nominal, then
nominal from a current-limited bench supply (``BENCH_CURRENT_LIMIT_A``,
assumed), then the source's min and max when its range has two ends, with
ceramic capacitors and inductors at −20 % there. Each corner is its own
run, named in its title and setup sentence; load step and dropout run at
nominal only.

``simulate`` runs the decks (two at once; nominal, bench, min, max, each
as built before its fix) and yields each verdict as it finishes. A run
that times out or fails is ``timed_out`` or ``failed``, with ngspice's
error; it never fails the request. A run still queued when the request's
``SPICE_BUDGET_S`` is spent is ``skipped``.
"""

import asyncio
import math
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
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
    PowerPart,
    PowerTree,
    SimAnalysis,
    SimLevel,
    SimPeak,
    SimRamp,
    Simulation,
    SimVerdict,
    SpiceModelStatus,
    WiringFix,
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
# Switching models (a switch-node pin) need the hand-built study's settings
# (plan 006 step 1): gear integration and relaxed tolerances, or ngspice
# stops with "timestep too small" in the first switching cycles; ``interp``
# keeps the output on the print grid; ``uic`` skips the operating point,
# which the models' internal state rarely solves.
_SWITCHING_OPTIONS = ".options method=gear reltol=0.01 abstol=1e-9 vntol=1e-5 itl4=100 interp"
_LINEAR_OPTIONS = ".options klu reltol=0.003"
_UNCITED_SWITCH = 2.0  # without a switch current limit: this many times the rated current
_LOAD_HYSTERESIS = 0.02  # the start-up load's switch, as a share of the output voltage
# The chain's source, best first (plan 006 step 2); an upstream regulator's
# rail comes after these.
_SOURCE_RANK = {"battery": 0, "usb": 1, "input": 2}
# Start-up corners (plan 006 step 4), in the order they're run.
_CORNER_ORDER = {"nominal": 0, "bench": 1, "min": 2, "max": 3}
_DERATE = 0.8  # ceramic C (DC bias) and L at the min and max corners
# The bench supply's current limit is smooth: a resistor this size at low
# current, saturating at the limit (a tanh), so ngspice sees no corner.
_BENCH_R = 0.05
# A capacitor that isn't ceramic (no DC-bias derating): polarized,
# electrolytic or tantalum, by symbol or footprint.
_POLARIZED = re.compile(r"(^|[:_\s])CP($|[_:\s])|polari|elec|tantal", re.IGNORECASE)

# Pin names, normalized to uppercase letters and digits.
_IN_PIN = re.compile(r"P?V?IN\d*|VI")
_OUT_PIN = re.compile(r"V?OUT\d*|VO")
# A switch node: a buck's LX/SW, a buck-boost's L1/L2 (TPS63070).
_SW_PIN = re.compile(r"LX\d*|SW\d*|L\d")
_PG_PIN = re.compile(r"PG|PGOOD|POWERGOOD|PWRGD")
_ENABLE_PIN = re.compile(r"(EN|CE|SHDN|ON|ENABLE)\d*")
_GROUND_PIN = re.compile(r"(A|D|P|S)?GND\d*|VSS\d*|EP|E?PAD|THERMALPAD")
_IC_REF = re.compile(r"(U|IC)\d+", re.IGNORECASE)

# Facts, matched on LimitFact.parameter.
_CURRENT = re.compile(r"(output|load)\s+current|^I_?OUT\b|^IO\b", re.IGNORECASE)
_NOT_RATED = re.compile(r"sink|sourc|limit|short|leak|quiescent|shut|switch|protect", re.IGNORECASE)
_SWITCH_LIMIT = re.compile(
    r"(switch|inductor|peak|valley)\s+current\s+limit|current\s+limit.*switch|^I_?LIM",
    re.IGNORECASE,
)
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
    switch_limit: float | None = None  # switch current limit, A
    switch_page: int | None = None
    accuracy: float = _DEFAULT_ACCURACY  # output accuracy, as a fraction
    accuracy_cited: bool = False
    dropout: float | None = None  # V
    startup: float | None = None  # summed start-up times, s
    vin: float | None = None  # typical input, V


@dataclass(frozen=True)
class Switch:
    """A switch-node pin routed through an ammeter, ``Vam_<name>``."""

    name: str  # the ammeter's suffix, "l2"
    label: str  # "U5.9 (L2) on +3V3"


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
    probes: tuple[str, ...] = ("out", "vin")  # the wrdata columns, in order
    switches: tuple[Switch, ...] = ()  # the switch-node pins with an ammeter
    switch_limit: float = 0.0  # a burst into a switch pin above this is flagged
    switch_label: str = ""
    pg: str | None = None  # the power-good pin probed, "U5.11 (PG)"
    setup: str = ""  # the source, the parts between, and the load, said


@dataclass(frozen=True)
class Job:
    """One deck to run, with what to check its waveform against."""

    ref: str
    analysis: SimAnalysis
    title: str
    deck: str
    limits: Limits
    files: dict[str, bytes] = field(default_factory=dict, repr=False)
    nets: frozenset[str] = frozenset()  # the board nets the deck holds
    corner: str = "nominal"  # Corner.name
    fix_of: str | None = None  # a with-fix run: the as-built run it pairs with
    finding: str | None = None  # and the WiringFinding whose fix it applies


@dataclass(frozen=True)
class Fixed:
    """A wiring finding's fix applied to the board (plan 006 step 3): each
    chain it touches runs again on ``design``, beside the as-built run."""

    finding: str  # the WiringFinding's id, "wf1"
    fix: WiringFix
    design: Design  # the board with the fix (``roles.apply_fix``)
    tree: PowerTree | None  # its budgeted power tree


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
    current: float  # the load: the output rail's budget, else the rated output current
    nodes: dict[str, str]  # board net -> deck node
    lines: list[str]  # the regulator instance and the kept passives
    reset: str | None  # "U16.7 (NRST)" when a reset RC is kept (node ``rst``)
    switches: tuple[Switch, ...] = ()
    pg: tuple[str, str] | None = None  # (node, "U5.11 (PG)") when PG is on its own net
    src: str = "vin"  # the node the source drives: the chain's top net
    setup: str = ""  # the source, the chain's parts and the load, said
    source_net: str | None = None  # the chain's top net, for the title
    span: tuple[float, float] | None = None  # the source's range, when it has two ends


@dataclass(frozen=True)
class Chain:
    """The path from a source to a regulator's input (plan 006 step 2):
    the nets on it, source first, and the part between each two."""

    nets: tuple[str, ...]  # the source's net first, the regulator's input last
    parts: tuple[PowerPart, ...]  # parts[i] joins nets[i] to nets[i + 1]
    kind: str  # "battery", "usb", "input", or "rail" (an upstream regulator's)
    vin: float | None  # the source's nominal: the middle of its range
    lo: float | None = None  # its range, for the min and max corners
    hi: float | None = None


@dataclass(frozen=True)
class Corner:
    """Where a start-up run sits (plan 006 step 4): at the source's
    nominal, min or max, or at nominal from a current-limited bench
    supply. Min and max take the parts' tolerances too: ceramic C and L
    at ``derate`` of their value."""

    name: str  # "nominal", "bench", "min", "max"
    vin: float | None = None  # the source's voltage; None: its nominal
    derate: float = 1.0
    limit: float | None = None  # the bench supply's current limit, A


NOMINAL = Corner("nominal")


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
    switch_limit = None
    switch_page = None
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
        elif switch_limit is None and _SWITCH_LIMIT.search(p):
            # the highest the part lets through: max, else typ, else min
            got = next((b for b in (lim.max, lim.typ, lim.min) if b is not None), None)
            switch_limit = scaled(got, lim.unit, "A")
            switch_page = lim.page if switch_limit else None
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
        switch_limit=switch_limit if switch_limit and switch_limit > 0 else None,
        switch_page=switch_page if switch_limit and switch_limit > 0 else None,
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


def chain(tree: PowerTree, net: str) -> Chain | None:
    """The path from ``net`` (a regulator's input) up the power tree to its
    source, through the switches and pass parts that make it: the best
    source by ``_SOURCE_RANK`` (a battery first), then an upstream
    regulator's or charger's rail, the shortest path breaking ties. None
    when the tree doesn't have the net."""
    rails = {r.net: r for r in tree.rails}
    found: list[tuple[int, Chain]] = []

    def walk(nets: tuple[str, ...], parts: tuple[PowerPart, ...]) -> None:
        top = nets[0]
        rail = rails.get(top)
        if rail is None:
            return
        makers = [p for p in tree.parts if p.output == top]
        if (
            rail.source is not None
            or not makers
            or any(p.kind in ("regulator", "charger") for p in makers)
        ):
            kind = rail.source or "rail"
            mid = (
                (rail.min + rail.max) / 2 if rail.min is not None and rail.max is not None else None
            )
            found.append(
                (
                    _SOURCE_RANK.get(kind, len(_SOURCE_RANK)),
                    Chain(nets, parts, kind, mid, rail.min, rail.max),
                )
            )
            return
        for p in makers:
            for up in p.inputs:
                if up not in nets:
                    walk((up, *nets), (p, *parts))

    walk((net,), ())
    if not found:
        return None
    return min(found, key=lambda f: (f[0], len(f[1].nets)))[1]


def _is_wire(design: Design, part: PowerPart) -> bool:
    """Whether a chain's pass part goes in as an ideal wire: a fuse,
    ferrite, slide switch or 0 Ω part, anything but a diode or a resistor
    with a value the passives keep as it is."""
    letter = _head(part.ref)
    if letter == "D":
        return False
    if letter == "R":
        value = parse_ohms(part.value)
        return value is None or value <= 0
    return True


def _supply(corner: Corner) -> str:
    """What drives the chain's top net: "an ideal source", or the bench
    supply, said."""
    if corner.limit is None:
        return "an ideal source"
    return f"a {corner.limit:g} A current-limited supply (assumed)"


def _is_ceramic(design: Design, ref: str) -> bool:
    """Whether capacitor ``ref`` derates with DC bias: not polarized."""
    part = design.parts[ref]
    return not _POLARIZED.search(f"{part.lib_part} {part.footprint}")


def _source_said(tree: PowerTree, c: Chain, vin: float, corner: Corner = NOMINAL) -> str:
    """The chain's source, said: "the battery VBAT at 4.8 V (the review form's)"."""
    net = c.nets[0]
    rail = next(r for r in tree.rails if r.net == net)
    if corner.name in ("min", "max"):
        how = f" (its {corner.name})"
    elif c.kind == "battery" and tree.battery_override:
        how = " (the review form's)"
    elif rail.min is not None and rail.max is not None and rail.min != rail.max:
        how = f" (the middle of {_v(rail.min)}–{_v(rail.max)})"
    else:
        how = ""
    what = {
        "battery": "the battery",
        "usb": "USB",
        "input": "the input",
    }.get(c.kind, f"{', '.join(rail.fed_by)}'s output")
    return f"{what} {net} at {_v(vin)}{how}, as {_supply(corner)}"


def _cut(
    design: Design,
    ref: str,
    m: ModelFiles,
    status: SpiceModelStatus,
    rate: Ratings,
    tree: PowerTree | None = None,
    corner: Corner = NOMINAL,
) -> _Cut | str | None:
    """The regulator's circuit, or why it can't be simulated; None when the
    model isn't a single-rail regulator's: no input and output pin, or input
    or output pins on more than one net (a driver's IN1/IN2, OUT1/OUT2).
    ``corner`` sets the source's voltage and the C and L derating."""
    board: dict[str, str] = {}
    number: dict[str, str] = {}  # normalized pin name -> the board's pin number
    for pin, on in _pins_of(design, ref):
        board.setdefault(_norm(design.pin_name(f"{ref}.{pin}")), on)
        number.setdefault(_norm(design.pin_name(f"{ref}.{pin}")), pin)
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
    up = chain(tree, net_in) if tree is not None else None
    vin = up.vin if up is not None and up.vin is not None else None
    if vin is None:
        up = None  # a source with no voltage: the regulator alone, as before
        vin = named_voltage(net_in) or rate.vin
    if vin is None:
        return (
            f"its input net {net_in} doesn't name a voltage and the datasheet gives no "
            "typical input"
        )
    if corner.vin is not None:
        vin = corner.vin
    span = (
        (up.lo, up.hi)
        if up is not None and up.lo is not None and up.hi is not None and up.lo < up.hi
        else None
    )
    if "set by the schematic" in status.reason:
        return "its model needs parameters the schematic sets"
    budget = next((r.budget for r in tree.rails if r.net == net_out), None) if tree else None
    if budget is not None and budget.load_ma > 0:
        current = budget.load_ma / 1e3
        unknown = f", without {', '.join(budget.unknown)}" if budget.unknown else ""
        load_said = f"{net_out}'s budget, {current * 1e3:g} mA{unknown}"
    elif rate.current is not None:
        current = rate.current
        load_said = f"the rated {current * 1e3:g} mA, no budget for {net_out}"
    else:
        return "the datasheet facts give no rated output current and its rail has no budget"
    nodes: dict[str, str] = {net_in: "vin", net_out: "out"}
    chain_lines: list[str] = []
    via: list[str] = []
    handled: set[str] = set()
    if up is not None:
        for i, link in enumerate(up.nets[:-1]):
            nodes.setdefault(link, f"c{i}")
        for i, step in enumerate(up.parts):
            a, b = nodes[up.nets[i]], nodes[up.nets[i + 1]]
            said = f"{step.ref} ({step.value})" if step.value else step.ref
            if step.kind == "switch" or (step.kind == "pass" and _is_wire(design, step)):
                # Zero volts: the mux's or fuse's own drop isn't modelled,
                # and i(v_sw_<ref>) is the current through it.
                chain_lines.append(f"{_element('V', 'sw_' + step.ref)} {a} {b} 0")
                handled.add(step.ref)
                kind = "switch" if step.kind == "switch" else "wire"
                said += f" as an ideal {kind}"
            via.append(said)
    if up is not None and tree is not None:
        source_said = _source_said(tree, up, vin, corner)
    else:
        what = (
            f"an ideal {_v(vin)} source"
            if corner.limit is None
            else f"a {corner.limit:g} A current-limited {_v(vin)} supply (assumed)"
        )
        source_said = f"{what} on {net_in}" + (
            " (its name)" if named_voltage(net_in) else " (the datasheet's typical input)"
        )
    through = f" through {', '.join(via)}" if via else ""
    derated = f"; ceramic C and L −{(1 - corner.derate) * 100:g} %" if corner.derate != 1 else ""
    setup = f"From {source_said}{through}; load {load_said}{derated}."
    pins_used: list[str] = []
    ammeters: list[str] = []
    switches: list[Switch] = []
    pg = None
    for raw, p in zip(m.pins, names, strict=True):
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
        on_chain = up is not None and net in up.nets
        if _ENABLE_PIN.fullmatch(p) and nodes[net] not in ("vin", "out", "0") and not on_chain:
            others = sorted({n.ref for n in design.nets[net] if n.ref != ref and _is_ic(n.ref)})
            if others:
                return f"its {p} is driven by {', '.join(others)}"
        node = nodes[net]
        if _SW_PIN.fullmatch(p) and node != "0":
            # The pin through a zero-volt source: i(vam_<x>) is the current
            # from the board's net into the pin.
            name = _norm(raw).lower()
            while any(s.name == name for s in switches):
                name += "x"
            ammeters.append(f"Vam_{name} {node} sw_{name} 0")
            switches.append(Switch(name, f"{ref}.{number[p]} ({p}) on {net}"))
            node = f"sw_{name}"
        elif _PG_PIN.fullmatch(p) and node.startswith("n") and pg is None:
            pg = (node, f"{ref}.{number[p]} ({p})")
        pins_used.append(node)
    for net in design.nets:
        if is_ground(net):
            nodes[net] = "0"
    lines = [
        f"X{re.sub(r'[^A-Za-z0-9_]', '_', ref)} {' '.join(pins_used)} {m.subckt}",
        *ammeters,
        *chain_lines,
    ]
    reset = None
    for part in design.parts.values():
        letter = _head(part.ref)
        if letter not in ("R", "C", "L", "D") or part.ref == ref or part.ref in handled:
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
        if letter == "L" or (letter == "C" and _is_ceramic(design, part.ref)):
            value *= corner.derate
        lines.append(f"{name} {a} {b} {value:g}")
    rc = _reset_rc(design, ref, net_out, nodes, corner.derate)
    if rc is not None:
        reset, extra = rc
        lines += extra
    if any(ln.split()[0].upper().startswith("D") for ln in lines[1:]):
        lines.append(".model hx_d D")
    return _Cut(
        vin,
        vout,
        current,
        nodes,
        lines,
        reset,
        tuple(switches),
        pg,
        src=nodes[up.nets[0]] if up is not None else "vin",
        setup=setup,
        source_net=up.nets[0] if up is not None else None,
        span=span,
    )


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
    design: Design, ref: str, net_out: str, nodes: dict[str, str], derate: float = 1.0
) -> tuple[str, list[str]] | None:
    """A resistor from the output to another IC's pin with a capacitor to
    ground: (that pin, the two deck lines, on node ``rst``); a ceramic
    capacitor at ``derate`` of its value."""
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
            farads = c[1] * (derate if _is_ceramic(design, c[0]) else 1.0)
            pin = ic_pins[0]
            label = f"{pin.pin_ref}" + (f" ({pin.function})" if pin.function else "")
            return label, [
                f"{_element('R', r[0])} out rst {r[1]:g}",
                f"{_element('C', c[0])} rst 0 {farads:g}",
            ]
    return None


# --- decks -------------------------------------------------------------------


def _window(rate: Ratings) -> float:
    """The start-up window: the ramp, then the datasheet's start-up time
    with a margin, so it ends soon after the rail settles."""
    if rate.startup is None:
        return _WINDOW_NO_FACTS
    return min(_RAMP + 1.1 * rate.startup, _WINDOW_MAX)


def _source(wave: str, node: str = "vin", limit: float | None = None) -> list[str]:
    """The source, ``wave`` from 0 V, through the input ammeter onto
    ``node`` (the chain's top net): i(vam_in) is the current it gives.
    With ``limit``, a bench supply: the ideal source behind a smooth
    current limit (a behavioural current source, ``_BENCH_R`` below it)."""
    if limit is None:
        return [f"Vsrc src 0 PWL({wave})", f"Vam_in src {node} 0"]
    return [
        f"Vsrc sup 0 PWL({wave})",
        f"Bbench sup src I={limit:g}*tanh((v(sup)-v(src))/{limit * _BENCH_R:g})",
        f"Vam_in src {node} 0",
    ]


def _switched_load(ohms: float, on: float, vout: float) -> list[str]:
    """The start-up load: ``ohms`` from the output, switched on once it
    rises past ``on`` (the downstream ICs' minimum supply), as those ICs
    start drawing only once their supply is up. Hysteresis keeps it on
    through the ripple."""
    vh = _LOAD_HYSTERESIS * vout
    return [
        f"Rload out lsw {ohms:g}",
        "Sload lsw 0 out 0 hx_load",
        f".model hx_load sw vt={on - vh:g} vh={vh:g} ron=0.01 roff=1e9",
    ]


def _deck(
    title: str, name: str, cut: _Cut, sources: list[str], stop: float, probes: Sequence[str]
) -> str:
    """The deck: the cut, ``sources``, and a transient to ``stop`` writing
    ``probes`` to out.txt; each ammeter's peak goes to run.log as ``pk_<x>``."""
    comments = [f"* {node}: {one_line(net)}" for net, node in cut.nodes.items() if node != "0"]
    comments += [f"* vam_{s.name}: into {one_line(s.label)}" for s in cut.switches]
    leaks = [
        f"Rleak_{node} {node} 0 1G"
        for node in sorted(
            {n for n in cut.nodes.values() if n not in ("0", "vin", cut.src)}
            | ({"rst"} if cut.reset else set())
        )
    ]
    ammeters = ["in", *(s.name for s in cut.switches)]
    switching = bool(cut.switches)
    return "\n".join(
        [
            one_line(title),
            *comments,
            f'.include "{name}"',
            _SWITCHING_OPTIONS if switching else _LINEAR_OPTIONS,
            *sources,
            *cut.lines,
            *leaks,
            ".control",
            f"save {' '.join(f'v({p})' for p in probes)} "
            + " ".join(f"i(vam_{a})" for a in ammeters),
            f"tran {_STEP:g} {stop:g}" + (" uic" if switching else ""),
            *(f"meas tran pk_{a} MAX i(vam_{a})" for a in ammeters),
            *(f"meas tran nk_{a} MIN i(vam_{a})" for a in ammeters),
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
    tree: PowerTree | None = None,
) -> list[Job] | Skip | None:
    """The decks for one regulator, or why it's skipped; None when the
    model isn't a regulator's. ``tree`` (budgeted, ``budget.apply``) gives
    the chain from the source and the load; without it, the regulator
    alone from its input. Every analysis runs at the source's nominal;
    start-up also runs from the bench supply and at the source's min and
    max (``Corner``), in that order. Pure; nothing runs yet."""
    ref = status.ref
    rate = ratings(reading.limits.get(ref, []))
    cut = _cut(design, ref, m, status, rate, tree)
    title = f"{ref} ({status.value[:60]})"
    if cut is None:
        return None
    if isinstance(cut, str):
        return Skip(ref, title, cut)
    jobs = _jobs(ref, design, m, reading, analyses, rate, cut, title, NOMINAL)
    if "startup" not in analyses:
        return jobs
    corners = [Corner("bench", limit=settings.BENCH_CURRENT_LIMIT_A)]
    if cut.span is not None:
        corners += [
            Corner("min", cut.span[0], _DERATE),
            Corner("max", cut.span[1], _DERATE),
        ]
    for corner in corners:
        at = _cut(design, ref, m, status, rate, tree, corner)
        if not isinstance(at, _Cut):
            logger.info("spice circuits: %s at %s isn't planned: %s", ref, corner.name, at)
            continue
        jobs += _jobs(ref, design, m, reading, ["startup"], rate, at, title, corner)
    return jobs


def _jobs(
    ref: str,
    design: Design,
    m: ModelFiles,
    reading: DatasheetReading,
    analyses: Sequence[SimAnalysis],
    rate: Ratings,
    cut: _Cut,
    title: str,
    corner: Corner,
) -> list[Job]:
    """One regulator's decks at one corner, an analysis each."""
    vout, vin, current = cut.vout, cut.vin, cut.current
    lo, hi = vout * (1 - rate.accuracy), vout * (1 + rate.accuracy)
    pct = f"±{rate.accuracy * 100:g} %" + ("" if rate.accuracy_cited else ", not cited")
    over, over_label, droop, droop_label = _supply_limits(design, ref, cut, reading)
    window = _window(rate)
    probes = ("out", "vin", *(["rst"] if cut.reset else []), *([cut.pg[0]] if cut.pg else []))
    if rate.switch_limit is not None:
        page = f", p. {rate.switch_page}" if rate.switch_page else ""
        limit, limit_label = rate.switch_limit, f"the switch current limit{page}"
    else:
        rated = rate.current if rate.current is not None else current
        what = "rated" if rate.current is not None else "load's"
        limit = _UNCITED_SWITCH * rated
        limit_label = f"{_UNCITED_SWITCH:g}× the {what} {rated:g} A, not cited"
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
        probes=probes,
        switches=cut.switches,
        switch_limit=limit,
        switch_label=limit_label,
        pg=cut.pg[1] if cut.pg else None,
        setup=cut.setup,
    )
    src = cut.src
    jobs: list[Job] = []
    for analysis in analyses:
        if analysis == "startup":
            limits = base
            sources = [
                *_source(f"0 0 {_RAMP:g} {vin:g}", src, corner.limit),
                *_switched_load(vout / current, droop, vout),
            ]
            what = "start-up"
        elif analysis == "load_step":
            t1 = window
            limits = replace(base, t_event=t1, stop=t1 + _LOAD_STEP_AFTER)
            sources = [
                *_source(f"0 0 {_RAMP:g} {vin:g}", src),
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
                *_source(f"0 0 {_RAMP:g} {vin:g} {t1:g} {vin:g} {t1 + _SWEEP:g} {vend:g}", src),
                f"Rload out 0 {vout / current:g}",
            ]
            what = "dropout"
        start = f" from {cut.source_net}" if cut.source_net else ""
        full = f"{title} {what}{start}{_corner_said(corner, vin)}, vendor model"
        deck = _deck(full, m.name, cut, sources, limits.stop, probes)
        jobs.append(
            Job(
                ref,
                analysis,
                full,
                deck,
                limits,
                m.files,
                frozenset(cut.nodes),
                corner=corner.name,
            )
        )
    return jobs


def _corner_said(corner: Corner, vin: float) -> str:
    """The corner, for a run's title: "" at nominal."""
    if corner.limit is not None:
        return f" on a {corner.limit:g} A current-limited supply (assumed)"
    if corner.name in ("min", "max"):
        derated = f", C and L −{(1 - corner.derate) * 100:g} %" if corner.derate != 1 else ""
        return f" at {corner.name} {_v(vin)}{derated}"
    return ""


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
Peaks = dict[str, tuple[float, float]]  # ammeter -> (signed peak current, A; when, s)

# ``meas tran pk_l2 MAX i(vam_l2)`` prints "pk_l2 = 1.77e+00 at= 3.75e-04", and
# ``nk_l2 … MIN`` the most negative: a switch pin sources current as often as
# it sinks it (L1 into its inductor), so the peak is whichever is larger.
_PEAK = re.compile(r"^[pn]k_(\w+)\s*=\s*(\S+)\s+at=\s*(\S+)", re.MULTILINE | re.IGNORECASE)


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


def parse_peaks(log: str) -> Peaks:
    """Each ammeter's peak current and when, from the ``meas`` lines in
    ngspice's log: the larger of its MAX and MIN by magnitude, signed, so a
    negative peak is current out of the pin. An ammeter whose lines are
    missing, unreadable or not a number ("nan") is left out."""
    peaks: Peaks = {}
    for m in _PEAK.finditer(log):
        try:
            peak, at = float(m.group(2)), float(m.group(3))
        except ValueError:
            continue
        name = m.group(1).lower()
        bigger = name not in peaks or abs(peak) > abs(peaks[name][0])
        if math.isfinite(peak) and math.isfinite(at) and bigger:
            peaks[name] = (peak, at)
    return peaks


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


def _a(amps: float) -> str:
    return f"{amps:.2f} A"


def _through(peak: float, label: str) -> str:
    """The peak's direction and pin: "into U5.9 (L2) on +3V3", or "out of
    U5.9 (L2) on +3V3" for a negative peak."""
    return f"{'into' if peak >= 0 else 'out of'} {label}"


def _currents(lim: Limits, peaks: Peaks) -> tuple[list[str], str]:
    """(what's wrong, what's said) about the ammeters' peaks: a burst into
    or out of a switch-node pin above the switch current limit is flagged."""
    wrong = []
    said = []
    for sw in lim.switches:
        if sw.name not in peaks:
            continue
        peak, at = peaks[sw.name]
        said.append(f"{_through(peak, sw.label)} {_a(abs(peak))} at {_t(at)}")
        if abs(peak) > lim.switch_limit:
            wrong.append(
                f"draws {_a(abs(peak))} {_through(peak, sw.label)} at {_t(at)}, above "
                f"{_a(lim.switch_limit)} ({lim.switch_label})"
            )
    if "in" in peaks:
        said.append(f"from the input {_a(peaks['in'][0])} at {_t(peaks['in'][1])}")
    text = f" Peak currents: {'; '.join(said)}." if said else ""
    return wrong, text


def _pg(lim: Limits, rows: Rows) -> str:
    """When the power-good pin rises (half its peak), if it's probed."""
    if lim.pg is None:
        return ""
    col = len(lim.probes)  # PG is the last probe
    top = max(r[col] for r in rows)
    t_pg = _crossing(rows, col, top / 2) if top > 0.5 else None
    return f" {lim.pg} rises at {_t(t_pg)}." if t_pg is not None else f" {lim.pg} stays low."


def verdict(
    analysis: SimAnalysis, lim: Limits, rows: Rows, peaks: Peaks | None = None
) -> tuple[SimVerdict, str]:
    """Whether the waveform (t, then a column per ``lim.probes``: v(out),
    v(vin)[, v(rst)][, v(PG)]) and the ammeters' ``peaks`` stay within
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
        if lim.reset and "rst" in lim.probes:
            t_rst = _crossing(rows, lim.probes.index("rst") + 1, RESET_RELEASE * lim.vout)
            if t_rst is not None and (t_band is None or t_rst < t_band):
                when = f"before the rail reaches {_v(lim.lo)}" + (
                    f" at {_t(t_band)}" if t_band is not None else ""
                )
                wrong.append(f"releases {lim.reset} at {_t(t_rst)}, {when}")
        facts = f"Reaches {_v(lim.lo)} at {_t(t_band)}" if t_band is not None else "Rises"
        said = (
            f"{facts}, settles at {_v(final)} and peaks at {_v(peak)}; "
            f"band {band}, ceiling {_v(lim.over)} ({lim.over_label})."
        ) + _pg(lim, rows)
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
    burst, currents = _currents(lim, peaks or {})
    said += currents + (f" {lim.setup}" if lim.setup else "")
    if wrong or burst:
        parts = ([f"{lim.ref}'s output {'; '.join(wrong)}"] if wrong else []) + [
            f"{lim.ref} {b}" for b in burst
        ]
        return "flagged", f"{'. '.join(parts)}. {said}"
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
    peaks: list[SimPeak] = []
    if run.code is None:
        minutes = settings.NGSPICE_MODEL_TIMEOUT_S / 60
        got, text = "timed_out", f"ngspice was stopped after {minutes:g} min, before it finished."
    else:
        error = ""
        if run.code == 0 and run.data:
            try:
                rows = parse_columns(run.data, len(job.limits.probes))
            except ValueError as e:
                error = f"its output was unreadable ({e})"
        parsed = parse_peaks(run.log)
        peaks = _peaks(job.limits, parsed)
        if rows:
            got, text = verdict(job.analysis, job.limits, rows, parsed)
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
        peaks=peaks,
        fix_of=job.fix_of,
        finding=job.finding,
    )


def _peaks(lim: Limits, peaks: Peaks) -> list[SimPeak]:
    """The ammeters' peaks as the UI lists them: each switch pin's, then
    the input's. Named by ammeter, so a with-fix run's rows line up with
    the as-built run's."""
    got = [
        SimPeak(
            name=sw.name,
            label=_through(peaks[sw.name][0], sw.label)[:200],
            a=peaks[sw.name][0],
            t_s=peaks[sw.name][1],
        )
        for sw in lim.switches
        if sw.name in peaks
    ]
    if "in" in peaks:
        got.append(SimPeak(name="in", label="from the input", a=peaks["in"][0], t_s=peaks["in"][1]))
    return got[:10]


def over_budget(sim_id: str, job: Job, budget_s: float) -> Simulation:
    """The Simulation for a run still queued when the request's time
    budget ran out; it keeps its pairing."""
    return Simulation(
        id=sim_id,
        ref=job.ref,
        analysis=job.analysis,
        verdict="skipped",
        title=job.title[:200],
        deck="",
        t=[],
        t_unit="µs",
        v=[],
        levels=[],
        text=(
            f"Not simulated: over the time budget ({budget_s / 60:g} min for all of a "
            "request's vendor-model runs)."
        ),
        fix_of=job.fix_of,
        finding=job.finding,
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


def _touches(fix: WiringFix, job: Job) -> bool:
    """Whether ``fix`` changes ``job``'s circuit: a pin of its IC moves, or
    the net the pins leave is one of its deck's."""
    return fix.leaves in job.nets or any(p.split(".", 1)[0] == job.ref for p in fix.pins)


def _queued(jobs: list[tuple[str, Job]]) -> list[tuple[str, Job]]:
    """The runs in the order they go to ngspice: by corner (nominal, bench,
    min, max), each as-built run right before its fixes. Stable, so a
    corner's runs keep the order they were planned in."""
    ids = {i: k for k, (i, _) in enumerate(jobs)}

    def rank(item: tuple[str, Job]) -> tuple[int, int, int]:
        sim_id, job = item
        anchor = ids[job.fix_of] if job.fix_of else ids[sim_id]
        return (_CORNER_ORDER.get(job.corner, len(_CORNER_ORDER)), anchor, job.fix_of is not None)

    return sorted(jobs, key=rank)


def _runnable(statuses: Iterable[SpiceModelStatus]) -> list[SpiceModelStatus]:
    return [s for s in statuses if s.loads and s.status in MODES and s.subckt]


async def simulate(
    statuses: Sequence[SpiceModelStatus],
    upload: dict[str, bytes],
    design: Design,
    reading: DatasheetReading,
    analyses: Sequence[SimAnalysis],
    runner: ModelRunner,
    tree: PowerTree | None = None,
    fixes: Sequence[Fixed] = (),
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[Simulation]:
    """Plan each loading model's decks and run them (``_PARALLEL`` at once),
    yielding the skipped ones first, then each verdict as it finishes.
    ``tree`` is the budgeted power tree each chain and load come from.
    Each deck a fix touches (its IC's pin moves, or the net it leaves is in
    the deck) runs again with the fix, naming the as-built run it pairs
    with (plan 006 step 3), at the same corner. Runs are queued by corner
    (nominal, bench, min, max), each as built before its fix; one still
    queued after ``budget_s`` (default ``SPICE_BUDGET_S``) of ``clock`` is
    skipped. Closing the iterator early cancels the runs still going."""
    if not analyses:
        return
    jobs: list[tuple[str, Job]] = []
    n = 0
    for s in _runnable(statuses):
        m = await asyncio.to_thread(model_files, s, upload)
        if not isinstance(m, ModelFiles):
            logger.info("spice circuits: %s's model isn't ready: %s", s.ref, m[1])
            continue
        got = plan(design, s, m, reading, analyses, tree)
        if got is None:
            continue
        if isinstance(got, Skip):
            n += 1
            logger.info("spice circuits: %s skipped: %s", s.ref, got.reason)
            yield skipped(f"vm{n}", got)
            continue
        built: list[tuple[str, Job]] = []
        for job in got:
            n += 1
            built.append((f"vm{n}", job))
        jobs.extend(built)
        for f in fixes:
            touched = {(j.analysis, j.corner): i for i, j in built if _touches(f.fix, j)}
            if not touched:
                continue
            again = plan(
                f.design, s, m, reading, list(dict.fromkeys(a for a, _ in touched)), f.tree
            )
            if not isinstance(again, list):
                logger.info(
                    "spice circuits: %s with %s's fix isn't planned: %s", s.ref, f.finding, again
                )
                continue
            for job in again:
                if (of := touched.get((job.analysis, job.corner))) is None:
                    continue  # this corner's as-built run doesn't touch it
                n += 1
                title = f"{job.title}, with {f.finding}'s fix"
                jobs.append((f"vm{n}", replace(job, title=title, fix_of=of, finding=f.finding)))
    jobs = _queued(jobs)
    budget = settings.SPICE_BUDGET_S if budget_s is None else budget_s
    start = clock()
    gate = asyncio.Semaphore(_PARALLEL)

    async def one(sim_id: str, job: Job) -> Simulation | None:
        async with gate:
            if clock() - start >= budget:
                logger.info("spice circuits: %s %s over the time budget", job.ref, job.title)
                return over_budget(sim_id, job, budget)
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

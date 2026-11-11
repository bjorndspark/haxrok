"""Simulates RC nodes with ``ngspice -b``: a flagged I²C line, and the RC
delay on an enable or reset pin.

``plan`` picks the circuits worth simulating from the calculations;
``simulate`` runs them in parallel and yields each ``Simulation`` as it
finishes, so ``service.stream`` can send them while the model is still
working. The review doesn't see them; the user can ask about each one
(``service.ask``) and re-run it with another R and C (``rerun``, a what-if).

Each circuit is a resistor from an ideal rail and a capacitor to ground,
rising from 0 V at t=0: for I²C the pull-up and the estimated bus
capacitance, for an RC delay the pull-up and the pin's capacitor. An RC
delay whose rail comes from a regulator with a datasheet start-up time
(``SimRamp``, built by ``spice_circuits.ramp``) instead ramps the rail up
over that time, and its run gets a verdict: does the pin release only once
the rail is in band?
Any failure (ngspice missing, a non-zero exit, a timeout, unreadable output)
drops that simulation; it never fails the request.

This is the feature's I/O edge for ngspice. The service takes a
``SpiceRunner`` so tests can fake it; ``Ngspice`` is the real one.
"""

import asyncio
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.features.debug.calc import (
    I2cLine,
    RcLine,
    format_farads,
    format_ohms,
    i2c_line,
    rc_line,
)
from app.features.debug.design import Design
from app.features.debug.schemas import (
    Calculation,
    SimCircuit,
    SimLevel,
    SimRamp,
    Simulation,
    SimVerdict,
    TimeUnit,
)

logger = get_logger(__name__)

_TAUS = 5  # simulate to 5 RC: the line is within 1% of the rail
_STEPS = 200
_POINTS = 120  # what the UI draws
_PARALLEL = 4  # ngspice processes at once, per request
RESET_RELEASE = 0.7  # an enable/reset input reads high above 0.7 × its rail (plans 003, 004)


class SpiceRunner(Protocol):
    async def run(self, netlist: str) -> str | None:
        """The ``wrdata`` output of ``netlist`` (time and value columns), or
        None if the simulation couldn't run."""
        ...


@dataclass(frozen=True)
class Log:
    """What one ngspice run printed. ``code`` is its exit status, None when
    it was stopped for taking too long."""

    code: int | None
    text: str


class SpiceLoader(Protocol):
    async def load(self, netlist: str, files: dict[str, bytes]) -> Log | None:
        """Run ``netlist`` with ``files`` (relative path -> content, such as
        model files and a ``.spiceinit``) beside it; None if ngspice isn't
        installed."""
        ...


@dataclass(frozen=True)
class Run:
    """One vendor-model transient: the ``wrdata`` output (None when there
    is none) and the log, with ngspice's exit status (None when it was
    stopped for taking too long)."""

    code: int | None
    data: str | None
    log: str


class ModelRunner(Protocol):
    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        """Run ``netlist`` (it writes ``out.txt``) with ``files`` (model
        files and a ``.spiceinit``) beside it; None if ngspice isn't
        installed."""
        ...


class Ngspice:
    """Writes the deck to a temp dir and runs ``ngspice -b`` there."""

    def __init__(self, binary: str | None = None, timeout_s: float | None = None) -> None:
        self.binary = binary or settings.NGSPICE
        self.timeout_s = timeout_s or settings.NGSPICE_TIMEOUT_S

    async def run(self, netlist: str) -> str | None:
        with tempfile.TemporaryDirectory(prefix="haxrok-spice-") as tmp:
            work = Path(tmp)
            (work / "line.cir").write_text(netlist, "utf-8")
            log = await self._exec(work, "-b", "line.cir")
            data = work / "out.txt"
            if log is None or log.code is None:
                return None
            if log.code != 0 or not data.exists():
                logger.warning("ngspice failed (%s): %s", log.code, log.text.strip()[-1000:])
                return None
            return data.read_text("utf-8")

    async def load(self, netlist: str, files: dict[str, bytes]) -> Log | None:
        """Run ``netlist`` with ``files`` beside it, for a load check: the
        log ngspice wrote and printed. Raises ValueError for a file path
        that leaves the work dir."""
        with tempfile.TemporaryDirectory(prefix="haxrok-spice-") as tmp:
            work = Path(tmp)
            _write(work, files)
            (work / "load.cir").write_text(netlist, "utf-8")
            # ``-o`` writes the log to a file, so a run stopped by the
            # timeout still says how far it got.
            if (log := await self._exec(work, "-b", "-o", "load.log", "load.cir")) is None:
                return None
            kept = work / "load.log"
            text = kept.read_text("utf-8", errors="replace") if kept.exists() else ""
            return Log(log.code, text + log.text)

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        """Run a vendor-model deck with ``files`` beside it. Raises
        ValueError for a file path that leaves the work dir."""
        with tempfile.TemporaryDirectory(prefix="haxrok-spice-") as tmp:
            work = Path(tmp)
            _write(work, files)
            (work / "circuit.cir").write_text(netlist, "utf-8")
            if (log := await self._exec(work, "-b", "-o", "run.log", "circuit.cir")) is None:
                return None
            kept, data = work / "run.log", work / "out.txt"
            text = kept.read_text("utf-8", errors="replace") if kept.exists() else ""
            out = data.read_text("utf-8") if data.exists() else None
            return Run(log.code, out, text + log.text)

    async def version(self) -> str | None:
        """ngspice's version banner (``ngspice -v``), for the run cache's
        key; None if it isn't installed or doesn't say."""
        with tempfile.TemporaryDirectory(prefix="haxrok-spice-") as tmp:
            log = await self._exec(Path(tmp), "-v")
        if log is None or log.code != 0 or not log.text.strip():
            return None
        return log.text.strip()

    async def _exec(self, work: Path, *args: str) -> Log | None:
        """Run ngspice in ``work``: its exit status (None after the timeout)
        and stdout and stderr together; None if it isn't installed."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                *args,
                cwd=work,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            logger.info("ngspice isn't installed; skipping the simulation")
            return None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("ngspice took longer than %.0f s", self.timeout_s)
            return Log(None, "")
        except asyncio.CancelledError:
            # The client went away mid-run: don't leave ngspice behind.
            proc.kill()
            raise
        return Log(proc.returncode, out.decode("utf-8", errors="replace"))


def _write(work: Path, files: dict[str, bytes]) -> None:
    """Put ``files`` (relative path -> content) in ``work``; ValueError for
    a path that leaves it."""
    for name, data in files.items():
        path = (work / name).resolve()
        if not path.is_relative_to(work.resolve()):
            raise ValueError(f"{name!r} is outside the work dir")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _deck(title: str, v: float, r: float, c: float, node: str, ramp_s: float | None = None) -> str:
    """The transient deck: rail → resistor → node, capacitor to ground,
    starting at 0 V and run to 5 RC. With ``ramp_s`` the rail rises
    linearly from 0 V over that time instead of switching on at t = 0, and
    the run lasts that much longer."""
    stop = (ramp_s or 0.0) + _TAUS * r * c
    source = f"PWL(0 0 {ramp_s:g} {v:g})" if ramp_s else f"DC {v:g}"
    return "\n".join(
        [
            one_line(title),
            f"Vpull rail 0 {source}",
            f"Rpull rail {node} {r:g}",
            f"Cbus {node} 0 {c:g} IC=0",
            ".control",
            f"tran {stop / _STEPS:g} {stop:g} uic",
            f"wrdata out.txt v({node})",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )


def one_line(text: str) -> str:
    """``text`` as one line of printable text, for a deck's title or a
    comment. Such text carries net names from the uploaded netlist: a line
    break in it would start a new deck line, and a ``.control`` block can
    run shell commands."""
    return "".join(ch if ch.isprintable() else " " for ch in text)


def netlist(line: I2cLine) -> str:
    """An I²C line released from low (the moment the driver lets go)."""
    return _deck(f"I2C {line.net} released from low", line.v_pull, line.r_ohm, line.c_farad, "sda")


def _ramp_s(ramp: SimRamp | None) -> float | None:
    return ramp.t_s if ramp is not None else None


def rc_netlist(line: RcLine, ramp: SimRamp | None = None) -> str:
    """An enable or reset pin rising from 0 V as its rail comes up: a step
    at t = 0, or a linear ramp over the regulator's start-up time."""
    title = f"RC on {line.net}"
    if (t := _ramp_s(ramp)) is not None:
        title += f", rail ramping up over {_seconds(t)}"
    return _deck(title, line.v_rail, line.r_ohm, line.c_farad, "pin", t)


def parse(text: str) -> list[tuple[float, float]]:
    """``wrdata`` rows (time, value) as floats; raises ValueError if empty."""
    rows = []
    for row in text.splitlines():
        cols = row.split()
        if len(cols) >= 2:
            rows.append((float(cols[0]), float(cols[1])))
    if len(rows) < 2:
        raise ValueError("no samples")
    return rows


def _crossing(rows: list[tuple[float, float]], level: float) -> float | None:
    """First time the rising waveform reaches ``level``, interpolated."""
    for (t0, v0), (t1, v1) in zip(rows, rows[1:], strict=False):
        if v0 < level <= v1:
            return t0 + (level - v0) * (t1 - t0) / (v1 - v0)
    return None


def _thin(rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    step = max(1, len(rows) // _POINTS)
    return rows[::step] if rows[-1] in rows[::step] else [*rows[::step], rows[-1]]


_UNITS: tuple[tuple[TimeUnit, float], ...] = (("ns", 1e9), ("µs", 1e6), ("ms", 1e3))


def time_unit(stop: float) -> tuple[TimeUnit, float]:
    """The smallest time unit that keeps the axis under 10,000 of it: an
    I²C rise stays in ns, an RC delay of tens of ms goes to ms."""
    for name, scale in _UNITS:
        if stop * scale <= 10_000:
            return name, scale
    return _UNITS[-1]


def _times(rows: list[tuple[float, float]], scale: float) -> list[float]:
    return [round(t * scale, 3) for t, _ in rows]


@dataclass(frozen=True)
class Job:
    """One circuit to simulate: the deck and what it came from."""

    id: str
    calc_id: str
    title: str
    deck: str
    line: I2cLine | RcLine
    ramp: SimRamp | None = None


def with_ramp(job: Job, ramp: SimRamp) -> Job:
    """``job`` (an RC delay) driven from its regulator's rail: the deck
    ramps when ``ramp`` has a start-up time, else it stays a step."""
    if not isinstance(job.line, RcLine):
        return job
    title = job.title
    if ramp.t_s is not None:
        title = f"{job.line.net} rising as {ramp.regulator}'s rail ramps up"
    return replace(job, title=title, deck=rc_netlist(job.line, ramp), ramp=ramp)


def simulation(
    job: Job, line: I2cLine, rows: list[tuple[float, float]], estimated: bool = True
) -> Simulation:
    """The I²C waveform and the caption the UI shows under it. ``estimated``
    is False once the user typed the bus capacitance (a what-if)."""
    rows = _thin(rows)
    unit, scale = time_unit(rows[-1][0])
    lo, hi = _crossing(rows, 0.3 * line.v_pull), _crossing(rows, 0.7 * line.v_pull)
    over = _crossing(rows, line.v_supply + 0.3)
    bus = (
        f"~{format_farads(line.c_farad)} of bus (estimated: 10 pF per device pin + 10 pF trace)"
        if estimated
        else f"{format_farads(line.c_farad)} of bus"
    )
    text = f"ngspice: {format_ohms(line.r_ohm)} to {line.v_pull:g} V into {bus}."
    if lo is not None and hi is not None:
        text += f" Rise time 30→70% is {(hi - lo) * scale:.3g} {unit}."
    if over is not None:
        text += (
            f" The line passes the IC's {line.v_supply:g} V supply + 0.3 V after "
            f"{over * scale:.3g} {unit} and settles at {line.v_pull:g} V."
        )
    limit = round(line.v_supply + 0.3, 1)
    return Simulation(
        id=job.id,
        calc_id=job.calc_id,
        title=job.title,
        deck=job.deck,
        t=_times(rows, scale),
        t_unit=unit,
        v=[round(v, 4) for _, v in rows],
        levels=[
            SimLevel(v=line.v_pull, label=f"rail {line.v_pull:g} V", danger=False),
            SimLevel(v=limit, label=f"VDD+0.3 {limit:g} V", danger=True),
        ],
        text=text,
        circuit=circuit_of(line),
    )


def rc_simulation(
    job: Job, line: RcLine, rows: list[tuple[float, float]], ramp: SimRamp | None = None
) -> Simulation:
    """The enable/reset pin's rise: with the 63% point at one time constant
    after a step, or checked against the rail's ramp (``_ramped``)."""
    if (t_ramp := _ramp_s(ramp)) is not None and ramp is not None:
        return _ramped(job, line, rows, ramp, t_ramp)
    thin = _thin(rows)
    unit, scale = time_unit(thin[-1][0])
    tau = line.r_ohm * line.c_farad
    at_63 = round(0.632 * line.v_rail, 2)
    reached = _crossing(thin, at_63)
    text = (
        f"ngspice: {format_ohms(line.r_ohm)} from {line.v_rail:g} V into "
        f"{format_farads(line.c_farad)}, the rail switched on at t = 0."
    )
    if ramp is not None:
        text += (
            f" {ramp.regulator}'s datasheet facts give no start-up time, so the rail isn't "
            "ramped and the pin isn't checked against it."
        )
    if reached is not None:
        text += (
            f" The pin reaches 63% ({at_63:g} V) after {reached * scale:.3g} {unit} "
            f"(R×C = {tau * scale:.3g} {unit}). The IC's enable threshold is in its datasheet."
        )
    return Simulation(
        id=job.id,
        calc_id=job.calc_id,
        title=job.title,
        deck=job.deck,
        t=_times(thin, scale),
        t_unit=unit,
        v=[round(v, 4) for _, v in thin],
        levels=[
            SimLevel(v=line.v_rail, label=f"rail {line.v_rail:g} V", danger=False),
            SimLevel(v=at_63, label=f"63% {at_63:g} V", danger=False),
        ],
        text=text,
        circuit=circuit_of(line, ramp),
    )


def _accuracy(ramp: SimRamp) -> str:
    pct = f"±{ramp.accuracy * 100:g} %"
    return pct if ramp.accuracy_cited else f"{pct}, not cited: assumed"


def _ramped(
    job: Job, line: RcLine, rows: list[tuple[float, float]], ramp: SimRamp, t_ramp: float
) -> Simulation:
    """The pin against a rail that rises linearly over ``t_ramp``: flagged
    when it releases (``RESET_RELEASE`` × the rail) before the rail is in
    its band. The rail is an ideal ramp, so it reaches the band's low edge
    at (1 − accuracy) × ``t_ramp`` exactly."""
    released = _crossing(rows, RESET_RELEASE * line.v_rail)
    band_lo = (1 - ramp.accuracy) * line.v_rail
    in_band = (1 - ramp.accuracy) * t_ramp
    early = released is not None and released < in_band
    verdict: SimVerdict = "flagged" if early else "passed"
    thin = _thin(rows)
    unit, scale = time_unit(thin[-1][0])
    text = (
        f"ngspice: {format_ohms(line.r_ohm)} into {format_farads(line.c_farad)} from a rail "
        f"that ramps from 0 to {line.v_rail:g} V over {_seconds(t_ramp)} "
        f"({ramp.regulator}'s start-up time)."
    )
    release = f"{RESET_RELEASE:g} × {line.v_rail:g} = {RESET_RELEASE * line.v_rail:.3g} V"
    band = f"≥ {band_lo:.3g} V, {_accuracy(ramp)}"
    if released is None:
        text += f" The pin doesn't reach its release level ({release}) in the run."
    else:
        text += f" The pin releases ({release}) at {_seconds(released)}"
        text += f"; the rail is in band ({band}) at {_seconds(in_band)}."
        text += (
            " The pin releases before the rail is in band: the IC may start on a low supply."
            if early
            else " The pin releases after the rail is in band."
        )
    return Simulation(
        id=job.id,
        calc_id=job.calc_id,
        verdict=verdict,
        title=job.title,
        deck=job.deck,
        t=_times(thin, scale),
        t_unit=unit,
        v=[round(v, 4) for _, v in thin],
        levels=[
            SimLevel(v=line.v_rail, label=f"rail {line.v_rail:g} V", danger=False),
            SimLevel(v=round(band_lo, 3), label=f"band {band_lo:.3g} V", danger=False),
            SimLevel(
                v=round(RESET_RELEASE * line.v_rail, 3),
                label=f"release {RESET_RELEASE * line.v_rail:.3g} V",
                danger=False,
            ),
        ],
        text=text,
        circuit=circuit_of(line, ramp),
    )


def circuit_of(line: I2cLine | RcLine, ramp: SimRamp | None = None) -> SimCircuit:
    """What a what-if needs to rebuild ``line``'s deck (and ``ramp``, an RC
    delay's rail), carried on the simulation to the browser and back."""
    if isinstance(line, I2cLine):
        return SimCircuit(
            kind="i2c",
            net=line.net,
            r_ohm=line.r_ohm,
            c_farad=line.c_farad,
            v_rail=line.v_pull,
            v_supply=line.v_supply,
            r_ref=line.r_ref,
        )
    return SimCircuit(
        kind="rc_delay",
        net=line.net,
        r_ohm=line.r_ohm,
        c_farad=line.c_farad,
        v_rail=line.v_rail,
        r_ref=line.r_ref,
        c_ref=line.c_ref,
        ramp=ramp,
    )


def line_of(circuit: SimCircuit, r_ohm: float, c_farad: float) -> I2cLine | RcLine:
    """``circuit`` with another R and C. The rail stays as it was."""
    if circuit.kind == "i2c":
        supply = circuit.v_supply if circuit.v_supply is not None else circuit.v_rail
        return I2cLine(circuit.net, r_ohm, circuit.v_rail, supply, c_farad, circuit.r_ref)
    return RcLine(circuit.net, r_ohm, circuit.v_rail, c_farad, circuit.r_ref, circuit.c_ref)


def _metric(
    line: I2cLine | RcLine, rows: list[tuple[float, float]], ramp: SimRamp | None = None
) -> float | None:
    """The number a what-if compares, in seconds: an I²C line's 30→70% rise
    time, an RC delay's time to 63%, or its release time on a ramped rail."""
    if isinstance(line, I2cLine):
        lo, hi = _crossing(rows, 0.3 * line.v_pull), _crossing(rows, 0.7 * line.v_pull)
        return None if lo is None or hi is None else hi - lo
    if _ramp_s(ramp) is not None:
        return _crossing(rows, RESET_RELEASE * line.v_rail)
    return _crossing(rows, round(0.632 * line.v_rail, 2))


def _seconds(seconds: float) -> str:
    """A time in the unit that keeps it under 1,000: 119 ns, 4.7 ms."""
    for name, scale in (("ns", 1e9), ("µs", 1e6), ("ms", 1e3)):
        if seconds * scale < 1000:
            return f"{seconds * scale:.3g} {name}"
    return f"{seconds:.3g} s"


def _change(ref: str, old: str, new: str) -> str:
    return f"{ref} {old} → {new}" if old != new else f"{ref} {new} (unchanged)"


async def rerun(
    sim: Simulation, r_ohm: float, c_farad: float, runner: SpiceRunner
) -> Simulation | None:
    """``sim`` re-run with another R and C (a what-if): the new waveform,
    with a caption that compares it to ``sim``. None if ``sim`` isn't an RC
    run or ngspice failed."""
    if sim.circuit is None or not sim.t:
        return None
    ramp = sim.circuit.ramp
    old = line_of(sim.circuit, sim.circuit.r_ohm, sim.circuit.c_farad)
    line = line_of(sim.circuit, r_ohm, c_farad)
    deck = netlist(line) if isinstance(line, I2cLine) else rc_netlist(line, ramp)
    if not (rows := await _run(runner, deck, line.net)):
        return None
    job = Job(sim.id, sim.calc_id or "", f"{sim.title}, what-if", deck, line, ramp)
    if isinstance(line, I2cLine):
        new = simulation(job, line, rows, estimated=c_farad == sim.circuit.c_farad)
        what, c_name = "Rise time 30→70%", "bus C"
    else:
        new = rc_simulation(job, line, rows, ramp)
        what = "Release" if _ramp_s(ramp) is not None else "Time to 63%"
        c_name = sim.circuit.c_ref or "C"
    changes = [
        _change(sim.circuit.r_ref or "R", format_ohms(old.r_ohm), format_ohms(r_ohm)),
        _change(c_name, format_farads(old.c_farad), format_farads(c_farad)),
    ]
    text = f"What-if: {', '.join(changes)}."
    scale = dict(_UNITS)[sim.t_unit]
    before = _metric(old, [(t / scale, v) for t, v in zip(sim.t, sim.v, strict=False)], ramp)
    after = _metric(line, _thin(rows), ramp)
    if before is not None and after is not None:
        text += f" {what}: {_seconds(before)} → {_seconds(after)}."
    return new.model_copy(update={"calc_id": sim.calc_id, "text": f"{text} {new.text}"})


async def _run(runner: SpiceRunner, deck: str, net: str) -> list[tuple[float, float]] | None:
    if (text := await runner.run(deck)) is None:
        return None
    try:
        return parse(text)
    except ValueError as e:
        logger.warning("ngspice output for %s unreadable: %s", net, e)
        return None


def plan(calcs: list[Calculation], design: Design) -> list[Job]:
    """The circuits worth simulating: each flagged I²C line with a pull-up,
    and each RC delay on a named rail. Pure; nothing runs yet."""
    jobs: list[Job] = []
    for c in calcs:
        line: I2cLine | RcLine | None
        if (line := i2c_line(design, c)) is not None:
            title, deck = f"I²C {line.net} rising after release", netlist(line)
        elif (line := rc_line(design, c)) is not None:
            title, deck = f"{line.net} rising through its RC delay", rc_netlist(line)
        else:
            continue
        jobs.append(Job(f"sim{len(jobs) + 1}", c.id, title, deck, line))
    return jobs


async def _one(runner: SpiceRunner, job: Job, gate: asyncio.Semaphore) -> Simulation | None:
    async with gate:
        rows = await _run(runner, job.deck, job.line.net)
    if not rows:
        return None
    if isinstance(job.line, I2cLine):
        return simulation(job, job.line, rows)
    return rc_simulation(job, job.line, rows, job.ramp)


async def simulate(jobs: list[Job], runner: SpiceRunner) -> AsyncIterator[Simulation]:
    """Run ``jobs`` in parallel (at most ``_PARALLEL`` at once) and yield
    each simulation as it finishes; failed ones are skipped. Closing the
    iterator early cancels the runs still going."""
    gate = asyncio.Semaphore(_PARALLEL)
    tasks = [asyncio.create_task(_one(runner, job, gate)) for job in jobs]
    try:
        for done in asyncio.as_completed(tasks):
            if (sim := await done) is not None:
                yield sim
    finally:
        for t in tasks:
            t.cancel()

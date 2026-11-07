"""Simulates RC nodes with ``ngspice -b``: a flagged I²C line, and the RC
delay on an enable or reset pin.

``plan`` picks the circuits worth simulating from the calculations;
``simulate`` runs them in parallel and yields each ``Simulation`` as it
finishes, so ``service.stream`` can send them while the model is still
working. The review doesn't see them; the user can ask about each one
(``service.ask``).

Each circuit is a resistor from an ideal rail and a capacitor to ground,
rising from 0 V at t=0: for I²C the pull-up and the estimated bus
capacitance, for an RC delay the pull-up and the pin's capacitor.
Any failure (ngspice missing, a non-zero exit, a timeout, unreadable output)
drops that simulation; it never fails the request.

This is the feature's I/O edge for ngspice. The service takes a
``SpiceRunner`` so tests can fake it; ``Ngspice`` is the real one.
"""

import asyncio
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from app.features.debug.schemas import Calculation, SimLevel, Simulation, TimeUnit

logger = get_logger(__name__)

_TAUS = 5  # simulate to 5 RC: the line is within 1% of the rail
_STEPS = 200
_POINTS = 120  # what the UI draws
_PARALLEL = 4  # ngspice processes at once, per request


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
            for name, data in files.items():
                path = (work / name).resolve()
                if not path.is_relative_to(work.resolve()):
                    raise ValueError(f"{name!r} is outside the work dir")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (work / "load.cir").write_text(netlist, "utf-8")
            # ``-o`` writes the log to a file, so a run stopped by the
            # timeout still says how far it got.
            if (log := await self._exec(work, "-b", "-o", "load.log", "load.cir")) is None:
                return None
            kept = work / "load.log"
            text = kept.read_text("utf-8", errors="replace") if kept.exists() else ""
            return Log(log.code, text + log.text)

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


def _deck(title: str, v: float, r: float, c: float, node: str) -> str:
    """The transient deck: rail → resistor → node, capacitor to ground,
    starting at 0 V and run to 5 RC."""
    stop = _TAUS * r * c
    # The title carries a net name from the uploaded netlist. A line break in
    # it would start a new deck line, and a ``.control`` block can run shell
    # commands, so keep the title to one line of printable text.
    safe_title = "".join(ch if ch.isprintable() else " " for ch in title)
    return "\n".join(
        [
            safe_title,
            f"Vpull rail 0 DC {v:g}",
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


def netlist(line: I2cLine) -> str:
    """An I²C line released from low (the moment the driver lets go)."""
    return _deck(f"I2C {line.net} released from low", line.v_pull, line.r_ohm, line.c_farad, "sda")


def rc_netlist(line: RcLine) -> str:
    """An enable or reset pin rising from 0 V as its rail comes up."""
    return _deck(f"RC on {line.net}", line.v_rail, line.r_ohm, line.c_farad, "pin")


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


def _unit(stop: float) -> tuple[TimeUnit, float]:
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


def simulation(job: Job, line: I2cLine, rows: list[tuple[float, float]]) -> Simulation:
    """The I²C waveform and the caption the UI shows under it."""
    rows = _thin(rows)
    unit, scale = _unit(rows[-1][0])
    lo, hi = _crossing(rows, 0.3 * line.v_pull), _crossing(rows, 0.7 * line.v_pull)
    over = _crossing(rows, line.v_supply + 0.3)
    text = (
        f"ngspice: {format_ohms(line.r_ohm)} to {line.v_pull:g} V into "
        f"~{format_farads(line.c_farad)} of bus (estimated: 10 pF per device pin + 10 pF trace)."
    )
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
    )


def rc_simulation(job: Job, line: RcLine, rows: list[tuple[float, float]]) -> Simulation:
    """The enable/reset pin's rise, with the 63% point at one time constant."""
    rows = _thin(rows)
    unit, scale = _unit(rows[-1][0])
    tau = line.r_ohm * line.c_farad
    at_63 = round(0.632 * line.v_rail, 2)
    reached = _crossing(rows, at_63)
    text = (
        f"ngspice: {format_ohms(line.r_ohm)} from {line.v_rail:g} V into "
        f"{format_farads(line.c_farad)}, the rail switched on at t = 0."
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
        t=_times(rows, scale),
        t_unit=unit,
        v=[round(v, 4) for _, v in rows],
        levels=[
            SimLevel(v=line.v_rail, label=f"rail {line.v_rail:g} V", danger=False),
            SimLevel(v=at_63, label=f"63% {at_63:g} V", danger=False),
        ],
        text=text,
    )


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
    return rc_simulation(job, job.line, rows)


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

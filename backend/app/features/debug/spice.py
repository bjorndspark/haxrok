"""Simulates a flagged I²C line with ``ngspice -b``, for the UI to draw.

Demo polish, not detection: it only re-draws what ``calc.py`` already
flagged (a pull-up rail above an IC's supply). The circuit is the pull-up,
the estimated bus capacitance and an ideal rail, released from 0 V at t=0.
Any failure (ngspice missing, a non-zero exit, a timeout, unreadable output)
leaves the Calculation without a simulation; it never fails the request.

This is the feature's I/O edge for ngspice. The service takes a
``SpiceRunner`` so tests can fake it; ``Ngspice`` is the real one.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.features.debug.calc import I2cLine, format_farads, format_ohms, i2c_line
from app.features.debug.design import Design
from app.features.debug.schemas import Calculation, Simulation

logger = get_logger(__name__)

_TAUS = 5  # simulate to 5 RC: the line is within 1% of the rail
_STEPS = 200
_POINTS = 120  # what the UI draws


class SpiceRunner(Protocol):
    async def run(self, netlist: str) -> str | None:
        """The ``wrdata`` output of ``netlist`` (time and value columns), or
        None if the simulation couldn't run."""
        ...


class Ngspice:
    """Writes the netlist to a temp dir and runs ``ngspice -b`` there."""

    def __init__(self, binary: str | None = None, timeout_s: float | None = None) -> None:
        self.binary = binary or settings.NGSPICE
        self.timeout_s = timeout_s or settings.NGSPICE_TIMEOUT_S

    async def run(self, netlist: str) -> str | None:
        with tempfile.TemporaryDirectory(prefix="haxrok-spice-") as tmp:
            work = Path(tmp)
            (work / "line.cir").write_text(netlist, "utf-8")
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.binary,
                    "-b",
                    "line.cir",
                    cwd=work,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except FileNotFoundError:
                logger.info("ngspice isn't installed; skipping the I²C simulation")
                return None
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), self.timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("ngspice took longer than %.0f s", self.timeout_s)
                return None
            data = work / "out.txt"
            if proc.returncode != 0 or not data.exists():
                text = out.decode("utf-8", errors="replace").strip()
                logger.warning("ngspice failed (%s): %s", proc.returncode, text[-1000:])
                return None
            return data.read_text("utf-8")


def netlist(line: I2cLine) -> str:
    """The transient deck: rail → pull-up → line, bus capacitance to ground,
    starting at 0 V (the moment the driver lets go)."""
    tau = line.r_ohm * line.c_farad
    stop = _TAUS * tau
    return "\n".join(
        [
            f"I2C {line.net} released from low",
            f"Vpull rail 0 DC {line.v_pull:g}",
            f"Rpull rail sda {line.r_ohm:g}",
            f"Cbus sda 0 {line.c_farad:g} IC=0",
            ".control",
            f"tran {stop / _STEPS:g} {stop:g} uic",
            "wrdata out.txt v(sda)",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )


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


def simulation(line: I2cLine, rows: list[tuple[float, float]]) -> Simulation:
    """The waveform and the caption the UI shows under it."""
    rows = _thin(rows)
    lo, hi = _crossing(rows, 0.3 * line.v_pull), _crossing(rows, 0.7 * line.v_pull)
    rise = (hi - lo) * 1e9 if lo is not None and hi is not None else None
    over = _crossing(rows, line.v_supply + 0.3)
    text = (
        f"ngspice: {format_ohms(line.r_ohm)} to {line.v_pull:g} V into "
        f"~{format_farads(line.c_farad)} of bus (estimated: 10 pF per device pin + 10 pF trace)."
    )
    if rise is not None:
        text += f" Rise time 30→70% is {rise:.0f} ns."
    if over is not None:
        text += (
            f" The line passes the IC's {line.v_supply:g} V supply + 0.3 V after "
            f"{over * 1e9:.0f} ns and settles at {line.v_pull:g} V."
        )
    return Simulation(
        t_ns=[round(t * 1e9, 3) for t, _ in rows],
        v=[round(v, 4) for _, v in rows],
        v_pull=line.v_pull,
        v_supply=line.v_supply,
        rise_ns=round(rise, 1) if rise is not None else None,
        text=text,
    )


async def attach(
    calcs: list[Calculation], design: Design, runner: SpiceRunner
) -> list[Calculation]:
    """``calcs`` with a simulation on each flagged I²C line that has a
    pull-up; the others unchanged."""
    out = []
    for c in calcs:
        line = i2c_line(design, c)
        sim = None
        if line is not None and (text := await runner.run(netlist(line))) is not None:
            try:
                sim = simulation(line, parse(text))
            except ValueError as e:
                logger.warning("ngspice output for %s unreadable: %s", line.net, e)
        out.append(c.model_copy(update={"simulation": sim}) if sim else c)
    return out

"""Runs ``kicad-cli`` on an uploaded project: the netlist export and the ERC.

This is the feature's I/O edge for KiCad. The service takes a
``KicadRunner`` so tests can fake it; ``KicadCli`` is the real one.
"""

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.errors import AppError, BadRequestError
from app.core.logging import get_logger
from app.features.debug.project import Project

logger = get_logger(__name__)


@dataclass(frozen=True)
class KicadOutput:
    netlist: str  # KiCad s-expression netlist
    erc: str  # ERC report, JSON


class KicadRunner(Protocol):
    async def export(self, project: Project) -> KicadOutput: ...


class KicadUnavailableError(AppError):
    status_code = 503
    error_code = "KICAD_UNAVAILABLE"


class KicadCli:
    """Writes the project to a temp dir and runs ``kicad-cli`` there."""

    def __init__(self, binary: str | None = None, timeout_s: float | None = None) -> None:
        self.binary = binary or settings.KICAD_CLI
        self.timeout_s = timeout_s or settings.KICAD_TIMEOUT_S

    async def export(self, project: Project) -> KicadOutput:
        with tempfile.TemporaryDirectory(prefix="haxrok-") as tmp:
            work = Path(tmp)
            for rel, data in project.files.items():
                dest = work / "project" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            root = work / "project" / project.root
            net, erc = work / "board.net", work / "erc.json"
            await self._run(work, "sch", "export", "netlist", "-o", str(net), str(root))
            await self._run(
                work, "sch", "erc", "--format", "json", "--severity-all", "-o", str(erc), str(root)
            )
            return KicadOutput(netlist=net.read_text("utf-8"), erc=erc.read_text("utf-8"))

    async def _run(self, work: Path, *args: str) -> None:
        # kicad-cli writes its config under HOME; keep that in the temp dir so
        # runs don't share state and a read-only HOME doesn't break it.
        env = {**os.environ, "HOME": str(work)}
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                *args,
                cwd=work,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as e:
            raise KicadUnavailableError("kicad-cli isn't installed on the server.") from e
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self.timeout_s)
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise BadRequestError(
                f"kicad-cli took longer than {self.timeout_s:.0f} s on this project."
            ) from e
        text = out.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            logger.warning("kicad-cli %s failed (%s): %s", args[:3], proc.returncode, text[-2000:])
            raise BadRequestError(
                "kicad-cli couldn't read the schematic. If it was saved by a newer KiCad "
                f"than the server's, that's the cause. Output: {text[-500:]}"
            )

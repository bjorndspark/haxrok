"""Each analysis run's record: one JSON line per thing the run did,
appended to ``<DEBUG_RUN_DIR>/<run id>.jsonl``.

A line is ``{"at": <ISO time>, "kind": <kind>, "data": {...}}``. The kinds,
roughly in the order a run writes them:

- ``input``: mode, language, symptoms, measurements, the vendor-model
  analyses, the battery, and each project file's name and SHA-256 (the
  files themselves aren't kept);
- ``context``: the first call's user message and everything the evidence
  check and the diagrams read (``context_data``), so a recorded answer can
  be checked again without a model call;
- ``model``: one per model call: its name, which prompt, the answer, seconds;
- ``event``: each SSE event as sent, the result included;
- ``ask``: each follow-up question about the result and its answer
  (``/debug/ask-result`` with the run's id), after the run.

Events are written as the stream sends them; ``context`` and ``model`` lines
are written as the review makes them, which can be ahead of the events it
has queued. Each line's ``at`` is from ``get_clock()``.

Writing fails open, like the answer cache: an error is logged once and the
run goes on unrecorded. A line is a small blocking append, not worth a
thread. Records are kept until deleted by hand (see ``DECISIONS.md``,
"Runs are recorded as files"). ``load`` and ``evidence_context`` read one
back, for ``replay``.
"""

import dataclasses
import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from app.core.clock import get_clock
from app.core.logging import get_logger
from app.features.debug.context import ReviewContext
from app.features.debug.design import Design, Node, Part
from app.features.debug.evidence import EvidenceContext
from app.features.debug.schemas import Calculation, ErcEntry, WiringFinding

logger = get_logger(__name__)


class Recorder(Protocol):
    """Where one run's lines go. ``run_id`` names the run to the browser."""

    @property
    def run_id(self) -> str: ...

    def append(self, kind: str, data: object) -> None:
        """Add one line. Never raises."""
        ...


class NoRecorder:
    """Records nothing; the run id is empty. For tests and the evals."""

    run_id = ""

    def append(self, kind: str, data: object) -> None:
        pass


class FileRecorder:
    """Appends each line to ``<folder>/<run_id>.jsonl``, creating the folder."""

    def __init__(self, folder: Path, run_id: str) -> None:
        self._path = folder / f"{run_id}.jsonl"
        self._run_id = run_id
        self._failed = False

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def path(self) -> Path:
        return self._path

    def append(self, kind: str, data: object) -> None:
        """Write one line; on any error log it (the first time only) and go on."""
        try:
            line = json.dumps(
                {"at": get_clock().now().isoformat(), "kind": kind, "data": data},
                ensure_ascii=False,
                default=_encode,
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            if not self._failed:
                logger.warning(
                    "couldn't record run %s to %s; the run goes on unrecorded",
                    self._run_id,
                    self._path,
                    exc_info=True,
                )
            self._failed = True


# What ``new_run_id`` makes. A run id from the browser is checked against
# it before it names a file, so it can't point outside the runs folder.
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")


def existing(folder: Path, run_id: str) -> Recorder:
    """A recorder that appends to the run's record if it is there, else one
    that records nothing: an empty or malformed id, or a record deleted by
    hand. A follow-up never starts a record of its own."""
    if RUN_ID.fullmatch(run_id) and (folder / f"{run_id}.jsonl").is_file():
        return FileRecorder(folder, run_id)
    return NoRecorder()


def load(path: Path) -> list[dict[str, Any]]:
    """A record's lines, in order: ``{"at", "kind", "data"}`` each."""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def new_run_id() -> str:
    """A run id that sorts by start time and is safe as a file name:
    ``20261112-153012-a1b2c3`` (UTC)."""
    return f"{get_clock().now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def file_digests(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    """Each file's name, size and SHA-256, in name order."""
    return [
        {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]


def design_data(design: Design) -> dict[str, object]:
    """The design's parts and nets as plain data; ``Design(parts, nets)``
    rebuilds it from the same fields."""
    return {
        "parts": {ref: dataclasses.asdict(p) for ref, p in design.parts.items()},
        "nets": {
            name: [dataclasses.asdict(n) for n in nodes] for name, nodes in design.nets.items()
        },
    }


def context_data(ctx: ReviewContext) -> dict[str, object]:
    """The ``context`` line: the user message, everything ``EvidenceContext``
    holds, and the wiring findings and calculations ``diagram.miswired_pins``
    reads."""
    ev = ctx.evidence
    return {
        "user": ctx.user,
        "design": design_data(ev.design),
        "erc": list(ev.erc.values()),
        "pin_data": ev.pin_data,
        "symptom_ids": ev.symptom_ids,
        "measurement_ids": ev.measurement_ids,
        "reference_ids": ev.reference_ids,
        "datasheet_ids": ev.datasheet_ids,
        "wiring": ctx.table.findings,
        "calculations": ctx.calculations,
    }


def design_from(data: Mapping[str, Any]) -> Design:
    """The design ``design_data`` wrote."""
    return Design(
        {ref: Part(**p) for ref, p in data["parts"].items()},
        {name: [Node(**n) for n in nodes] for name, nodes in data["nets"].items()},
    )


@dataclass(frozen=True)
class Recorded:
    """What a ``context`` line gives back: the evidence check's input, and
    the wiring findings and calculations the diagrams read."""

    evidence: EvidenceContext
    findings: list[WiringFinding]
    calculations: list[Calculation]


def evidence_context(data: Mapping[str, Any]) -> Recorded:
    """The ``context`` line read back (``context_data`` wrote it)."""
    erc = [ErcEntry.model_validate(e) for e in data["erc"]]
    return Recorded(
        evidence=EvidenceContext(
            design=design_from(data["design"]),
            erc={e.id: e for e in erc},
            pin_data=frozenset(data["pin_data"]),
            symptom_ids=frozenset(data["symptom_ids"]),
            measurement_ids=frozenset(data["measurement_ids"]),
            reference_ids=frozenset(data["reference_ids"]),
            datasheet_ids=frozenset(data["datasheet_ids"]),
        ),
        findings=[WiringFinding.model_validate(w) for w in data["wiring"]],
        calculations=[Calculation.model_validate(c) for c in data["calculations"]],
    )


def _encode(value: object) -> object:
    """JSON for what ``json`` can't write: Pydantic models and sets (sorted)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"can't record a {type(value).__name__}")

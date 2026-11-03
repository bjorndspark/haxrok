"""Debug orchestration: uploaded project + symptoms -> checked analysis.

Two phases, so failures land where the UI can show them:

1. ``prepare`` (before the response starts): validate the upload, run
   kicad-cli, parse the netlist and ERC. A bad folder or a schematic the
   server's KiCad can't open is an ordinary error response.
2. ``stream`` (the SSE body): design summary at once, then our own stage
   updates while the model thinks (it streams nothing for ~90 s per call),
   then the checked result or an error event.

The model is called twice (see ``prompt``): a free-text diagnosis, then a
conversion of it into the structured answer. The kicad runner and both
model calls are passed in, so tests fake them.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.core.errors import AppError, BadRequestError
from app.core.logging import get_logger
from app.features.debug import pins, prompt
from app.features.debug.design import Design, compact, format_erc, parse_erc, parse_netlist
from app.features.debug.evidence import EvidenceContext, check
from app.features.debug.kicad import KicadRunner
from app.features.debug.project import load_project
from app.features.debug.schemas import (
    Analysis,
    Claim,
    DesignSummary,
    ErcEntry,
    ErrorEvent,
    Measurement,
    MeasurementInput,
    ProgressEvent,
    Symptom,
)

logger = get_logger(__name__)

Diagnoser = Callable[[str, str], Awaitable[str]]
"""(system prompt, user message) -> the model's free-text diagnosis."""

Structurer = Callable[[str, str], Awaitable[Analysis[Claim]]]
"""(system prompt, user message) -> the model's structured analysis."""


@dataclass(frozen=True)
class Model:
    """The two model calls one analysis makes."""

    diagnose: Diagnoser
    structure: Structurer


SseEvent = dict[str, str]


@dataclass(frozen=True)
class Prepared:
    """Everything parsed from the input, ready for the model call."""

    summary: DesignSummary
    design: Design
    erc: list[ErcEntry]
    pin_lookup: pins.PinLookup
    symptoms: list[Symptom]
    measurements: list[Measurement]


async def prepare(
    files: list[tuple[str, bytes]],
    symptoms_text: str,
    measurements: list[MeasurementInput],
    kicad: KicadRunner,
) -> Prepared:
    """Validate the upload, run kicad-cli and parse its output. Raises
    ``AppError`` subclasses with messages the user can act on."""
    symptoms = prompt.number_symptoms(symptoms_text)
    if not symptoms:
        raise BadRequestError("Describe what you see on the bench first.", field="symptoms")
    project = load_project(files)
    output = await kicad.export(project)
    try:
        design = parse_netlist(output.netlist)
        erc = parse_erc(output.erc)
    except ValueError as e:
        raise BadRequestError(f"Couldn't parse kicad-cli's output: {e}") from e
    lookup = pins.lookup(design)
    numbered = [
        Measurement(id=f"m{i}", step_id=m.step_id, context=m.context, value=m.value)
        for i, m in enumerate(measurements, start=1)
    ]
    summary = DesignSummary(
        project=project.name,
        sheets=sum(1 for p in project.files if p.endswith(".kicad_sch")),
        parts=len(design.parts),
        nets=len(design.nets),
        ics=lookup.ics,
        erc=erc,
        wiring=lookup.findings,
        symptoms=symptoms,
        measurements=numbered,
    )
    return Prepared(summary, design, erc, lookup, symptoms, numbered)


async def stream(prepared: Prepared, model: Model) -> AsyncIterator[SseEvent]:
    """The SSE body for one analysis run."""
    yield _event("design_summary", prepared.summary.model_dump_json())
    with_data = sum(1 for ic in prepared.summary.ics if ic.pin_data)
    conflicts = len(prepared.pin_lookup.findings)
    plural = "s" if conflicts > 1 else ""
    found = f" {conflicts} pin{plural} wired against the datasheet." if conflicts else ""
    yield _progress(
        "pin_lookup",
        f"Pin table found for {with_data} of {len(prepared.summary.ics)} ICs.{found}",
    )
    yield _progress(
        "reasoning", "The model is tracing the circuit against your symptoms. About 2 minutes."
    )
    user = prompt.build_user_message(
        symptoms=prepared.symptoms,
        measurements=prepared.measurements,
        netlist=compact(prepared.design),
        erc=format_erc(prepared.erc),
        pin_table=prepared.pin_lookup.text,
        findings=pins.format_findings(prepared.pin_lookup.findings),
    )
    start = time.monotonic()
    try:
        diagnosis = await model.diagnose(prompt.DIAGNOSE, user)
        # Logged whole: it's the reasoning the structured answer must follow,
        # and the first thing to read when a result looks wrong.
        logger.info(
            "debug diagnosis of %s in %.1fs:\n%s",
            prepared.summary.project,
            time.monotonic() - start,
            diagnosis,
        )
        yield _progress("structuring", "Writing the diagnosis up as claims and bench steps.")
        analysis = await model.structure(
            prompt.STRUCTURE, prompt.build_structure_message(diagnosis=diagnosis, user=user)
        )
    except AppError as e:
        yield _event("error", ErrorEvent(message=e.detail).model_dump_json())
        return
    logger.info(
        "debug analysis of %s: %d hypotheses in %.1fs",
        prepared.summary.project,
        len(analysis.hypotheses),
        time.monotonic() - start,
    )
    yield _progress("checking", "Checking every citation against your schematic.")
    ctx = EvidenceContext(
        design=prepared.design,
        erc={e.id: e for e in prepared.erc},
        pin_data=prepared.pin_lookup.pin_refs,
        symptom_ids=frozenset(s.id for s in prepared.symptoms),
        measurement_ids=frozenset(m.id for m in prepared.measurements),
    )
    yield _event("result", check(analysis, ctx).model_dump_json())


def _progress(stage: str, message: str) -> SseEvent:
    return _event(
        "progress",
        ProgressEvent.model_validate({"stage": stage, "message": message}).model_dump_json(),
    )


def _event(name: str, data: str) -> SseEvent:
    return {"event": name, "data": data}

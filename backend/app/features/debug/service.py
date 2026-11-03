"""Debug orchestration: uploaded project + symptoms -> checked analysis.

Two phases, so failures land where the UI can show them:

1. ``prepare`` (before the response starts): validate the upload, run
   kicad-cli, parse the netlist and ERC. A bad folder or a schematic the
   server's KiCad can't open is an ordinary error response.
2. ``stream`` (the SSE body): design summary at once, then our own stage
   updates while the model thinks (it streams nothing for ~90 s), then the
   checked result or an error event.

The kicad runner and the model call are passed in, so tests fake both.
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

Reasoner = Callable[[str, str], Awaitable[Analysis[Claim]]]
"""(system prompt, user message) -> the model's analysis."""

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
        symptoms=symptoms,
        measurements=numbered,
    )
    return Prepared(summary, design, erc, lookup, symptoms, numbered)


async def stream(prepared: Prepared, reason: Reasoner) -> AsyncIterator[SseEvent]:
    """The SSE body for one analysis run."""
    yield _event("design_summary", prepared.summary.model_dump_json())
    with_data = sum(1 for ic in prepared.summary.ics if ic.pin_data)
    yield _progress(
        "pin_lookup",
        f"Pin table found for {with_data} of {len(prepared.summary.ics)} ICs.",
    )
    yield _progress("reasoning", "The model is reading the schematic. This takes about 2 minutes.")
    user = prompt.build_user_message(
        symptoms=prepared.symptoms,
        measurements=prepared.measurements,
        netlist=compact(prepared.design),
        erc=format_erc(prepared.erc),
        pin_table=prepared.pin_lookup.text,
    )
    start = time.monotonic()
    try:
        analysis = await reason(prompt.SYSTEM, user)
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

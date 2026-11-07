"""Debug orchestration: uploaded project + symptoms -> checked analysis.

Two phases, so failures land where the UI can show them:

1. ``prepare`` (before the response starts): validate the upload, run
   kicad-cli, parse the netlist and ERC, find each IC's datasheet PDF. A
   bad folder or a schematic the server's KiCad can't open is an ordinary
   error response; a datasheet that can't be found is only a status.
2. ``stream`` (the SSE body): design summary at once, then each found
   datasheet read for facts (``facts``, cached per part), then our own stage
   updates while the model thinks (it streams nothing for ~90 s per call),
   and, in parallel, each ngspice simulation as it finishes; then the
   checked result or an error event.

The model is called twice (see ``prompt``): a free-text diagnosis, then a
conversion of it into the structured answer. ``ask`` answers a question
about one simulation afterwards, ``ask_result`` a follow-up about the run.
The kicad and ngspice runners, the datasheet finder and reader, and the
model calls are passed in, so tests fake them.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.core.errors import AppError, BadRequestError
from app.core.logging import get_logger
from app.features.debug import application, calc, diagram, pins, prompt, reference, spice
from app.features.debug.datasheet import DatasheetFinder
from app.features.debug.design import (
    Design,
    compact,
    format_erc,
    parse_erc,
    parse_labels,
    parse_netlist,
)
from app.features.debug.evidence import EvidenceContext, check
from app.features.debug.facts import DatasheetReading, FactReader
from app.features.debug.kicad import KicadRunner
from app.features.debug.project import load_project
from app.features.debug.schemas import (
    Analysis,
    AskRequest,
    Calculation,
    Claim,
    DatasheetStatus,
    DesignSummary,
    ErcEntry,
    ErrorEvent,
    Language,
    Measurement,
    MeasurementInput,
    Mode,
    ProgressEvent,
    ReferenceNote,
    ResultAskRequest,
    Symptom,
)
from app.features.debug.spice_models import SpiceModelFinder

logger = get_logger(__name__)

Diagnoser = Callable[[str, str], Awaitable[str]]
"""(system prompt, user message) -> the model's free-text diagnosis."""

Structurer = Callable[[str, str], Awaitable[Analysis[Claim]]]
"""(system prompt, user message) -> the model's structured analysis."""

Answerer = Callable[[str, str], Awaitable[str]]
"""(system prompt, user message) -> the model's answer to a question."""


@dataclass(frozen=True)
class Model:
    """The two model calls one analysis makes."""

    diagnose: Diagnoser
    structure: Structurer


SseEvent = dict[str, str]


@dataclass(frozen=True)
class Prepared:
    """Everything parsed from the input, ready for the model call."""

    mode: Mode
    summary: DesignSummary
    design: Design
    erc: list[ErcEntry]
    pin_lookup: pins.PinLookup
    references: list[ReferenceNote]
    calculations: list[Calculation]
    symptoms: list[Symptom]
    measurements: list[Measurement]
    datasheets: list[DatasheetStatus]
    language: Language = "en"


async def prepare(
    files: list[tuple[str, bytes]],
    symptoms_text: str,
    measurements: list[MeasurementInput],
    kicad: KicadRunner,
    datasheets: DatasheetFinder,
    spice_models: SpiceModelFinder,
    mode: Mode = "debug",
    language: Language = "en",
) -> Prepared:
    """Validate the upload, run kicad-cli and parse its output, then look
    up each IC's datasheet and SPICE model. Raises
    ``AppError`` subclasses with messages the user can act on. In review
    mode the symptoms are optional notes on what the board is for.
    ``language`` is the one the structured answer is written in."""
    symptoms = prompt.number_symptoms(symptoms_text)
    if mode == "debug" and not symptoms:
        raise BadRequestError("Describe what you see on the bench first.", field="symptoms")
    project = load_project(files)
    output = await kicad.export(project)
    try:
        design = parse_netlist(output.netlist)
        erc = parse_erc(output.erc)
    except ValueError as e:
        raise BadRequestError(f"Couldn't parse kicad-cli's output: {e}") from e
    lookup = pins.lookup(design, erc, parse_labels(project.files))
    notes = reference.lookup(design)
    calcs = calc.lookup(design)
    ic_parts = [design.parts[ic.ref] for ic in lookup.ics]
    sheets = await datasheets.find(ic_parts)
    models = await spice_models.find(ic_parts, sheets, project.files, project.root)
    numbered = [
        Measurement(id=f"m{i}", step_id=m.step_id, context=m.context, value=m.value)
        for i, m in enumerate(measurements, start=1)
    ]
    summary = DesignSummary(
        mode=mode,
        project=project.name,
        sheets=sum(1 for p in project.files if p.endswith(".kicad_sch")),
        parts=len(design.parts),
        nets=len(design.nets),
        ics=lookup.ics,
        datasheets=sheets,
        spice_models=models,
        erc=erc,
        wiring=lookup.findings,
        pin_warnings=lookup.warnings,
        references=notes,
        calculations=calcs,
        symptoms=symptoms,
        measurements=numbered,
    )
    return Prepared(
        mode, summary, design, erc, lookup, notes, calcs, symptoms, numbered, sheets, language
    )


async def stream(
    prepared: Prepared,
    model: Model,
    ngspice: spice.SpiceRunner | None = None,
    facts: FactReader | None = None,
) -> AsyncIterator[SseEvent]:
    """The SSE body for one analysis run: the review (with ``facts``, after
    reading the found datasheets), and with ``ngspice`` the simulations
    ``spice.plan`` picks, run alongside it. The terminal event (result or
    error) waits for the simulations, so it stays last."""
    yield _event("design_summary", prepared.summary.model_dump_json())
    sources = [_review(prepared, model, facts)]
    jobs = spice.plan(prepared.calculations, prepared.design) if ngspice else []
    if ngspice and jobs:
        sources.append(_simulations(jobs, ngspice))
    terminal: SseEvent | None = None
    async for ev in _merge(sources):
        if ev["event"] in ("result", "error"):
            terminal = ev
        else:
            yield ev
    if terminal is not None:
        yield terminal


async def _simulations(
    jobs: list[spice.Job], ngspice: spice.SpiceRunner
) -> AsyncIterator[SseEvent]:
    start = time.monotonic()
    n = 0
    async for sim in spice.simulate(jobs, ngspice):
        n += 1
        yield _event("simulation", sim.model_dump_json())
    logger.info("ngspice: %d of %d simulations in %.2fs", n, len(jobs), time.monotonic() - start)


_DONE: SseEvent = {}


async def _merge(sources: list[AsyncIterator[SseEvent]]) -> AsyncIterator[SseEvent]:
    """Events from all ``sources`` as they come. Each source runs in its
    own task; closing the merge (the client went away) cancels them all,
    and an exception in a source is raised here once the others are done."""
    queue: asyncio.Queue[SseEvent] = asyncio.Queue()

    async def pump(source: AsyncIterator[SseEvent]) -> None:
        try:
            async for ev in source:
                await queue.put(ev)
        finally:
            await queue.put(_DONE)

    tasks = [asyncio.create_task(pump(s)) for s in sources]
    try:
        left = len(tasks)
        while left:
            ev = await queue.get()
            if ev is _DONE:
                left -= 1
            else:
                yield ev
        for t in tasks:
            t.result()
    finally:
        for t in tasks:
            t.cancel()


async def _review(
    prepared: Prepared, model: Model, facts: FactReader | None = None
) -> AsyncIterator[SseEvent]:
    """The model's part of the stream: progress, then result or error."""
    reading = DatasheetReading()
    found_sheets = sum(1 for s in prepared.datasheets if s.status == "found")
    if facts is not None and found_sheets:
        yield _progress(
            "pin_lookup",
            f"Reading {found_sheets} datasheet{'s' if found_sheets > 1 else ''} "
            "(once per part, then cached).",
        )
        reading = await facts.read(prepared.design, prepared.datasheets)
    # Pins read from a datasheet replace that IC's hand-written table lines;
    # the wiring findings stay the ones prepare() made from the table.
    table = (
        pins.lookup(prepared.design, datasheet_pins=reading.pins)
        if reading.pins
        else prepared.pin_lookup
    )
    with_data = sum(1 for ic in table.ics if ic.pin_data)
    conflicts = len(prepared.pin_lookup.findings)
    plural = "s" if conflicts > 1 else ""
    found = f" {conflicts} pin{plural} wired against the datasheet." if conflicts else ""
    n_warn = len(prepared.pin_lookup.warnings)
    if n_warn:
        found += f" {n_warn} transistor{'s' if n_warn > 1 else ''} with a pin order to check."
    n_calcs = len(prepared.calculations)
    flagged = sum(1 for c in prepared.calculations if c.flagged)
    calcs = (
        f" {n_calcs} value{'s' if n_calcs != 1 else ''} calculated from the netlist"
        + (f", {flagged} flagged." if flagged else ".")
        if n_calcs
        else ""
    )
    notes = f" {len(prepared.references)} reference-design notes." if prepared.references else ""
    read = (
        f" {len(reading.sources)} datasheet{'s' if len(reading.sources) > 1 else ''} read,"
        f" {len(reading.facts)} fact{'' if len(reading.facts) == 1 else 's'}."
        if reading.sources
        else ""
    )
    deviations = application.check(prepared.design, reading.application)
    if deviations:
        n_dev = len(deviations)
        read += (
            f" {n_dev} deviation{'s' if n_dev > 1 else ''} from a datasheet's typical application."
        )
    yield _progress(
        "pin_lookup",
        f"Pin data for {with_data} of {len(table.ics)} ICs.{read}{found}{notes}{calcs}",
    )
    review = prepared.mode == "review"
    yield _progress(
        "reasoning",
        "The model is reviewing the circuit block by block. About 2 minutes."
        if review
        else "The model is tracing the circuit against your symptoms. About 2 minutes.",
    )
    user = prompt.build_user_message(
        mode=prepared.mode,
        symptoms=prepared.symptoms,
        measurements=prepared.measurements,
        netlist=compact(prepared.design),
        erc=format_erc(prepared.erc),
        pin_table=table.text,
        findings=pins.format_findings(prepared.pin_lookup.findings),
        warnings=pins.format_warnings(prepared.pin_lookup.warnings),
        references=reference.format_notes(prepared.references),
        calculations=calc.format_calculations(prepared.calculations),
        datasheet_facts=reading.text,
        deviations=application.format_deviations(deviations) if reading.application else "",
    )
    start = time.monotonic()
    try:
        diagnosis = await model.diagnose(
            prompt.REVIEW_DIAGNOSE if review else prompt.DIAGNOSE, user
        )
        # Logged whole: it's the reasoning the structured answer must follow,
        # and the first thing to read when a result looks wrong.
        logger.info(
            "debug %s of %s in %.1fs:\n%s",
            "review" if review else "diagnosis",
            prepared.summary.project,
            time.monotonic() - start,
            diagnosis,
        )
        yield _progress(
            "structuring",
            "Writing the review up as issues, claims and fixes."
            if review
            else "Writing the diagnosis up as claims and bench steps.",
        )
        # Only the write-up switches language: the diagnosis is reasoning the
        # user never sees, and the prompts that find bugs were tuned in English.
        analysis = await model.structure(
            prompt.in_language(
                prompt.REVIEW_STRUCTURE if review else prompt.STRUCTURE, prepared.language
            ),
            prompt.build_structure_message(diagnosis=diagnosis, user=user),
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
        pin_data=table.pin_refs,
        symptom_ids=frozenset(s.id for s in prepared.symptoms),
        measurement_ids=frozenset(m.id for m in prepared.measurements),
        reference_ids=frozenset(
            [n.id for n in prepared.references] + [c.id for c in prepared.calculations]
        ),
        datasheet_ids=reading.ids | {d.id for d in deviations},
    )
    checked = check(analysis, ctx)
    miswired = diagram.miswired_pins(prepared.pin_lookup.findings, prepared.calculations)
    checked.diagrams = {
        h.id: d for h in checked.hypotheses if (d := diagram.build(h, prepared.design, miswired))
    }
    yield _event("result", checked.model_dump_json())


async def ask(req: AskRequest, answer: Answerer) -> str:
    """The model's answer to a question about one simulation, from the
    deck, the caption and the samples the browser sent back."""
    start = time.monotonic()
    text = await answer(
        prompt.in_language(prompt.ASK_SIMULATION, req.language),
        prompt.build_ask_message(req.simulation, req.history, req.question),
    )
    logger.info(
        "ask about %s (%s) in %.1fs: %r",
        req.simulation.id,
        req.simulation.title,
        time.monotonic() - start,
        req.question,
    )
    return text.strip()


async def ask_result(req: ResultAskRequest, answer: Answerer) -> str:
    """The model's answer to a follow-up question about a finished run,
    from the design summary and checked result the browser sent back."""
    start = time.monotonic()
    text = await answer(
        prompt.in_language(prompt.ASK_RESULT, req.language),
        prompt.build_result_ask_message(req.summary, req.result, req.history, req.question),
    )
    logger.info(
        "ask about %s (%s run, %d findings) in %.1fs: %r",
        req.summary.project,
        req.summary.mode,
        len(req.result.hypotheses),
        time.monotonic() - start,
        req.question,
    )
    return text.strip()


def _progress(stage: str, message: str) -> SseEvent:
    return _event(
        "progress",
        ProgressEvent.model_validate({"stage": stage, "message": message}).model_dump_json(),
    )


def _event(name: str, data: str) -> SseEvent:
    return {"event": name, "data": data}

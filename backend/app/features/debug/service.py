"""Debug orchestration: uploaded project + symptoms -> checked analysis.

Two phases, so failures land where the UI can show them:

1. ``prepare`` (before the response starts): validate the upload, run
   kicad-cli, parse the netlist and ERC, find each IC's datasheet PDF and
   SPICE model, and check that each model loads in ngspice. A bad folder
   or a schematic the server's KiCad can't open is an ordinary error
   response; a datasheet that can't be found is only a status.
2. ``stream`` (the SSE body): design summary at once, then each found
   datasheet read for facts (``facts``, cached per part), then our own stage
   updates while the model thinks (it streams nothing for ~90 s per call),
   and, in parallel, each ngspice simulation as it finishes; then the
   checked result or an error event. The vendor-model simulations
   (``spice_circuits``, minutes each) start once the datasheets are read
   and follow the terminal event, each as it finishes.

The model is called twice (see ``prompt``): a free-text diagnosis, then a
conversion of it into the structured answer. ``ask`` answers a question
about one simulation afterwards, ``ask_result`` a follow-up about the run;
``rerun`` re-runs an RC simulation with the user's R and C (a what-if).
The kicad and ngspice runners, the datasheet finder and reader, the
model calls and the run's recorder (``record``) are passed in, so tests
fake them.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field

from app.core.errors import AppError, BadRequestError
from app.core.logging import get_logger
from app.features.debug import (
    calc,
    context,
    diagram,
    mcu,
    pins,
    power,
    prompt,
    record,
    reference,
    spice,
    spice_circuits,
    spice_convert,
)
from app.features.debug.datasheet import DatasheetFinder
from app.features.debug.design import (
    Design,
    Label,
    parse_erc,
    parse_labels,
    parse_netlist,
)
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
    RerunRequest,
    ResultAskRequest,
    SimAnalysis,
    Simulation,
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
    labels: list[Label]  # the schematic's net labels, for the wiring findings' causes
    references: list[ReferenceNote]
    calculations: list[Calculation]
    symptoms: list[Symptom]
    measurements: list[Measurement]
    datasheets: list[DatasheetStatus]
    language: Language = "en"
    upload: dict[str, bytes] = field(default_factory=dict, repr=False)  # the model files
    analyses: tuple[SimAnalysis, ...] = ("startup",)  # the vendor-model simulations
    battery: power.Range | None = None  # the review form's battery range, if given


async def prepare(
    files: list[tuple[str, bytes]],
    symptoms_text: str,
    measurements: list[MeasurementInput],
    kicad: KicadRunner,
    datasheets: DatasheetFinder,
    spice_models: SpiceModelFinder,
    mode: Mode = "debug",
    language: Language = "en",
    *,
    ngspice: spice.SpiceLoader | None = None,
    analyses: Sequence[SimAnalysis] = ("startup",),
    battery: power.Range | None = None,
) -> Prepared:
    """Validate the upload, run kicad-cli and parse its output, then look
    up each IC's datasheet and SPICE model, and with ``ngspice`` check that
    each found model loads (``spice_convert``). Raises
    ``AppError`` subclasses with messages the user can act on. In review
    mode the symptoms are optional notes on what the board is for.
    ``language`` is the one the structured answer is written in;
    ``analyses`` the vendor-model simulations ``stream`` runs; ``battery``
    the user's (min, max) for the board's battery, over the inferred one."""
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
    labels = parse_labels(project.files)
    lookup = pins.lookup(design, erc, labels)
    notes = reference.lookup(design)
    calcs = calc.lookup(design)
    ic_parts = [design.parts[ic.ref] for ic in lookup.ics]
    sheets = await datasheets.find(ic_parts)
    models = await spice_models.find(ic_parts, sheets, project.files, project.root)
    if ngspice is not None:
        models = await spice_convert.check(models, project.files, ngspice)
    numbered = [
        Measurement(id=f"m{i}", step_id=m.step_id, context=m.context, value=m.value)
        for i, m in enumerate(measurements, start=1)
    ]
    tree = power.build(design, calcs, battery=battery)
    # The pin table's microcontroller rules, shown before the datasheets are
    # read; kept out of Prepared.calculations, which the power tree and the
    # simulations read.
    pin_rules = mcu.check(design, tree, mcu.rules(design), first_id=len(calcs) + 1)
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
        calculations=calcs + pin_rules,
        symptoms=symptoms,
        measurements=numbered,
        power=tree,
    )
    return Prepared(
        mode,
        summary,
        design,
        erc,
        lookup,
        labels,
        notes,
        calcs,
        symptoms,
        numbered,
        sheets,
        language,
        upload=project.files,
        analyses=tuple(analyses),
        battery=battery,
    )


async def stream(
    prepared: Prepared,
    model: Model,
    ngspice: spice.SpiceRunner | None = None,
    facts: FactReader | None = None,
    models: spice.ModelRunner | None = None,
    recorder: record.Recorder | None = None,
) -> AsyncIterator[SseEvent]:
    """``_stream``'s events, each recorded with ``recorder`` as it is sent,
    after the run's input; the design summary carries the recorder's run id."""
    rec = recorder or record.NoRecorder()
    rec.append("input", _input(prepared))
    if rec.run_id:
        logger.info("run %s: %s, %s mode", rec.run_id, prepared.summary.project, prepared.mode)
    # aclosing: when the client goes away, _stream's own cleanup (cancelling
    # the reading and the vendor-model runs) runs now, not when collected.
    async with aclosing(_stream(prepared, model, ngspice, facts, models, rec)) as events:
        async for ev in events:
            rec.append("event", {"event": ev["event"], "data": json.loads(ev["data"])})
            yield ev


def _input(prepared: Prepared) -> dict[str, object]:
    """The record's ``input`` line: what the user sent, the files as digests."""
    return {
        "project": prepared.summary.project,
        "mode": prepared.mode,
        "language": prepared.language,
        "symptoms": prepared.symptoms,
        "measurements": prepared.measurements,
        "analyses": list(prepared.analyses),
        "battery": list(prepared.battery) if prepared.battery else None,
        "files": record.file_digests(prepared.upload),
    }


async def _stream(
    prepared: Prepared,
    model: Model,
    ngspice: spice.SpiceRunner | None,
    facts: FactReader | None,
    models: spice.ModelRunner | None,
    rec: record.Recorder,
) -> AsyncGenerator[SseEvent]:
    """The SSE body for one analysis run: the review (with ``facts``, after
    reading the found datasheets), and with ``ngspice`` the simulations
    ``spice.plan`` picks, run alongside it (an enable/reset RC fed by a
    regulator once the datasheets are read: its start-up time ramps the
    rail). The terminal event (result or error) waits for those. With
    ``models``, the vendor-model simulations
    (``spice_circuits``) start once the datasheets are read; they take
    minutes, so they come after the terminal event, each as it finishes.
    Closing the stream cancels whatever still runs."""
    summary = prepared.summary.model_copy(update={"run_id": rec.run_id})
    yield _event("design_summary", summary.model_dump_json())
    reading = asyncio.create_task(_read(prepared, facts))
    vendor: asyncio.Queue[SseEvent] = asyncio.Queue()
    later = (
        asyncio.create_task(_vendor_simulations(prepared, reading, models, vendor))
        if models is not None and prepared.analyses
        else None
    )
    try:
        sources = [_review(prepared, model, facts, reading, rec)]
        if ngspice is not None:
            now, fed = _by_rail(spice.plan(prepared.calculations, prepared.design), prepared)
            if now:
                sources.append(_simulations(now, ngspice))
            if fed:
                sources.append(_ramped_simulations(fed, reading, ngspice))
        terminal: SseEvent | None = None
        async for ev in _merge(sources):
            if ev["event"] in ("result", "error"):
                terminal = ev
            else:
                yield ev
        if terminal is not None:
            yield terminal
        if later is not None:
            while (ev := await vendor.get()) is not _DONE:
                yield ev
    finally:
        reading.cancel()
        if later is not None:
            later.cancel()


async def _read(prepared: Prepared, facts: FactReader | None) -> DatasheetReading:
    """The found datasheets' facts, read once for the review and the
    vendor-model simulations."""
    if facts is None or not any(s.status == "found" for s in prepared.datasheets):
        return DatasheetReading()
    return await facts.read(prepared.design, prepared.datasheets)


async def _vendor_simulations(
    prepared: Prepared,
    reading: Awaitable[DatasheetReading],
    runner: spice.ModelRunner,
    out: asyncio.Queue[SseEvent],
) -> None:
    """Run the vendor-model simulations into ``out``, then ``_DONE``. A
    failure here is logged and ends them; it never fails the analysis."""
    start = time.monotonic()
    n = 0
    try:
        got = await reading
        async for sim in spice_circuits.simulate(
            prepared.summary.spice_models,
            prepared.upload,
            prepared.design,
            got,
            prepared.analyses,
            runner,
            context.budgeted_tree(prepared.design, prepared.calculations, got, prepared.battery),
            context.fixes(
                prepared.design,
                prepared.calculations,
                got,
                prepared.erc,
                prepared.labels,
                prepared.battery,
            ),
        ):
            n += 1
            await out.put(_event("simulation", sim.model_dump_json()))
    except Exception:
        logger.exception("vendor-model simulations of %s failed", prepared.summary.project)
    finally:
        logger.info("vendor models: %d simulations in %.1fs", n, time.monotonic() - start)
        out.put_nowait(_DONE)


async def _simulations(
    jobs: list[spice.Job], ngspice: spice.SpiceRunner
) -> AsyncIterator[SseEvent]:
    start = time.monotonic()
    n = 0
    async for sim in spice.simulate(jobs, ngspice):
        n += 1
        yield _event("simulation", sim.model_dump_json())
    logger.info("ngspice: %d of %d simulations in %.2fs", n, len(jobs), time.monotonic() - start)


def _by_rail(
    jobs: list[spice.Job], prepared: Prepared
) -> tuple[list[spice.Job], list[tuple[spice.Job, str]]]:
    """``jobs`` split into those that run now and the enable/reset RCs whose
    rail a regulator feeds, with that regulator's ref."""
    now: list[spice.Job] = []
    fed: list[tuple[spice.Job, str]] = []
    for job in jobs:
        rail = job.line.rail if isinstance(job.line, calc.RcLine) else None
        ref = spice_circuits.regulator_of(prepared.design, rail) if rail else None
        if ref is None:
            now.append(job)
        else:
            fed.append((job, ref))
    return now, fed


async def _ramped_simulations(
    fed: list[tuple[spice.Job, str]],
    reading: Awaitable[DatasheetReading],
    ngspice: spice.SpiceRunner,
) -> AsyncIterator[SseEvent]:
    """The regulator-fed RC runs: each rail ramps over its regulator's
    start-up time from the datasheet reading, or stays a step without one
    (a failed reading counts as none)."""
    try:
        got = await reading
    except Exception:
        logger.warning("datasheet reading failed; RC rails stay steps", exc_info=True)
        got = DatasheetReading()
    jobs = [spice.with_ramp(job, spice_circuits.ramp(ref, got)) for job, ref in fed]
    async for ev in _simulations(jobs, ngspice):
        yield ev


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
    prepared: Prepared,
    model: Model,
    facts: FactReader | None,
    reading_task: Awaitable[DatasheetReading],
    rec: record.Recorder,
) -> AsyncIterator[SseEvent]:
    """The model's part of the stream: progress, the datasheet facts (when a
    datasheet was found), then result or error. ``reading_task`` is
    ``_read``'s, shared with the vendor-model runs. The context and each
    model answer go to ``rec``."""
    found_sheets = sum(1 for s in prepared.datasheets if s.status == "found")
    if facts is not None and found_sheets:
        yield _progress(
            "pin_lookup",
            f"Reading {found_sheets} datasheet{'s' if found_sheets > 1 else ''} "
            "(once per part, then cached).",
        )
    reading = await reading_task
    ctx = context.build(
        mode=prepared.mode,
        design=prepared.design,
        reading=reading,
        symptoms=prepared.symptoms,
        measurements=prepared.measurements,
        references=prepared.references,
        calculations=prepared.calculations,
        erc=prepared.erc,
        labels=prepared.labels,
        battery=prepared.battery,
    )
    rec.append("context", record.context_data(ctx))
    if facts is not None and found_sheets:
        yield _event("datasheet_facts", context.facts_event(ctx).model_dump_json())
    yield _progress("pin_lookup", context.progress_note(ctx))
    review = prepared.mode == "review"
    yield _progress(
        "reasoning",
        "The model is reviewing the circuit block by block. About 2 minutes."
        if review
        else "The model is tracing the circuit against your symptoms. About 2 minutes.",
    )
    start = time.monotonic()
    try:
        diagnosis = await model.diagnose(
            prompt.REVIEW_DIAGNOSE if review else prompt.DIAGNOSE, ctx.user
        )
        rec.append(
            "model",
            {
                "name": "debug_diagnosis",
                "prompt": "REVIEW_DIAGNOSE" if review else "DIAGNOSE",
                "answer": diagnosis,
                "seconds": round(time.monotonic() - start, 1),
            },
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
        structuring = time.monotonic()
        analysis = await model.structure(
            prompt.in_language(
                prompt.REVIEW_STRUCTURE if review else prompt.STRUCTURE, prepared.language
            ),
            prompt.build_structure_message(diagnosis=diagnosis, user=ctx.user),
        )
        rec.append(
            "model",
            {
                "name": "debug_analysis",
                "prompt": "REVIEW_STRUCTURE" if review else "STRUCTURE",
                "language": prepared.language,
                "answer": analysis,
                "seconds": round(time.monotonic() - structuring, 1),
            },
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
    checked = diagram.checked(analysis, ctx.evidence, ctx.table.findings, ctx.calculations)
    yield _event("result", checked.model_dump_json())


async def ask(req: AskRequest, answer: Answerer) -> str:
    """The model's answer to a question about one simulation, from the
    deck, the caption and the samples the browser sent back."""
    start = time.monotonic()
    text = await answer(
        prompt.in_language(prompt.ASK_SIMULATION, req.language),
        prompt.build_ask_message(req.simulation, req.history, req.question, req.what_if),
    )
    logger.info(
        "ask about %s (%s%s) in %.1fs: %r",
        req.simulation.id,
        req.simulation.title,
        ", with a what-if" if req.what_if else "",
        time.monotonic() - start,
        req.question,
    )
    return text.strip()


# What a what-if accepts: anything outside is a typo, not a design.
_R_OHM = (1.0, 100e6)
_C_FARAD = (0.1e-12, 0.1)


async def rerun(req: RerunRequest, runner: spice.SpiceRunner) -> Simulation:
    """An RC simulation re-run with the user's R and C. Raises
    BadRequestError for a run that isn't RC or a value out of bounds, and
    AppError when ngspice can't run it."""
    sim = req.simulation
    if sim.circuit is None or not sim.t:
        raise BadRequestError("Only an RC simulation can be re-run.", field="simulation")
    if not _R_OHM[0] <= req.r_ohm <= _R_OHM[1]:
        raise BadRequestError("R must be between 1 Ω and 100 MΩ.", field="r_ohm")
    if not _C_FARAD[0] <= req.c_farad <= _C_FARAD[1]:
        raise BadRequestError("C must be between 0.1 pF and 0.1 F.", field="c_farad")
    start = time.monotonic()
    new = await spice.rerun(sim, req.r_ohm, req.c_farad, runner)
    if new is None:
        raise AppError("ngspice couldn't run the what-if.", error_code="SIMULATION_FAILED")
    logger.info(
        "what-if on %s (%s): R %g → %g Ω, C %g → %g F in %.1fs",
        sim.id,
        sim.title,
        sim.circuit.r_ohm,
        req.r_ohm,
        sim.circuit.c_farad,
        req.c_farad,
        time.monotonic() - start,
    )
    return new


async def ask_result(
    req: ResultAskRequest, answer: Answerer, recorder: record.Recorder | None = None
) -> str:
    """The model's answer to a follow-up question about a finished run,
    from the design summary and checked result the browser sent back. The
    question and answer go to the run's record as an ``ask`` line."""
    start = time.monotonic()
    text = (
        await answer(
            prompt.in_language(prompt.ASK_RESULT, req.language),
            prompt.build_result_ask_message(req.summary, req.result, req.history, req.question),
        )
    ).strip()
    (recorder or record.NoRecorder()).append(
        "ask",
        {
            "question": req.question,
            "language": req.language,
            "answer": text,
            "seconds": round(time.monotonic() - start, 1),
        },
    )
    logger.info(
        "ask about %s (%s run, %d findings) in %.1fs: %r",
        req.summary.project,
        req.summary.mode,
        len(req.result.hypotheses),
        time.monotonic() - start,
        req.question,
    )
    return text


def _progress(stage: str, message: str) -> SseEvent:
    return _event(
        "progress",
        ProgressEvent.model_validate({"stage": stage, "message": message}).model_dump_json(),
    )


def _event(name: str, data: str) -> SseEvent:
    return {"event": name, "data": data}

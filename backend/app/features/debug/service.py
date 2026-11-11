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
The kicad and ngspice runners, the datasheet finder and reader, and the
model calls are passed in, so tests fake them.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from app.core.errors import AppError, BadRequestError
from app.core.logging import get_logger
from app.features.debug import (
    application,
    calc,
    diagram,
    pins,
    power,
    prompt,
    reference,
    spice,
    spice_circuits,
    spice_convert,
    voltage,
)
from app.features.debug.datasheet import DatasheetFinder
from app.features.debug.design import (
    Design,
    Label,
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
    DatasheetFactsEvent,
    DatasheetLine,
    DatasheetReadFailure,
    DatasheetStatus,
    DesignSummary,
    ErcEntry,
    ErrorEvent,
    Language,
    Measurement,
    MeasurementInput,
    Mode,
    PowerTree,
    ProgressEvent,
    ReferenceNote,
    RerunRequest,
    ResultAskRequest,
    SimAnalysis,
    Simulation,
    Symptom,
    WiringFinding,
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
        power=power.build(design, calcs, battery=battery),
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
) -> AsyncIterator[SseEvent]:
    """The SSE body for one analysis run: the review (with ``facts``, after
    reading the found datasheets), and with ``ngspice`` the simulations
    ``spice.plan`` picks, run alongside it (an enable/reset RC fed by a
    regulator once the datasheets are read: its start-up time ramps the
    rail). The terminal event (result or error) waits for those. With
    ``models``, the vendor-model simulations
    (``spice_circuits``) start once the datasheets are read; they take
    minutes, so they come after the terminal event, each as it finishes.
    Closing the stream cancels whatever still runs."""
    yield _event("design_summary", prepared.summary.model_dump_json())
    reading = asyncio.create_task(_read(prepared, facts))
    vendor: asyncio.Queue[SseEvent] = asyncio.Queue()
    later = (
        asyncio.create_task(_vendor_simulations(prepared, reading, models, vendor))
        if models is not None and prepared.analyses
        else None
    )
    try:
        sources = [_review(prepared, model, facts, reading)]
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
) -> AsyncIterator[SseEvent]:
    """The model's part of the stream: progress, the datasheet facts (when a
    datasheet was found), then result or error. ``reading_task`` is
    ``_read``'s, shared with the vendor-model runs."""
    found_sheets = sum(1 for s in prepared.datasheets if s.status == "found")
    if facts is not None and found_sheets:
        yield _progress(
            "pin_lookup",
            f"Reading {found_sheets} datasheet{'s' if found_sheets > 1 else ''} "
            "(once per part, then cached).",
        )
    reading = await reading_task
    # Pins read from a datasheet replace that IC's hand-written table lines,
    # and its pin roles and typical application recheck the wiring.
    table = (
        pins.lookup(
            prepared.design,
            prepared.erc,
            prepared.labels,
            datasheet_pins=reading.pins,
            datasheet_roles=reading.roles,
            application=reading.application,
        )
        if reading.pins or reading.application
        else prepared.pin_lookup
    )
    with_data = sum(1 for ic in table.ics if ic.pin_data)
    conflicts = len(table.findings)
    plural = "s" if conflicts > 1 else ""
    found = f" {conflicts} pin{plural} wired against the datasheet." if conflicts else ""
    n_warn = len(prepared.pin_lookup.warnings)
    if n_warn:
        found += f" {n_warn} transistor{'s' if n_warn > 1 else ''} with a pin order to check."
    # Rebuilt with the datasheets' output and charge voltages, now they're read;
    # its rails then meet each pin's datasheet limits.
    tree = power.build(
        prepared.design, prepared.calculations, reading.limits, battery=prepared.battery
    )
    calcs = prepared.calculations + voltage.check(
        prepared.design, tree, reading.limits, first_id=len(prepared.calculations) + 1
    )
    n_calcs = len(calcs)
    flagged = sum(1 for c in calcs if c.flagged)
    calc_note = (
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
    if facts is not None and found_sheets:
        yield _event(
            "datasheet_facts",
            _datasheet_facts(reading, deviations, tree, table.findings, calcs).model_dump_json(),
        )
    yield _progress(
        "pin_lookup",
        f"Pin data for {with_data} of {len(table.ics)} ICs.{read}{found}{notes}{calc_note}",
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
        findings=pins.format_findings(table.findings),
        warnings=pins.format_warnings(prepared.pin_lookup.warnings),
        references=reference.format_notes(prepared.references),
        calculations=calc.format_calculations(calcs),
        datasheet_facts=reading.text,
        deviations=application.format_deviations(deviations) if reading.application else "",
        power=power.format_power(tree),
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
        reference_ids=frozenset([n.id for n in prepared.references] + [c.id for c in calcs]),
        datasheet_ids=reading.ids | {d.id for d in deviations},
    )
    checked = check(analysis, ctx)
    miswired = diagram.miswired_pins(table.findings, calcs)
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


def _datasheet_facts(
    reading: DatasheetReading,
    deviations: list[application.Deviation],
    tree: PowerTree,
    wiring: list[WiringFinding],
    calculations: list[Calculation],
) -> DatasheetFactsEvent:
    """The datasheet_facts event: what claims can cite, and the power tree,
    wiring findings and calculations rebuilt with what the datasheets said,
    for the browser."""
    return DatasheetFactsEvent(
        power=tree,
        wiring=wiring,
        calculations=calculations,
        datasheet_facts=[
            DatasheetLine(id=f.id, ref=f.ref, page=f.page, text=f.body) for f in reading.facts
        ],
        deviations=[
            DatasheetLine(id=d.id, ref=d.ref, page=d.page, text=d.text) for d in deviations
        ],
        datasheet_failures=[
            DatasheetReadFailure(ref=ref, reason=why) for ref, why in reading.failed.items()
        ],
    )


def _event(name: str, data: str) -> SseEvent:
    return {"event": name, "data": data}

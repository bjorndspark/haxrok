"""The review's input, built once: what the model reads and what its answer
is checked against.

``build`` is pure. It takes the parsed board and what the datasheets said
and returns a ``ReviewContext``: the pin table and wiring findings, the
budgeted power tree, the calculations with the datasheet-limit and
microcontroller checks added, the deviations from each typical
application, the first call's user message and the ``EvidenceContext``.
The app's review (``service._review``) and the evals
(``evals/app_prompts.py``) both call it, so the prompt an eval measures is
the one the app sends. The progress text and the ``datasheet_facts``
event are read off the context by the functions below it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.features.debug import (
    application,
    budget,
    calc,
    mcu,
    pins,
    power,
    prompt,
    reference,
    roles,
    spice_circuits,
    voltage,
)
from app.features.debug.design import Design, Label, compact, format_erc
from app.features.debug.evidence import EvidenceContext
from app.features.debug.facts import DatasheetReading
from app.features.debug.schemas import (
    Calculation,
    DatasheetFactsEvent,
    DatasheetLine,
    DatasheetReadFailure,
    ErcEntry,
    Measurement,
    Mode,
    PowerTree,
    ReferenceNote,
    Symptom,
)


@dataclass(frozen=True)
class ReviewContext:
    """Everything one review sends the model and checks its answer against."""

    reading: DatasheetReading
    table: pins.PinLookup  # the pin table and wiring findings, datasheets applied
    tree: PowerTree  # budgeted, with the datasheets' voltages
    calculations: list[Calculation]  # the netlist's, then the limit and MCU checks
    deviations: list[application.Deviation]
    references: list[ReferenceNote]
    user: str  # the first call's user message
    evidence: EvidenceContext


def wiring(
    design: Design,
    reading: DatasheetReading,
    erc: Sequence[ErcEntry] = (),
    labels: Sequence[Label] = (),
) -> pins.PinLookup:
    """The pin table and wiring findings: pins read from a datasheet
    replace that IC's hand-written table lines, and its pin roles and
    typical application recheck the wiring. With nothing read it is the
    table ``prepare`` built. The review and the with-fix vendor-model runs
    (``fixes``) both use it, so a with-fix run names the review's finding id."""
    return pins.lookup(
        design,
        erc,
        labels,
        datasheet_pins=reading.pins,
        datasheet_roles=reading.roles,
        application=reading.application,
    )


def budgeted_tree(
    design: Design,
    calculations: list[Calculation],
    reading: DatasheetReading,
    battery: power.Range | None = None,
) -> PowerTree:
    """The power tree rebuilt with the datasheets' output and charge
    voltages, and each rail's current budget: the review meets its rails
    against each pin's limits, and the vendor-model simulations run each
    regulator's chain and load from it. ``design`` may be the board with a
    wiring fix applied; ``battery`` is the review form's range, if given."""
    return budget.apply(
        design,
        power.build(design, calculations, reading.limits, battery=battery),
        reading.limits,
        calculations,
    )


def fixes(
    design: Design,
    calculations: list[Calculation],
    reading: DatasheetReading,
    erc: Sequence[ErcEntry] = (),
    labels: Sequence[Label] = (),
    battery: power.Range | None = None,
) -> list[spice_circuits.Fixed]:
    """Each wiring finding with an unambiguous fix, applied to the board
    with its own budgeted tree, for the with-fix vendor-model runs."""
    got = []
    for f in wiring(design, reading, erc, labels).findings:
        if f.fix is None:
            continue
        fixed = roles.apply_fix(design, f.fix)
        tree = budgeted_tree(fixed, calculations, reading, battery)
        got.append(spice_circuits.Fixed(f.id, f.fix, fixed, tree))
    return got


def build(
    *,
    mode: Mode,
    design: Design,
    reading: DatasheetReading,
    symptoms: list[Symptom],
    measurements: list[Measurement],
    references: list[ReferenceNote],
    calculations: list[Calculation],
    erc: Sequence[ErcEntry] = (),
    labels: Sequence[Label] = (),
    battery: power.Range | None = None,
    erc_report: str | None = None,
) -> ReviewContext:
    """The review's input for one board. ``calculations`` are the
    netlist's (``calc.lookup``); the datasheet-limit and microcontroller
    checks are added here, numbered after them. ``erc_report`` is the ERC
    as the model reads it when it came as kicad-cli's text report (an eval
    case) rather than as parsed ``erc`` entries."""
    table = wiring(design, reading, erc, labels)
    tree = budgeted_tree(design, calculations, reading, battery)
    calcs = calculations + voltage.check(
        design, tree, reading.limits, first_id=len(calculations) + 1
    )
    calcs += mcu.check(design, tree, mcu.rules(design, reading.rules), first_id=len(calcs) + 1)
    deviations = application.check(design, reading.application)
    user = prompt.build_user_message(
        mode=mode,
        symptoms=symptoms,
        measurements=measurements,
        netlist=compact(design),
        erc=erc_report if erc_report is not None else format_erc(list(erc)),
        pin_table=table.text,
        findings=pins.format_findings(table.findings),
        warnings=pins.format_warnings(table.warnings),
        references=reference.format_notes(references),
        calculations=calc.format_calculations(calcs),
        datasheet_facts=reading.text,
        deviations=application.format_deviations(deviations) if reading.application else "",
        power=power.format_power(tree),
    )
    evidence = EvidenceContext(
        design=design,
        erc={e.id: e for e in erc},
        pin_data=table.pin_refs,
        symptom_ids=frozenset(s.id for s in symptoms),
        measurement_ids=frozenset(m.id for m in measurements),
        reference_ids=frozenset([n.id for n in references] + [c.id for c in calcs]),
        datasheet_ids=reading.ids | {d.id for d in deviations},
    )
    return ReviewContext(
        reading=reading,
        table=table,
        tree=tree,
        calculations=calcs,
        deviations=deviations,
        references=references,
        user=user,
        evidence=evidence,
    )


def progress_note(ctx: ReviewContext) -> str:
    """The progress line once the input is built: pin data coverage, what
    the datasheets gave, the wiring findings, pin warnings, reference notes
    and calculations."""
    table, reading = ctx.table, ctx.reading
    with_data = sum(1 for ic in table.ics if ic.pin_data)
    conflicts = len(table.findings)
    plural = "s" if conflicts > 1 else ""
    found = f" {conflicts} pin{plural} wired against the datasheet." if conflicts else ""
    n_warn = len(table.warnings)
    if n_warn:
        found += f" {n_warn} transistor{'s' if n_warn > 1 else ''} with a pin order to check."
    n_calcs = len(ctx.calculations)
    flagged = sum(1 for c in ctx.calculations if c.flagged)
    calc_note = (
        f" {n_calcs} value{'s' if n_calcs != 1 else ''} calculated from the netlist"
        + (f", {flagged} flagged." if flagged else ".")
        if n_calcs
        else ""
    )
    notes = f" {len(ctx.references)} reference-design notes." if ctx.references else ""
    read = (
        f" {len(reading.sources)} datasheet{'s' if len(reading.sources) > 1 else ''} read,"
        f" {len(reading.facts)} fact{'' if len(reading.facts) == 1 else 's'}."
        if reading.sources
        else ""
    )
    if ctx.deviations:
        n_dev = len(ctx.deviations)
        read += (
            f" {n_dev} deviation{'s' if n_dev > 1 else ''} from a datasheet's typical application."
        )
    return f"Pin data for {with_data} of {len(table.ics)} ICs.{read}{found}{notes}{calc_note}"


def facts_event(ctx: ReviewContext) -> DatasheetFactsEvent:
    """The datasheet_facts event: what claims can cite, and the power tree,
    wiring findings and calculations rebuilt with what the datasheets said,
    for the browser."""
    return DatasheetFactsEvent(
        power=ctx.tree,
        wiring=ctx.table.findings,
        calculations=ctx.calculations,
        datasheet_facts=[
            DatasheetLine(id=f.id, ref=f.ref, page=f.page, text=f.body) for f in ctx.reading.facts
        ],
        deviations=[
            DatasheetLine(id=d.id, ref=d.ref, page=d.page, text=d.text) for d in ctx.deviations
        ],
        datasheet_failures=[
            DatasheetReadFailure(ref=ref, reason=why) for ref, why in ctx.reading.failed.items()
        ],
    )

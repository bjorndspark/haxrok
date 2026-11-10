"""The debug feature's contract: what the user sends, what the model must
return, what the backend returns after checking it, and the SSE events.

Two layers of output models:

- ``Analysis[Claim]`` is what the model returns. Its JSON schema is sent as a
  strict ``response_format``, so every model here forbids extra fields and has
  no defaults (strict mode requires every property to be present; optional
  values are ``X | None``).
- ``CheckedAnalysis`` is what the UI gets. ``evidence.check`` builds it: each
  claim keeps its evidence label only if every reference resolves against the
  parsed design, otherwise it drops to ``none`` with ``downgraded=True``.

The evidence label says what *kind* of evidence a claim rests on, not that its
conclusion was verified. The UI words it accordingly ("Based on your
schematic", not "Confirmed").
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- shared vocabulary ---------------------------------------------------------

Evidence = Literal["schematic", "erc", "datasheet", "reference", "measurement", "none"]
"""schematic: the netlist. erc: a KiCad ERC entry. datasheet: a fact read from
the part's datasheet (a pin, a limit, its typical application), or the
hand-written pin table where there's none. reference: a manufacturer
reference note or a value the backend calculated with a datasheet formula
(``reference.py``). measurement: a value
the user entered. none: no direct evidence."""

Mode = Literal["debug", "review"]
"""debug: find the schematic cause of bench symptoms. review: look for design
mistakes before the board is ordered; there are no symptoms, only optional
notes on what the board is for."""

Language = Literal["en", "ja"]
"""The language the user reads the answer in. Only the model's prose
follows it; ids, refs, nets and the backend's own check text stay as they are."""

Severity = Literal["will_fail", "may_fail", "improve"]
"""Review mode only. will_fail: the board won't work as drawn. may_fail: it
fails under some conditions (a supply, a load, a tolerance). improve: works,
but a cheap change makes it more robust."""

CiteKind = Literal[
    "pin_on_net", "pin_name", "erc", "pin_function", "datasheet", "reference", "measurement"
]

# Which citation kinds back which evidence label. pin_name is the pin's name
# as the netlist gives it (U2.9 is "L2"); pin_function is what the pin does,
# from the datasheet or the hand-written pin table; datasheet is a limit or
# typical-application fact read from a datasheet ("ds3").
EVIDENCE_CITE_KINDS: dict[Evidence, frozenset[CiteKind]] = {
    "schematic": frozenset({"pin_on_net", "pin_name"}),
    "erc": frozenset({"erc"}),
    "datasheet": frozenset({"pin_function", "datasheet"}),
    "reference": frozenset({"reference"}),
    "measurement": frozenset({"measurement"}),
    "none": frozenset(),
}


class _Strict(BaseModel):
    """Base for model-output types: unknown fields are an error, and the
    generated JSON schema says ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid")


# --- model output ----------------------------------------------------------------


class Cite(_Strict):
    """One reference a claim rests on. Flat rather than a tagged union: strict
    structured output handles flat objects more reliably."""

    kind: CiteKind = Field(
        description="pin_on_net: the pin is on that net in the netlist. pin_name: the pin's "
        "name as the netlist gives it, e.g. U2.9(L2). erc: an ERC entry. pin_function: what "
        "the pin does, from the pin table. datasheet: a datasheet fact (ds1) or a deviation "
        "from a typical application (dv1). reference: a "
        "reference note (rd1) or calculated value (cf1). measurement: a value the user entered."
    )
    pin: str | None = Field(
        description='Pin as REF.PIN, e.g. "U2.9". Required for pin_on_net, pin_name and '
        "pin_function."
    )
    net: str | None = Field(description="Net name exactly as in the netlist. For pin_on_net.")
    id: str | None = Field(
        description='ERC entry id ("erc-3"), datasheet fact or deviation id ("ds4", "dv2"), '
        'reference id ("rd2", "cf1") or measurement id ("m1").'
    )


class Claim(_Strict):
    """A single fact. One fact per claim, so each can be checked on its own."""

    text: str = Field(description="One fact, in plain words a hobbyist understands.")
    evidence: Evidence
    cites: list[Cite] = Field(description="Every pin, net, ERC entry or measurement the text uses.")


class SetupChange(_Strict):
    """A connection change to make before a test is powered up."""

    action: Literal["disconnect", "connect"]
    pin: str = Field(description="REF.PIN being changed.")
    net: str | None = Field(description="disconnect: the net it leaves. connect: a net it joins.")
    to_pin: str | None = Field(description="connect: a REF.PIN it is wired to, instead of a net.")


class Reading(_Strict):
    """One possible outcome of a confirm step and what it would mean."""

    reading: str = Field(description='What the user sees, e.g. "Board boots, +3V3 ≈ 3.3 V".')
    establishes: str = Field(
        description="What this outcome shows, including when it shows nothing."
    )
    supports: list[str] = Field(description="Hypothesis ids this outcome supports.")
    rules_out: list[str] = Field(description="Hypothesis ids this outcome rules out.")


class ConfirmStep(_Strict):
    """A bench test. A step that changes the board must leave the circuit in a
    complete, intended configuration (``setup``), or its result means nothing."""

    id: str = Field(description='Unique, e.g. "h1-c1".')
    purpose: Literal["test_cause", "check_board_matches_schematic"]
    instruction: str
    setup: list[SetupChange] = Field(description="Changes to make before power. Empty if none.")
    tool: Literal["multimeter", "scope", "look", "other"]
    power: Literal["off", "on"]
    practical: bool = Field(description="False if a hobbyist can't do it by hand (e.g. QFN pins).")
    practical_note: str | None
    readings: list[Reading]


class ReworkStep(_Strict):
    """Physical rework, at pin and part level only. The input has no PCB, so
    never traces or vias. Always unverified."""

    text: str
    practical: bool
    practical_note: str | None


class Fix(_Strict):
    """A part fact recalled from memory (a threshold, a reference voltage, a
    recommended circuit) is unverified and never alone makes a part wrong. When
    the cause depends on one, the hypothesis's first confirm step checks it, and
    every change here says to make it only after that check."""

    schematic: list[str] = Field(description="Changes for the next revision of the schematic.")
    rework: list[ReworkStep] = Field(description="Bodges for the board on the bench.")


class Hypothesis[C: Claim](_Strict):
    id: str = Field(description='Unique, e.g. "h1".')
    rank: int = Field(description="1 is the most likely cause (review: the most severe issue).")
    severity: Severity | None = Field(
        description="Review mode only: how badly the issue hurts the board. Null when debugging."
    )
    title: str
    summary: str = Field(description="The mechanism in plain words, 2-4 sentences.")
    claims: list[C]
    explains: list[str] = Field(description="Symptom ids this hypothesis explains.")
    does_not_explain: list[str] = Field(description="Symptom ids it can't explain.")
    confirm: list[ConfirmStep]
    fix: Fix


class OpenQuestion(_Strict):
    text: str
    suggested_check: str | None


class GlossaryEntry(_Strict):
    term: str
    definition: str


class Analysis[C: Claim](_Strict):
    """The full answer. ``Analysis[Claim]`` is the model's output schema."""

    hypotheses: list[Hypothesis[C]]
    open_questions: list[OpenQuestion] = Field(description="What the input can't answer.")
    glossary: list[GlossaryEntry] = Field(description="Jargon used above, defined plainly.")


# --- datasheet facts (model output, one call per datasheet) -----------------------


class PinFact(_Strict):
    """What one pin of the board's package does, per the datasheet."""

    pin: str = Field(description="The pin number on the board's package, as listed to you.")
    name: str = Field(description='The datasheet\'s name for the pin, e.g. "BOOT0".')
    function: str = Field(description="What the pin does, in one short sentence.")
    page: int = Field(description="The page the pin is described on (=== Page N ===).")


class LimitFact(_Strict):
    """One threshold or limit a schematic review needs: absolute maximums,
    operating ranges, thresholds, reset and start-up times."""

    parameter: str = Field(description='What is limited, e.g. "VDD supply voltage".')
    min: float | None
    typ: float | None
    max: float | None
    unit: str = Field(description='"V", "mA", "ms", "kΩ", …')
    conditions: str = Field(description='Conditions as printed, e.g. "TA = 25 °C"; "" if none.')
    page: int


AppPartKind = Literal["resistor", "capacitor", "inductor", "diode", "crystal", "other"]


class AppPart(_Strict):
    """One part of the datasheet's typical application circuit."""

    role: str = Field(description='What the part is for, e.g. "input capacitor".')
    kind: AppPartKind
    value: str = Field(description='Value as printed, e.g. "10 µF", "100 kΩ"; "" if not given.')
    pins: list[str] = Field(
        description="The IC's pin numbers (board package) the part connects to, or [] if "
        "it connects to none directly."
    )
    page: int
    min: float | None = Field(
        description="The smallest value the datasheet allows for this part, in unit; null "
        "if it prints none (a typical value alone is not a minimum)."
    )
    max: float | None = Field(description="The largest value it allows, in unit; or null.")
    unit: str = Field(
        description='The unit of min and max as printed, e.g. "µF", "kΩ", "µH"; "" if both '
        "are null."
    )
    range_page: int | None = Field(
        description="The page min and max are printed on (it may be another page than the "
        "circuit, e.g. a recommended-components table); null if both are null."
    )
    required: bool = Field(
        description="false when the datasheet calls the part optional, or it only serves "
        "a feature a board may not use (a power-good pull-up, a low-speed crystal, a "
        "soft-start or feed-forward capacitor); true otherwise."
    )


class DatasheetFacts(_Strict):
    """Everything one model call reads from one datasheet."""

    pins: list[PinFact]
    limits: list[LimitFact]
    application: list[AppPart]


# --- checked output ----------------------------------------------------------------


class CheckedClaim(Claim):
    """A claim after ``evidence.check``. When ``downgraded`` is true,
    ``evidence`` is ``none`` and ``claimed_evidence`` holds the model's label."""

    downgraded: bool = False
    claimed_evidence: Evidence | None = None
    reason: str | None = Field(None, description="Why it was downgraded, for the UI.")


class Problem(BaseModel):
    """A reference the checker removed or couldn't resolve outside a claim,
    e.g. an unknown symptom id in ``explains``. ``where`` is a hypothesis or
    confirm-step id, so the UI can show it in place."""

    where: str
    message: str


class DiagramPin(BaseModel):
    pin: str = Field(description="REF.PIN of the diagram's IC.")
    name: str = Field(description="The pin's name in the netlist, e.g. L2.")
    net: str
    flagged: bool = Field(
        description="A check proved this pin wired against its datasheet function."
    )


class DiagramMember(BaseModel):
    """Another part's pin on a net: an IC pin or an inductor."""

    pin: str
    name: str
    inductor: bool


class DiagramNet(BaseModel):
    name: str
    members: list[DiagramMember]
    more: int = Field(description="Other pins on the net that aren't listed.")


class Diagram(BaseModel):
    """A hypothesis's IC, its cited pins and the nets they are on, from the
    netlist (``diagram.build``). The UI draws it."""

    ic: str
    value: str
    pins: list[DiagramPin]
    nets: list[DiagramNet]


class CheckedAnalysis(Analysis[CheckedClaim]):
    problems: list[Problem] = []
    diagrams: dict[str, Diagram] = Field(
        {}, description="Hypothesis id -> its wiring diagram, for those citing an IC pin."
    )


# --- design summary (first SSE event) ----------------------------------------------


class IcSummary(BaseModel):
    ref: str
    value: str
    footprint: str
    pin_data: bool


class DatasheetStatus(BaseModel):
    """Whether one IC's datasheet PDF was found (``datasheet.find``). Found
    PDFs are kept on disk by part number, so a later run reads them from
    there."""

    ref: str
    value: str
    status: Literal["found", "not_found", "skipped"]
    source: Literal["schematic", "lcsc"] | None = Field(
        None, description="Where the PDF came from: the schematic's URL or LCSC's listing."
    )
    url: str | None = Field(None, description="The PDF's URL; None unless found.")
    part_number: str | None = Field(None, description="The LCSC number or MPN, if any.")
    reason: str = Field("", description="Why not found or skipped, in a few words.")
    # The PDF on the server, for the extraction step. Not sent to the browser.
    path: str = Field("", exclude=True)


class SpiceModelStatus(BaseModel):
    """One IC's SPICE model and which simulator it's written for
    (``spice_models.find``). ``ngspice`` is a guess from the text's syntax;
    ``none`` means no model was found, ``skipped`` that nothing could be
    looked up."""

    ref: str
    value: str
    status: Literal["ngspice", "pspice", "ltspice", "encrypted", "none", "skipped"]
    source: Literal["upload", "web"] | None = Field(
        None,
        description="Where the model came from: the uploaded file the symbol's Sim.Library "
        "names, or a model link on the part's product page.",
    )
    url: str | None = Field(None, description="The model's download URL; None unless from the web.")
    file: str | None = Field(None, description="The model file's name.")
    subckt: str | None = Field(None, description="The subcircuit or model name to use.")
    part_number: str | None = Field(None, description="The MPN looked up, if any.")
    reason: str = Field(
        "", description="Why none or skipped, or a note on the model used, in a few words."
    )
    loads: bool | None = Field(
        None,
        description="Whether the subcircuit loads in ngspice (in its PSpice or LTspice "
        "compatibility mode); None when nothing was checked or ngspice isn't installed. "
        "A failure's ngspice error is in reason.",
    )
    # The kept model file on the server, for the simulation step; "" for an
    # uploaded one, which isn't kept. Not sent to the browser.
    path: str = Field("", exclude=True)


class ErcEntry(BaseModel):
    """One KiCad ERC violation. ``id`` is what claims cite."""

    id: str
    sheet: str
    rule: str
    severity: str
    message: str
    items: list[str]
    # The items' schematic uuids, to find a label the entry names. Not sent
    # to the browser.
    uuids: list[str] = Field(default_factory=list, exclude=True)


class WiringFinding(BaseModel):
    """A pin wired against its pin-table function, found by
    ``pins.check_wiring`` before the model runs. Verified: it follows from the
    netlist and the pin table alone. ``id`` (wf1, …) is how the model's input
    names it."""

    id: str
    pin: str = Field(description="REF.PIN, e.g. U2.9.")
    pin_name: str = Field(description="The datasheet's pin name, e.g. L2.")
    net: str
    conflicts: list[str] = Field(description="REF.PIN refs on the net that shouldn't be.")
    inductors: list[str] = Field(description="Inductor pins on the net, which belong there.")
    nets: list[str] = Field(description="Every net the text names, for the browser to mark.")
    text: str


class PinWarning(BaseModel):
    """A transistor whose symbol pin order breaks the common SOT-23 pinout,
    found by ``pins.check_convention``. Unverified: a convention, not the
    part's datasheet. ``id`` (pw1, …) is how the model's input names it."""

    id: str
    ref: str
    pins: list[str] = Field(description="REF.PIN refs whose net would land on another pad.")
    nets: list[str] = Field(description="Every net the text names, for the browser to mark.")
    text: str


class ReferenceNote(BaseModel):
    """A fact from a manufacturer's reference design, datasheet or app note
    (``reference.REFERENCES``). ``id`` (rd1, …) is what claims cite."""

    id: str
    part: str
    source: str
    text: str


TimeUnit = Literal["ns", "µs", "ms"]


class SimLevel(BaseModel):
    """A horizontal reference line on a simulation plot."""

    v: float
    label: str
    danger: bool = Field(description="A limit the waveform shouldn't cross.")


SimAnalysis = Literal["startup", "load_step", "dropout"]
"""What a vendor-model simulation checks (``spice_circuits``)."""

SimVerdict = Literal["passed", "flagged", "timed_out", "failed", "skipped"]
"""A vendor-model simulation's outcome: within the datasheet limits, outside
them, stopped at the timeout, ngspice failed, or not run (the text says why).
A ramped RC run is only ever ``passed`` or ``flagged``."""


class SimRamp(BaseModel):
    """The regulator an enable/reset RC's rail comes from (plan 003 step
    4). With its datasheet start-up time the rail ramps up over it and the
    run checks that the pin releases only once the rail is in band; without
    one (``t_s`` None) the rail is a step at t = 0 and the caption says
    why."""

    regulator: str = Field(max_length=40)
    t_s: float | None = Field(
        default=None, gt=0, le=1.0, description="Start-up time from its datasheet facts."
    )
    accuracy: float = Field(gt=0, lt=0.5, description="Output accuracy, as a fraction.")
    accuracy_cited: bool = Field(description="False: ±5 % assumed, no datasheet value.")


class SimCircuit(BaseModel):
    """The RC behind an RC simulation, so a what-if can rebuild its deck
    (``RerunRequest``): a resistor from an ideal rail, a capacitor to
    ground. The refs are set where the netlist has a single part; an I²C
    line's capacitance is an estimate and has none. An enable/reset RC fed
    by a regulator carries its ``ramp``, so a what-if ramps the same way."""

    kind: Literal["i2c", "rc_delay"]
    net: str = Field(max_length=200)
    r_ohm: float
    c_farad: float
    v_rail: float
    v_supply: float | None = Field(
        default=None, description="I²C: the lowest supply of an IC on the line."
    )
    r_ref: str | None = Field(default=None, max_length=40)
    c_ref: str | None = Field(default=None, max_length=40)
    ramp: SimRamp | None = None


class Simulation(BaseModel):
    """An ngspice transient, streamed as its own ``simulation`` event. The
    review never sees it, but the user can ask about it (``AskRequest``)
    and, for an RC run, re-run it with other values (``RerunRequest``).

    Two kinds: an RC node rising from 0 V (``spice.py``: a flagged I²C line
    through its pull-up and estimated bus capacitance, or an enable/reset
    pin through its RC delay), with ``calc_id`` set; or a regulator's
    circuit run with its vendor model (``spice_circuits.py``), with ``ref``,
    ``analysis`` and ``verdict`` set, the backend's own finding checked
    against the datasheet limits. ``skipped`` says why a regulator whose
    model loads wasn't simulated (``text``); it has no samples. An RC run
    whose rail ramps up (``circuit.ramp.t_s``) has a ``verdict`` too:
    whether the pin releases after the rail is in band."""

    id: str = Field(max_length=20, description="sim1, … (RC); vm1, … (vendor model).")
    calc_id: str | None = Field(
        default=None, max_length=20, description="The Calculation it simulates."
    )
    ref: str | None = Field(default=None, max_length=40, description="The modelled IC.")
    analysis: SimAnalysis | None = None
    verdict: SimVerdict | None = None
    title: str = Field(max_length=200)
    deck: str = Field(max_length=4000, description="The ngspice netlist that was run.")
    t: list[float] = Field(max_length=500, description="Sample times, in t_unit.")
    t_unit: TimeUnit
    v: list[float] = Field(max_length=500)
    levels: list[SimLevel] = Field(max_length=10)
    text: str = Field(max_length=2000)
    circuit: SimCircuit | None = Field(
        default=None, description="RC runs only: what a what-if re-run rebuilds."
    )


class Calculation(BaseModel):
    """A value or wiring check the backend computed from the netlist
    (``calc.py``): a regulator's output or headroom, a charge current, an LED
    current, an I²C pull-up, an RC delay, a divider, USB-C CC pull-downs,
    supply decoupling, a UART's TX/RX wiring, a regulator's enable or an
    STM32's BOOT0, a pull-up above an IC's supply. Verified, not the model's.
    ``flagged`` means the netlist as drawn contradicts itself or misses what
    every such circuit needs (+3V3 set to 0.73 V, an I²C line with no
    pull-up, CC1 and CC2 sharing one resistor, a supply rail with no
    capacitor, TX wired to TX, an enable pin on its own output, a BOOT0 with
    no path to ground)."""

    id: str
    kind: Literal[
        "regulator_output",
        "charge_current",
        "led_current",
        "i2c_pullup",
        "rc_delay",
        "divider",
        "usb_cc",
        "decoupling",
        "dropout",
        "uart",
        "enable_pin",
        "overvoltage_pullup",
    ]
    ic: str
    pins: list[str] = Field(description="REF.PIN of the IC pin and the resistors used.")
    value: float | None = Field(description="None when the thing to measure is missing.")
    unit: str
    expected: float | None = Field(description="The value the net name promises, if any.")
    flagged: bool
    text: str


class Symptom(BaseModel):
    id: str
    text: str


class Measurement(BaseModel):
    """A bench value the user entered. ``context`` is the confirm step it
    answers, as text, since the model doesn't see earlier runs."""

    id: str
    step_id: str | None
    context: str | None
    value: str


class DatasheetLine(BaseModel):
    """A fact read from a datasheet (ds1, …) or a deviation from its typical
    application (dv1, …), as cited by a claim with ``datasheet`` evidence."""

    id: str
    ref: str = Field(description="The IC whose datasheet it comes from.")
    page: int = Field(description="The datasheet page that says so.")
    text: str


class DatasheetReadFailure(BaseModel):
    """An IC whose datasheet was found but couldn't be read."""

    ref: str
    reason: str


PowerSourceKind = Literal["usb", "battery", "input"]
PowerPartKind = Literal["regulator", "switch", "charger", "pass"]
# Where a rail's voltage comes from: its net's name, a feedback-divider
# calculation, the part's value (AMS1117-3.3), the datasheet's output
# voltage, the parts feeding it, USB's 5 V, a chemistry default, the user's
# value on the review form, or nothing found.
VoltageBasis = Literal[
    "named",
    "calculated",
    "fixed",
    "datasheet",
    "upstream",
    "usb",
    "charger",
    "default",
    "user",
    "unknown",
]


class PowerPart(BaseModel):
    """A part that makes a rail from other rails: a regulator, a power mux
    or load switch (more than one input), a battery charger, or a pass
    part (fuse, ferrite, 0 Ω, diode, slide switch)."""

    ref: str
    value: str
    kind: PowerPartKind
    inputs: list[str] = Field(description="The rails it takes power from.")
    output: str


class PowerRail(BaseModel):
    """A power net, its voltage range and where that comes from, the parts
    that make it and the ICs it supplies. A source (``source`` set) is a
    root: USB VBUS, a battery, or an input nothing on the board makes."""

    net: str
    source: PowerSourceKind | None = None
    min: float | None = Field(description="Volts; equal to max for a fixed rail.")
    max: float | None
    basis: VoltageBasis
    inferred: bool = Field(description="A guess the user should check, not a fact.")
    note: str = Field(description='Where the voltage comes from, e.g. "U5 feedback (cf1)".')
    fed_by: list[str] = Field(description="Refs of the parts that make it.")
    loads: list[str] = Field(description="ICs with a supply pin on it.")


class PowerTree(BaseModel):
    """The board's power tree, built from the netlist (``power.build``):
    rails roots first, and the parts between them."""

    rails: list[PowerRail]
    parts: list[PowerPart]
    battery_override: bool = Field(
        default=False, description="The battery's range is the user's, from the review form."
    )


class DesignSummary(BaseModel):
    """Parsed input, shown before the model call: lets the user check the
    parse, and shows which ICs have pin data."""

    mode: Mode
    project: str
    sheets: int
    parts: int
    nets: int
    ics: list[IcSummary]
    # Defaults to empty: /ask-result gets summaries back from the browser,
    # including runs from before datasheets were looked up.
    datasheets: list[DatasheetStatus] = Field(
        default_factory=list, description="One per IC, in the order of ``ics``."
    )
    spice_models: list[SpiceModelStatus] = Field(
        default_factory=list, description="One per IC, in the order of ``ics``."
    )
    erc: list[ErcEntry]
    wiring: list[WiringFinding]
    pin_warnings: list[PinWarning]
    references: list[ReferenceNote]
    calculations: list[Calculation]
    symptoms: list[Symptom] = Field(
        description="Bench symptoms; in review mode, the user's notes on what the board is for."
    )
    measurements: list[Measurement]
    # Filled from the datasheet_facts event once the datasheets are read;
    # the browser merges it into the summary, so /ask-result gets it back.
    datasheet_facts: list[DatasheetLine] = Field(default_factory=list)
    deviations: list[DatasheetLine] = Field(default_factory=list)
    datasheet_failures: list[DatasheetReadFailure] = Field(default_factory=list)
    # Built from the netlist; rebuilt with the datasheet facts and sent in
    # the datasheet_facts event. None in runs from before it existed.
    power: PowerTree | None = None


# --- input ----------------------------------------------------------------------


class SymptomsInput(BaseModel):
    """The bench symptoms as one free-text box. The backend numbers each
    non-empty line as a symptom (s1, s2, …) so hypotheses can cite them, so
    each line should stand on its own ("Fails on battery", not "battery")."""

    text: str = Field(..., min_length=1, max_length=8000)


class MeasurementInput(BaseModel):
    """A value the user entered under a confirm step, sent on re-runs."""

    step_id: str | None = Field(None, max_length=50)
    context: str | None = Field(None, max_length=1000, description="The step's instruction.")
    value: str = Field(..., min_length=1, max_length=500)


# --- SSE events -------------------------------------------------------------------
#
# Order: design_summary, then any number of progress and simulation events
# (simulations run in parallel with the model) and, when a datasheet was found,
# one datasheet_facts once they're read; then exactly one terminal event,
# result or error, sent once those simulations are done; then the vendor-model
# simulation events, each as it finishes. Parsing and ERC run before the
# stream opens, so their failures come back as an ordinary error response
# instead.

ProgressStage = Literal["pin_lookup", "reasoning", "structuring", "checking"]


class ProgressEvent(BaseModel):
    stage: ProgressStage
    message: str


class ErrorEvent(BaseModel):
    message: str


class DatasheetFactsEvent(BaseModel):
    """What the found datasheets said, sent before the review: the facts and
    deviations claims cite, and the ICs whose read failed."""

    datasheet_facts: list[DatasheetLine]
    deviations: list[DatasheetLine]
    datasheet_failures: list[DatasheetReadFailure]
    power: PowerTree | None = Field(
        default=None, description="The power tree again, with the datasheets' voltages."
    )


# --- ask --------------------------------------------------------------------------------


class AskTurn(BaseModel):
    """An earlier question about the same simulation and its answer."""

    question: str = Field(max_length=1000)
    answer: str = Field(max_length=8000)


class AskRequest(BaseModel):
    """A question about one simulation. Runs live in the browser, so the
    simulation, its latest what-if re-run if any, and the conversation so
    far come with every question."""

    simulation: Simulation
    what_if: Simulation | None = None
    question: str = Field(min_length=1, max_length=1000)
    history: list[AskTurn] = Field(default_factory=list, max_length=10)
    language: Language = "en"


class RerunRequest(BaseModel):
    """A what-if: an RC simulation re-run with another R and C. Stateless
    like ``AskRequest``: the browser sends the original simulation back,
    and its ``circuit`` rebuilds the deck."""

    simulation: Simulation
    r_ohm: float
    c_farad: float


class ResultAskRequest(BaseModel):
    """A follow-up question about a finished run: the design summary and the
    checked result it produced. Nothing is stored, so both and the
    conversation so far come with every question, as with ``AskRequest``."""

    summary: DesignSummary
    result: CheckedAnalysis
    question: str = Field(min_length=1, max_length=1000)
    history: list[AskTurn] = Field(default_factory=list, max_length=10)
    language: Language = "en"


class AskAnswer(BaseModel):
    answer: str

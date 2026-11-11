import type { Language } from "@/shared/lib/language"

/** Wire shapes mirroring backend app/features/debug/schemas.py. */

/** debug: bench symptoms -> causes. review: pre-fab design review, no symptoms. */
export type Mode = "debug" | "review"

/** Review mode only: how badly an issue hurts the board. */
export type Severity = "will_fail" | "may_fail" | "improve"

export type Evidence = "schematic" | "erc" | "datasheet" | "reference" | "measurement" | "none"
export type CiteKind =
  "pin_on_net" | "pin_name" | "erc" | "pin_function" | "datasheet" | "reference" | "measurement"

export interface Cite {
  kind: CiteKind
  pin: string | null
  net: string | null
  id: string | null
}

export interface CheckedClaim {
  text: string
  evidence: Evidence
  cites: Cite[]
  downgraded: boolean
  claimed_evidence: Evidence | null
  reason: string | null
}

export interface SetupChange {
  action: "disconnect" | "connect"
  pin: string
  net: string | null
  to_pin: string | null
}

export interface Reading {
  reading: string
  establishes: string
  supports: string[]
  rules_out: string[]
}

export interface ConfirmStep {
  id: string
  purpose: "test_cause" | "check_board_matches_schematic"
  instruction: string
  setup: SetupChange[]
  tool: "multimeter" | "scope" | "look" | "other"
  power: "off" | "on"
  practical: boolean
  practical_note: string | null
  readings: Reading[]
}

export interface ReworkStep {
  text: string
  practical: boolean
  practical_note: string | null
}

export interface Hypothesis {
  id: string
  rank: number
  /** Null when debugging. */
  severity: Severity | null
  title: string
  summary: string
  claims: CheckedClaim[]
  explains: string[]
  does_not_explain: string[]
  confirm: ConfirmStep[]
  fix: { schematic: string[]; rework: ReworkStep[] }
}

export interface OpenQuestion {
  text: string
  suggested_check: string | null
}

export interface GlossaryEntry {
  term: string
  definition: string
}

export interface Problem {
  where: string
  message: string
}

export interface DiagramPin {
  pin: string
  name: string
  net: string
  flagged: boolean
}

export interface DiagramMember {
  pin: string
  name: string
  inductor: boolean
}

export interface DiagramNet {
  name: string
  members: DiagramMember[]
  more: number
}

/** A hypothesis's IC, its cited pins and their nets, built from the
 *  netlist by the backend (diagram.build). */
export interface Diagram {
  ic: string
  value: string
  pins: DiagramPin[]
  nets: DiagramNet[]
}

export interface CheckedAnalysis {
  hypotheses: Hypothesis[]
  open_questions: OpenQuestion[]
  glossary: GlossaryEntry[]
  problems: Problem[]
  /** Hypothesis id -> its diagram; missing when it cites no IC pin. */
  diagrams: Partial<Record<string, Diagram>>
}

export interface IcSummary {
  ref: string
  value: string
  footprint: string
  pin_data: boolean
}

export interface ErcEntry {
  id: string
  sheet: string
  rule: string
  severity: string
  message: string
  items: string[]
}

/** A pin wired against its role (datasheet, pin table or pin name), found by
 *  the backend before the model runs (roles.check, pins.check_pinout).
 *  Verified, not the model's reasoning. */
export interface WiringFinding {
  id: string
  pin: string
  pin_name: string
  net: string
  conflicts: string[]
  inductors: string[]
  /** Every net the text names. Absent in runs recorded before it existed. */
  nets?: string[]
  text: string
  /** The unambiguous rewiring, when the rule gives one (plan 006 step 3). */
  fix?: WiringFix | null
}

/** A wiring finding's fix: its pins leave one net and join another (a new
 *  one when `new_net`). The vendor-model runs it touches run again with it. */
export interface WiringFix {
  /** REF.PIN refs that move, the finding's pin first. */
  pins: string[]
  leaves: string
  joins: string
  new_net: boolean
  text: string
}

/** A transistor whose symbol pin order breaks the common SOT-23 pinout
 *  (pins.check_convention). Unverified: a convention, not its datasheet. */
export interface PinWarning {
  id: string
  ref: string
  pins: string[]
  /** Every net the text names. Absent in runs recorded before it existed. */
  nets?: string[]
  text: string
}

/** A fact from a manufacturer's reference design or datasheet (rd1, …). */
export interface ReferenceNote {
  id: string
  part: string
  source: string
  text: string
}

/** A horizontal reference line on a simulation plot. */
export interface SimLevel {
  v: number
  label: string
  /** A limit the waveform shouldn't cross. */
  danger: boolean
}

/** What a vendor-model simulation checks. */
export type SimAnalysis = "startup" | "load_step" | "dropout"

/** A vendor-model simulation's outcome against the datasheet limits;
 *  `skipped` has no samples and its text says why. */
export type SimVerdict = "passed" | "flagged" | "timed_out" | "failed" | "skipped"

/** An RC simulation's circuit, so the user can re-run it with their own
 *  R and C (a what-if). The rail is fixed. */
export interface SimCircuit {
  kind: "i2c" | "rc_delay"
  net: string
  r_ohm: number
  c_farad: number
  v_rail: number
  /** I²C: the lowest supply of an IC on the line. */
  v_supply: number | null
  /** The part, when the netlist has a single one; an I²C bus C has none. */
  r_ref: string | null
  c_ref: string | null
  /** An enable/reset RC fed by a regulator: how its rail comes up. */
  ramp?: SimRamp | null
}

/** The regulator an enable/reset RC's rail comes from. With a start-up
 *  time the rail ramps over it and the run gets a verdict; without one
 *  (`t_s` null) the rail is a step at t = 0. */
export interface SimRamp {
  regulator: string
  t_s: number | null
  /** Output accuracy, as a fraction. */
  accuracy: number
  /** False: ±5 % assumed, no datasheet value. */
  accuracy_cited: boolean
}

/** An ngspice transient, streamed as it finishes; the user can ask about
 *  it. Either an RC node rising from 0 V (a flagged I²C line, or an
 *  enable/reset pin's RC delay, with a `verdict` when its rail ramps), with
 *  `calc_id`; or a regulator's circuit run with its vendor model, with
 *  `ref`, `analysis` and `verdict`, which arrives after the result. */
export interface Simulation {
  /** sim1, … (RC); vm1, … (vendor model). */
  id: string
  /** The Calculation it simulates (RC only). */
  calc_id: string | null
  /** The modelled IC (vendor model only). */
  ref?: string | null
  analysis?: SimAnalysis | null
  verdict?: SimVerdict | null
  title: string
  /** The ngspice netlist that was run. */
  deck: string
  /** Sample times, in t_unit. */
  t: number[]
  t_unit: "ns" | "µs" | "ms"
  v: number[]
  levels: SimLevel[]
  text: string
  /** RC runs only: what a what-if re-run needs. */
  circuit?: SimCircuit | null
  /** Vendor model only: each ammeter's peak, switch pins then the input. */
  peaks?: SimPeak[]
  /** A with-fix run: the as-built run it pairs with, and the finding. */
  fix_of?: string | null
  finding?: string | null
}

/** One ammeter's peak current in a vendor-model run. */
export interface SimPeak {
  /** The ammeter, "l2" or "in": the same in an as-built run and its with-fix run. */
  name: string
  /** "into U5.9 (L2) on +3V3", "from the input". */
  label: string
  a: number
  t_s: number
}

/** Re-run an RC simulation with the user's R and C. */
export interface RerunRequest {
  simulation: Simulation
  r_ohm: number
  c_farad: number
}

/** A value the backend computed from the netlist with a datasheet formula
 *  (cf1, …). `flagged`: it contradicts the design's intent. */
export interface Calculation {
  id: string
  kind:
    | "regulator_output"
    | "charge_current"
    | "led_current"
    | "i2c_pullup"
    | "rc_delay"
    | "divider"
    | "usb_cc"
    | "decoupling"
    | "dropout"
    | "uart"
    | "enable_pin"
    | "overvoltage_pullup"
    | "pin_voltage"
    | "mcu_pin"
  ic: string
  pins: string[]
  /** Null when the thing to measure is missing (no pull-up, no resistor). */
  value: number | null
  unit: string
  expected: number | null
  flagged: boolean
  text: string
}

export interface Symptom {
  id: string
  text: string
}

export interface Measurement {
  id: string
  step_id: string | null
  context: string | null
  value: string
}

export interface MeasurementInput {
  step_id: string | null
  context: string | null
  value: string
}

/** Where an IC's datasheet came from, or why there is none. */
export interface DatasheetStatus {
  ref: string
  value: string
  status: "found" | "not_found" | "skipped"
  source: string | null
  url: string | null
  part_number: string | null
  reason: string | null
}

/** An IC's SPICE model, its format and whether ngspice loads it. */
export interface SpiceModelStatus {
  ref: string
  value: string
  status: "ngspice" | "pspice" | "ltspice" | "encrypted" | "none" | "skipped"
  source: string | null
  url: string | null
  file: string | null
  subckt: string | null
  part_number: string | null
  reason: string | null
  /** Null when it wasn't tried. */
  loads: boolean | null
}

/** A fact read from a datasheet (ds1, …) or a deviation from its typical
 *  application (dv1, …), as a datasheet claim cites it. */
export interface DatasheetLine {
  id: string
  /** The IC whose datasheet it comes from. */
  ref: string
  page: number
  text: string
}

/** An IC whose datasheet was found but couldn't be read. */
export interface DatasheetReadFailure {
  ref: string
  reason: string
}

/** A part on a power path: a regulator, mux/load switch, charger, or a
 *  pass part (fuse, ferrite, 0 Ω link, diode, slide switch). */
export interface PowerPart {
  ref: string
  value: string
  kind: "regulator" | "switch" | "charger" | "pass"
  /** Its input rails; a mux has more than one. */
  inputs: string[]
  output: string
}

/** Where a rail's voltage came from. */
export type VoltageBasis =
  | "named"
  | "calculated"
  | "fixed"
  | "datasheet"
  | "upstream"
  | "usb"
  | "charger"
  | "default"
  | "user"
  | "unknown"

/** What one part draws from a rail (budget.apply). */
export interface RailLoad {
  ref: string
  /** Milliamps; null when its datasheet gives no figure. */
  ma: number | null
  /** Where the figure came from, e.g. "datasheet p.27". */
  note: string
}

/** A rail's load against the rated current of the part making it, filled
 *  once the datasheets are read. Over budget may fail; it isn't verified. */
export interface RailBudget {
  /** The sum of the known loads; a floor when `unknown` isn't empty. */
  load_ma: number
  /** Refs with no figure, here or on a rail below. */
  unknown: string[]
  loads: RailLoad[]
  rated_ma: number | null
  /** Where the rating came from, e.g. "U5 datasheet p.1". */
  rated_note: string
  over: boolean
}

/** A rail of the power tree (power.build). */
export interface PowerRail {
  net: string
  /** Set at a root: USB, a battery, or another input connector. */
  source: "usb" | "battery" | "input" | null
  /** Volts; both null when unknown, equal when nominal. */
  min: number | null
  max: number | null
  basis: VoltageBasis
  /** A guess to check, not a fact. */
  inferred: boolean
  /** Where the voltage comes from, e.g. "U5 feedback (cf1)". */
  note: string
  /** Refs of the parts that make it, and of the parts that draw from it. */
  fed_by: string[]
  loads: string[]
  budget?: RailBudget | null
}

/** A rail that's down in an operating state but may be fed backwards from
 *  a live one: may fail, not verified. */
export interface BackPower {
  rail: string
  /** The parts and pins it flows through, e.g. ["R17", "U2.5"]. */
  via: string[]
  text: string
}

/** Which rails are up from one combination of the sources (states.walk). */
export interface OperatingState {
  sources: string[]
  up: string[]
  down: string[]
  /** Parts held off by their enable, or taken as on with nothing on it. */
  off: string[]
  back_powered: BackPower[]
}

/** Each rail, the part that makes it and what it supplies, built from the
 *  netlist by the backend before the model runs. */
export interface PowerTree {
  rails: PowerRail[]
  parts: PowerPart[]
  /** The battery's range came from the review form, not inferred. */
  battery_override: boolean
  /** Missing from runs saved before operating states existed. */
  states?: OperatingState[]
}

/** The datasheet_facts event: merged into the run's summary. */
export interface DatasheetFacts {
  datasheet_facts: DatasheetLine[]
  deviations: DatasheetLine[]
  datasheet_failures: DatasheetReadFailure[]
  /** Rebuilt with the datasheets' output voltages. Absent in older runs. */
  power?: PowerTree | null
  /** Rechecked with the datasheets' pin roles; replaces the summary's.
   *  Absent in older runs, which keep the summary's. */
  wiring?: WiringFinding[]
  /** With each IC pin's rail checked against its datasheet limits;
   *  replaces the summary's. Absent in older runs. */
  calculations?: Calculation[]
}

export interface DesignSummary {
  mode: Mode
  project: string
  sheets: number
  parts: number
  nets: number
  ics: IcSummary[]
  erc: ErcEntry[]
  wiring: WiringFinding[]
  pin_warnings: PinWarning[]
  references: ReferenceNote[]
  calculations: Calculation[]
  /** Bench symptoms; in review mode, the designer's notes on the board. */
  symptoms: Symptom[]
  measurements: Measurement[]
  /** One per IC. Absent in runs recorded before they existed. */
  datasheets?: DatasheetStatus[]
  spice_models?: SpiceModelStatus[]
  /** From the datasheet_facts event; absent until it arrives. */
  datasheet_facts?: DatasheetLine[]
  deviations?: DatasheetLine[]
  datasheet_failures?: DatasheetReadFailure[]
  /** Absent in runs recorded before it existed. */
  power?: PowerTree | null
}

export type ProgressStage = "pin_lookup" | "reasoning" | "structuring" | "checking"

export interface ProgressEvent {
  stage: ProgressStage
  message: string
}

export type AnalysisEvent =
  | { type: "design_summary"; summary: DesignSummary }
  | { type: "progress"; progress: ProgressEvent }
  | { type: "datasheet_facts"; facts: DatasheetFacts }
  | { type: "simulation"; simulation: Simulation }
  | { type: "result"; result: CheckedAnalysis }
  | { type: "error"; message: string }

/** A schematic file to upload, with its path inside the dropped folder. */
export interface ProjectFile {
  path: string
  file: Blob
}

/** One analysis run, kept on the page after later runs (newest first). */
export interface Run {
  number: number
  trigger: string
  startedAt: number
  finishedAt: number | null
  summary: DesignSummary | null
  stage: ProgressStage | null
  result: CheckedAnalysis | null
  error: string | null
  /** The stream is open: until the result, and after it while the
   *  vendor-model simulations run. */
  streaming: boolean
  /** In the order they finished. */
  simulations: Simulation[]
  /** Questions asked about each simulation, by simulation id. */
  chats: Record<string, AskTurn[]>
  /** Each simulation's latest what-if re-run, by simulation id. */
  whatIfs: Record<string, Simulation>
  /** Follow-up questions asked about the finished run. */
  resultChat: AskTurn[]
}

/** A question about a simulation and the model's answer. */
export interface AskTurn {
  question: string
  answer: string
}

export interface AskRequest {
  simulation: Simulation
  /** The what-if drawn over it, if any; the model compares both. */
  what_if?: Simulation | null
  question: string
  history: AskTurn[]
  /** The language the answer is written in. */
  language: Language
}

/** A follow-up question about a finished run; the summary and result go
 *  back with it, since the server keeps nothing. */
export interface ResultAskRequest {
  summary: DesignSummary
  result: CheckedAnalysis
  question: string
  history: AskTurn[]
  language: Language
}

export interface AskAnswer {
  answer: string
}

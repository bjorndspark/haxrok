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

/** A pin wired against its pin-table function, found by the backend before
 *  the model runs (pins.check_wiring). Verified, not the model's reasoning. */
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

/** An ngspice transient, streamed as it finishes; the user can ask about
 *  it. Either an RC node rising from 0 V (a flagged I²C line, or an
 *  enable/reset pin's RC delay), with `calc_id`; or a regulator's circuit
 *  run with its vendor model, with `ref`, `analysis` and `verdict`, which
 *  arrives after the result. */
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

/** The datasheet_facts event: merged into the run's summary. */
export interface DatasheetFacts {
  datasheet_facts: DatasheetLine[]
  deviations: DatasheetLine[]
  datasheet_failures: DatasheetReadFailure[]
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

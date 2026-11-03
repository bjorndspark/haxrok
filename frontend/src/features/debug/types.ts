/** Wire shapes mirroring backend app/features/debug/schemas.py. */

export type Evidence = "schematic" | "erc" | "datasheet" | "measurement" | "none"
export type CiteKind = "pin_on_net" | "pin_name" | "erc" | "pin_function" | "measurement"

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

export interface CheckedAnalysis {
  hypotheses: Hypothesis[]
  open_questions: OpenQuestion[]
  glossary: GlossaryEntry[]
  problems: Problem[]
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

export interface DesignSummary {
  project: string
  sheets: number
  parts: number
  nets: number
  ics: IcSummary[]
  erc: ErcEntry[]
  wiring: WiringFinding[]
  symptoms: Symptom[]
  measurements: Measurement[]
}

export type ProgressStage = "pin_lookup" | "reasoning" | "structuring" | "checking"

export interface ProgressEvent {
  stage: ProgressStage
  message: string
}

export type AnalysisEvent =
  | { type: "design_summary"; summary: DesignSummary }
  | { type: "progress"; progress: ProgressEvent }
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
}

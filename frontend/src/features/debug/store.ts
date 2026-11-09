import { create } from "zustand"

import type {
  AskTurn,
  MeasurementInput,
  Mode,
  ProjectFile,
  Run,
  SimAnalysis,
  Simulation,
} from "./types"

/** Every vendor-model analysis, in the order the form lists them. */
export const SIM_ANALYSES: SimAnalysis[] = ["startup", "load_step", "dropout"]

/**
 * Debug UI state. There is no server-side history (plan 001: no accounts),
 * so a run is the product of one SSE stream and lives only on this page,
 * like chat's streaming draft: nothing here is cached server data.
 */
interface DebugState {
  view: "input" | "session"
  /** Debug a board on the bench, or review one before ordering. */
  mode: Mode
  /** The vendor-model simulations to run; not kept across reloads. */
  analyses: SimAnalysis[]
  files: ProjectFile[]
  symptoms: string
  /** Every measurement entered so far; each re-run sends all of them. */
  measurements: MeasurementInput[]
  /** Newest first. */
  runs: Run[]
  /** Aborts the run in flight. */
  controller: AbortController | null
  inputError: string | null
  setMode: (mode: Mode) => void
  /** Tick or untick one analysis; the list keeps SIM_ANALYSES' order. */
  toggleAnalysis: (analysis: SimAnalysis) => void
  setFiles: (files: ProjectFile[]) => void
  setSymptoms: (text: string) => void
  setInputError: (message: string | null) => void
  addMeasurement: (m: MeasurementInput) => void
  startRun: (trigger: string, controller: AbortController) => number
  updateRun: (number: number, patch: (run: Run) => Partial<Run>) => void
  /** Record an answered question about simulation `simId` of run `number`. */
  addAskTurn: (number: number, simId: string, turn: AskTurn) => void
  /** Keep `whatIf` as simulation `simId`'s what-if, replacing the last. */
  setWhatIf: (number: number, simId: string, whatIf: Simulation) => void
  /** Drop simulation `simId`'s what-if (Reset). */
  clearWhatIf: (number: number, simId: string) => void
  /** Record an answered follow-up question about run `number`. */
  addResultTurn: (number: number, turn: AskTurn) => void
  editInput: () => void
  showSession: () => void
  reset: () => void
}

export const useDebugStore = create<DebugState>((set, get) => ({
  view: "input",
  mode: "review",
  analyses: ["startup"],
  files: [],
  symptoms: "",
  measurements: [],
  runs: [],
  controller: null,
  inputError: null,
  setMode: (mode) => set({ mode, inputError: null }),
  toggleAnalysis: (analysis) =>
    set((s) => ({
      analyses: SIM_ANALYSES.filter((a) => (a === analysis) !== s.analyses.includes(a)),
    })),
  setFiles: (files) => set({ files, inputError: null }),
  setSymptoms: (symptoms) => set({ symptoms }),
  setInputError: (inputError) => set({ inputError }),
  addMeasurement: (m) => set((s) => ({ measurements: [...s.measurements, m] })),
  startRun: (trigger, controller) => {
    const number = get().runs.length + 1
    const run: Run = {
      number,
      trigger,
      startedAt: Date.now(),
      finishedAt: null,
      summary: null,
      stage: null,
      result: null,
      error: null,
      streaming: true,
      simulations: [],
      chats: {},
      whatIfs: {},
      resultChat: [],
    }
    set((s) => ({ view: "session", runs: [run, ...s.runs], controller, inputError: null }))
    return number
  },
  updateRun: (number, patch) =>
    set((s) => ({ runs: s.runs.map((r) => (r.number === number ? { ...r, ...patch(r) } : r)) })),
  addAskTurn: (number, simId, turn) => {
    get().updateRun(number, (r) => ({
      chats: { ...r.chats, [simId]: [...(r.chats[simId] ?? []), turn] },
    }))
  },
  setWhatIf: (number, simId, whatIf) => {
    get().updateRun(number, (r) => ({ whatIfs: { ...r.whatIfs, [simId]: whatIf } }))
  },
  clearWhatIf: (number, simId) => {
    get().updateRun(number, (r) => ({
      whatIfs: Object.fromEntries(Object.entries(r.whatIfs).filter(([id]) => id !== simId)),
    }))
  },
  addResultTurn: (number, turn) => {
    get().updateRun(number, (r) => ({ resultChat: [...r.resultChat, turn] }))
  },
  editInput: () => set({ view: "input" }),
  showSession: () => set({ view: "session" }),
  reset: () => {
    get().controller?.abort()
    set({
      view: "input",
      files: [],
      symptoms: "",
      measurements: [],
      runs: [],
      controller: null,
      inputError: null,
    })
  },
}))

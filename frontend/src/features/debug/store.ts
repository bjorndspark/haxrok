import { create } from "zustand"

import type { AskTurn, MeasurementInput, Mode, ProjectFile, Run } from "./types"

/**
 * Debug UI state. There is no server-side history (plan 001: no accounts),
 * so a run is the product of one SSE stream and lives only on this page,
 * like chat's streaming draft: nothing here is cached server data.
 */
interface DebugState {
  view: "input" | "session"
  /** Debug a board on the bench, or review one before ordering. */
  mode: Mode
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
  setFiles: (files: ProjectFile[]) => void
  setSymptoms: (text: string) => void
  setInputError: (message: string | null) => void
  addMeasurement: (m: MeasurementInput) => void
  startRun: (trigger: string, controller: AbortController) => number
  updateRun: (number: number, patch: (run: Run) => Partial<Run>) => void
  /** Record an answered question about simulation `simId` of run `number`. */
  addAskTurn: (number: number, simId: string, turn: AskTurn) => void
  /** Record an answered follow-up question about run `number`. */
  addResultTurn: (number: number, turn: AskTurn) => void
  editInput: () => void
  showSession: () => void
  reset: () => void
}

export const useDebugStore = create<DebugState>((set, get) => ({
  view: "input",
  mode: "review",
  files: [],
  symptoms: "",
  measurements: [],
  runs: [],
  controller: null,
  inputError: null,
  setMode: (mode) => set({ mode, inputError: null }),
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
      simulations: [],
      chats: {},
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

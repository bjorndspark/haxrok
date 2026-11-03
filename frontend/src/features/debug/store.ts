import { create } from "zustand"

import type { MeasurementInput, ProjectFile, Run } from "./types"

/**
 * Debug UI state. There is no server-side history (plan 001: no accounts),
 * so a run is the product of one SSE stream and lives only on this page,
 * like chat's streaming draft: nothing here is cached server data.
 */
interface DebugState {
  view: "input" | "session"
  files: ProjectFile[]
  symptoms: string
  /** Every measurement entered so far; each re-run sends all of them. */
  measurements: MeasurementInput[]
  /** Newest first. */
  runs: Run[]
  /** Aborts the run in flight. */
  controller: AbortController | null
  inputError: string | null
  setFiles: (files: ProjectFile[]) => void
  setSymptoms: (text: string) => void
  setInputError: (message: string | null) => void
  addMeasurement: (m: MeasurementInput) => void
  startRun: (trigger: string, controller: AbortController) => number
  updateRun: (number: number, patch: (run: Run) => Partial<Run>) => void
  editInput: () => void
  showSession: () => void
  reset: () => void
}

export const useDebugStore = create<DebugState>((set, get) => ({
  view: "input",
  files: [],
  symptoms: "",
  measurements: [],
  runs: [],
  controller: null,
  inputError: null,
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
      log: [],
      stage: null,
      result: null,
      error: null,
    }
    set((s) => ({ view: "session", runs: [run, ...s.runs], controller, inputError: null }))
    return number
  },
  updateRun: (number, patch) =>
    set((s) => ({ runs: s.runs.map((r) => (r.number === number ? { ...r, ...patch(r) } : r)) })),
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

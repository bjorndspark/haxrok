/**
 * Debug behavior behind the components: run an analysis, re-run with a
 * measurement, cancel, read a dropped or picked project. Components only
 * render and call these.
 */
import { useMutation } from "@tanstack/react-query"
import { useCallback, useEffect, useState } from "react"

import { errorMessage } from "@/shared/lib/utils"

import { askSimulation, streamAnalysis } from "./api"
import { fromDataTransfer, fromFileList } from "./files"
import { toMarkdown } from "./present"
import { useDebugStore } from "./store"
import type { AnalysisEvent, AskTurn, ConfirmStep, Run, Simulation } from "./types"

function applyEvent(run: Run, ev: AnalysisEvent): Partial<Run> {
  switch (ev.type) {
    case "design_summary":
      return { summary: ev.summary }
    case "progress":
      return { stage: ev.progress.stage }
    case "simulation":
      return { simulations: [...run.simulations, ev.simulation] }
    case "result":
      return { result: ev.result, finishedAt: Date.now(), stage: null }
    case "error":
      return { error: ev.message, finishedAt: Date.now(), stage: null }
  }
}

export interface DebugActions {
  analyze: () => Promise<void>
  rerunWithMeasurement: (step: ConfirmStep, value: string) => Promise<void>
  cancel: () => void
  pickFiles: (list: FileList) => void
  dropFiles: (dt: DataTransfer) => Promise<void>
  downloadReport: (run: Run) => void
}

function setProject(files: ReturnType<typeof fromFileList>): void {
  const store = useDebugStore.getState()
  if (files.length === 0) {
    store.setInputError("No .kicad_pro or .kicad_sch files found. Drop the KiCad project folder.")
    return
  }
  store.setFiles(files)
}

export function useDebugActions(): DebugActions {
  const run = useCallback(async (trigger: string) => {
    const store = useDebugStore.getState()
    if (store.files.length === 0) {
      store.setInputError("Drop your KiCad project folder first.")
      return
    }
    if (store.mode === "debug" && !store.symptoms.trim()) {
      store.setInputError("Describe what you see on the bench first.")
      return
    }
    store.controller?.abort()
    const controller = new AbortController()
    const number = store.startRun(trigger, controller)
    const update = useDebugStore.getState().updateRun
    try {
      for await (const ev of streamAnalysis(
        store.files,
        store.symptoms,
        useDebugStore.getState().measurements,
        store.mode,
        controller.signal,
      )) {
        update(number, (r) => applyEvent(r, ev))
      }
      update(number, (r) =>
        r.finishedAt === null
          ? { error: "The connection closed before a result arrived.", finishedAt: Date.now() }
          : {},
      )
    } catch (e) {
      const cancelled = controller.signal.aborted
      update(number, () => ({
        error: cancelled ? "Cancelled." : errorMessage(e, "The analysis failed."),
        finishedAt: Date.now(),
        stage: null,
      }))
    }
  }, [])

  const analyze = useCallback(
    () =>
      run(
        useDebugStore.getState().mode === "review" ? "Review before ordering" : "Initial analysis",
      ),
    [run],
  )

  const rerunWithMeasurement = useCallback(
    async (step: ConfirmStep, value: string) => {
      const store = useDebugStore.getState()
      const id = `m${String(store.measurements.length + 1)}`
      store.addMeasurement({ step_id: step.id, context: step.instruction, value })
      await run(`${id}: ${value} (step ${step.id})`)
    },
    [run],
  )

  const cancel = useCallback(() => {
    useDebugStore.getState().controller?.abort()
  }, [])

  const pickFiles = useCallback((list: FileList) => {
    setProject(fromFileList(list))
  }, [])

  const dropFiles = useCallback(async (dt: DataTransfer) => {
    try {
      setProject(await fromDataTransfer(dt))
    } catch (e) {
      useDebugStore.getState().setInputError(errorMessage(e, "Couldn't read the dropped folder."))
    }
  }, [])

  const downloadReport = useCallback((run: Run) => {
    if (!run.summary || !run.result) return
    const blob = new Blob([toMarkdown(run, run.summary, run.result)], { type: "text/markdown" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${run.summary.project}-run${String(run.number)}.md`
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  return { analyze, rerunWithMeasurement, cancel, pickFiles, dropFiles, downloadReport }
}

export interface SimulationChat {
  turns: AskTurn[]
  ask: (question: string) => void
  pending: string | null
  error: string | null
}

/** Questions about one simulation of run `runNumber`. Answered turns live
 *  in the run (the store); the call in flight is a TanStack mutation. */
export function useSimulationChat(runNumber: number, sim: Simulation): SimulationChat {
  const turns = useDebugStore((s) => s.runs.find((r) => r.number === runNumber)?.chats[sim.id])
  const history = turns ?? []
  const mutation = useMutation({
    mutationFn: (question: string) => askSimulation({ simulation: sim, question, history }),
    onSuccess: (answer, question) => {
      useDebugStore.getState().addAskTurn(runNumber, sim.id, { question, answer })
    },
  })
  return {
    turns: history,
    ask: (question) => {
      if (question.trim() && !mutation.isPending) mutation.mutate(question.trim())
    },
    pending: mutation.isPending ? mutation.variables : null,
    error: mutation.isError ? errorMessage(mutation.error, "The question failed.") : null,
  }
}

/** Seconds since `startedAt`, ticking until `finishedAt` is set. */
export function useElapsed(startedAt: number, finishedAt: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (finishedAt !== null) return
    const timer = setInterval(() => {
      setNow(Date.now())
    }, 1000)
    return () => {
      clearInterval(timer)
    }
  }, [finishedAt])
  return Math.max(0, Math.floor(((finishedAt ?? now) - startedAt) / 1000))
}

export function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

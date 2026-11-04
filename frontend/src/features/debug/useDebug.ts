/**
 * Debug behavior behind the components: run an analysis, re-run with a
 * measurement, cancel, read a dropped or picked project. Components only
 * render and call these.
 */
import { useMutation } from "@tanstack/react-query"
import { useCallback, useEffect, useState } from "react"

import { useLanguage, type Language } from "@/shared/lib/language"
import { errorMessage } from "@/shared/lib/utils"

import { askResult, askSimulation, streamAnalysis } from "./api"
import { fromDataTransfer, fromFileList } from "./files"
import { currentMessages, useMessages } from "./messages"
import { toMarkdown } from "./present"
import { useDebugStore } from "./store"
import type {
  AnalysisEvent,
  AskTurn,
  CheckedAnalysis,
  ConfirmStep,
  DesignSummary,
  Run,
  Simulation,
} from "./types"

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
    store.setInputError(currentMessages().run.errors.noProjectFiles)
    return
  }
  store.setFiles(files)
}

export function useDebugActions(): DebugActions {
  const run = useCallback(async (trigger: string) => {
    const store = useDebugStore.getState()
    const errors = currentMessages().run.errors
    if (store.files.length === 0) {
      store.setInputError(errors.dropFirst)
      return
    }
    if (store.mode === "debug" && !store.symptoms.trim()) {
      store.setInputError(errors.describeFirst)
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
        useLanguage.getState().language,
        controller.signal,
      )) {
        update(number, (r) => applyEvent(r, ev))
      }
      update(number, (r) =>
        r.finishedAt === null ? { error: errors.closedEarly, finishedAt: Date.now() } : {},
      )
    } catch (e) {
      const cancelled = controller.signal.aborted
      update(number, () => ({
        error: cancelled ? errors.cancelled : errorMessage(e, errors.failed),
        finishedAt: Date.now(),
        stage: null,
      }))
    }
  }, [])

  const analyze = useCallback(() => {
    const trigger = currentMessages().run.trigger
    return run(useDebugStore.getState().mode === "review" ? trigger.review : trigger.initial)
  }, [run])

  const rerunWithMeasurement = useCallback(
    async (step: ConfirmStep, value: string) => {
      const store = useDebugStore.getState()
      const id = `m${String(store.measurements.length + 1)}`
      store.addMeasurement({ step_id: step.id, context: step.instruction, value })
      await run(currentMessages().run.trigger.measurement(id, value, step.id))
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
      useDebugStore
        .getState()
        .setInputError(errorMessage(e, currentMessages().run.errors.readFolder))
    }
  }, [])

  const downloadReport = useCallback((run: Run) => {
    if (!run.summary || !run.result) return
    // index.html's <title> carries PROJECT_NAME, so the name stays config.
    const md = toMarkdown(run, run.summary, run.result, currentMessages(), document.title)
    const blob = new Blob([md], { type: "text/markdown" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${run.summary.project}-run${String(run.number)}.md`
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  return { analyze, rerunWithMeasurement, cancel, pickFiles, dropFiles, downloadReport }
}

/** A conversation with the model: answered turns, the question in flight
 *  and its error. */
export interface Chat {
  turns: AskTurn[]
  ask: (question: string) => void
  pending: string | null
  error: string | null
}

/** The shared chat mechanics: answered turns live in the run (the store),
 *  the call in flight is a TanStack mutation. `send` gets the question and
 *  the history; `record` stores the answered turn. */
function useChat(
  turns: AskTurn[],
  send: (question: string, history: AskTurn[], language: Language) => Promise<string>,
  record: (turn: AskTurn) => void,
): Chat {
  const m = useMessages()
  const mutation = useMutation({
    mutationFn: (question: string) => send(question, turns, useLanguage.getState().language),
    onSuccess: (answer, question) => {
      record({ question, answer })
    },
  })
  return {
    turns,
    ask: (question) => {
      if (question.trim() && !mutation.isPending) mutation.mutate(question.trim())
    },
    pending: mutation.isPending ? mutation.variables : null,
    error: mutation.isError ? errorMessage(mutation.error, m.run.errors.question) : null,
  }
}

const NO_TURNS: AskTurn[] = []

/** Questions about one simulation of run `runNumber`. */
export function useSimulationChat(runNumber: number, sim: Simulation): Chat {
  const turns = useDebugStore((s) => s.runs.find((r) => r.number === runNumber)?.chats[sim.id])
  return useChat(
    turns ?? NO_TURNS,
    (question, history, language) =>
      askSimulation({ simulation: sim, question, history, language }),
    (turn) => {
      useDebugStore.getState().addAskTurn(runNumber, sim.id, turn)
    },
  )
}

/** Follow-up questions about a finished run: its summary and checked
 *  result go back with every question. */
export function useResultChat(run: Run, summary: DesignSummary, result: CheckedAnalysis): Chat {
  return useChat(
    run.resultChat,
    (question, history, language) => askResult({ summary, result, question, history, language }),
    (turn) => {
      useDebugStore.getState().addResultTurn(run.number, turn)
    },
  )
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

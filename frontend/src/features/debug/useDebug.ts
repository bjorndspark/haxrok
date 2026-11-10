/**
 * Debug behavior behind the components: run an analysis, re-run with a
 * measurement, cancel, read a dropped or picked project. Components only
 * render and call these.
 */
import { useMutation } from "@tanstack/react-query"
import { useCallback, useEffect, useState } from "react"

import { useLanguage, type Language } from "@/shared/lib/language"
import { errorMessage } from "@/shared/lib/utils"

import { askResult, askSimulation, rerunSimulation, streamAnalysis } from "./api"
import { fromDataTransfer, fromFileList } from "./files"
import { currentMessages, useMessages } from "./messages"
import { formatSi, parseSi, toMarkdown, whatIfLabels } from "./present"
import { useDebugStore } from "./store"
import type {
  AnalysisEvent,
  AskTurn,
  CheckedAnalysis,
  ConfirmStep,
  DesignSummary,
  Run,
  SimCircuit,
  Simulation,
} from "./types"

function applyEvent(run: Run, ev: AnalysisEvent): Partial<Run> {
  switch (ev.type) {
    case "design_summary":
      return { summary: ev.summary }
    case "progress":
      return { stage: ev.progress.stage }
    case "datasheet_facts":
      // Into the summary, so the report and /ask-result carry the facts.
      return { summary: run.summary && { ...run.summary, ...ev.facts } }
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
        store.analyses,
        // The field is on the review form only.
        store.mode === "review" ? store.battery : "",
        useLanguage.getState().language,
        controller.signal,
      )) {
        update(number, (r) => applyEvent(r, ev))
      }
      update(number, (r) =>
        r.finishedAt === null
          ? { error: errors.closedEarly, finishedAt: Date.now(), streaming: false }
          : { streaming: false },
      )
    } catch (e) {
      const cancelled = controller.signal.aborted
      update(number, (r) =>
        // Stopped (or cut off) after the result, while the vendor-model
        // simulations ran: the run itself finished, so it keeps its result.
        r.finishedAt !== null
          ? { streaming: false }
          : {
              error: cancelled ? errors.cancelled : errorMessage(e, errors.failed),
              finishedAt: Date.now(),
              stage: null,
              streaming: false,
            },
      )
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

/** An RC simulation's what-if: the R and C fields, the re-run in flight
 *  and the latest re-run, kept in the run like the questions. */
export interface WhatIf {
  labels: { r: string; c: string }
  r: string
  c: string
  setR: (text: string) => void
  setC: (text: string) => void
  /** Parse the fields and re-run ngspice with them. */
  rerun: () => void
  /** Drop the what-if and put the fields back to the original values. */
  reset: () => void
  whatIf: Simulation | null
  pending: boolean
  error: string | null
}

/** The what-if controls for `sim` of run `runNumber`; `circuit` is its
 *  RC circuit (`sim.circuit`). Each re-run replaces the last. */
export function useWhatIf(runNumber: number, sim: Simulation, circuit: SimCircuit): WhatIf {
  const m = useMessages()
  const labels = whatIfLabels(circuit, m)
  const whatIf = useDebugStore((s) => s.runs.find((r) => r.number === runNumber)?.whatIfs[sim.id])
  const [r, setR] = useState(() => formatSi(circuit.r_ohm))
  const [c, setC] = useState(() => formatSi(circuit.c_farad))
  const [badValue, setBadValue] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (values: { r_ohm: number; c_farad: number }) =>
      rerunSimulation({ simulation: sim, ...values }),
    onSuccess: (result) => {
      useDebugStore.getState().setWhatIf(runNumber, sim.id, result)
    },
  })
  return {
    labels,
    r,
    c,
    setR,
    setC,
    rerun: () => {
      const rOhm = parseSi(r)
      const cFarad = parseSi(c)
      const bad = rOhm === null ? labels.r : cFarad === null ? labels.c : null
      setBadValue(bad === null ? null : m.simulations.whatIf.badValue(bad))
      if (rOhm !== null && cFarad !== null && !mutation.isPending) {
        mutation.mutate({ r_ohm: rOhm, c_farad: cFarad })
      }
    },
    reset: () => {
      setR(formatSi(circuit.r_ohm))
      setC(formatSi(circuit.c_farad))
      setBadValue(null)
      mutation.reset()
      useDebugStore.getState().clearWhatIf(runNumber, sim.id)
    },
    whatIf: whatIf ?? null,
    pending: mutation.isPending,
    error:
      badValue ??
      (mutation.isError ? errorMessage(mutation.error, m.simulations.whatIf.failed) : null),
  }
}

/** Questions about one simulation of run `runNumber`. */
export function useSimulationChat(runNumber: number, sim: Simulation): Chat {
  const run = useDebugStore((s) => s.runs.find((r) => r.number === runNumber))
  const whatIf = run?.whatIfs[sim.id] ?? null
  return useChat(
    run?.chats[sim.id] ?? NO_TURNS,
    // The what-if drawn over the plot goes too, so the answer compares both.
    (question, history, language) =>
      askSimulation({ simulation: sim, what_if: whatIf, question, history, language }),
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

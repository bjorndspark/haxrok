import { CircleCheck, Clock, LoaderCircle, Terminal } from "lucide-react"

import { cn } from "@/shared/lib/utils"
import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"

import type { ProgressStage, Run } from "../types"
import { formatElapsed, useElapsed } from "../useDebug"
import { DesignSummaryCard } from "./DesignSummaryCard"

const STEPS: { key: "upload" | ProgressStage; label: string; detail: string }[] = [
  { key: "upload", label: "Parse + ERC", detail: "kicad-cli netlist export and ERC" },
  { key: "pin_lookup", label: "Pin lookup", detail: "Datasheet pin tables for the ICs" },
  { key: "reasoning", label: "Reasoning", detail: "The model reads the schematic" },
  { key: "checking", label: "Checking", detail: "Every citation checked against the input" },
]

function stepState(run: Run, index: number): "done" | "active" | "pending" {
  // Before the summary arrives, the upload + kicad-cli step is running.
  const current =
    run.summary === null ? 0 : run.stage ? STEPS.findIndex((s) => s.key === run.stage) : 1
  if (index < current) return "done"
  return index === current ? "active" : "pending"
}

export function AnalyzingPanel({ run, onCancel }: { run: Run; onCancel: () => void }) {
  const elapsed = useElapsed(run.startedAt, run.finishedAt)
  return (
    <div className="animate-fade-in space-y-6">
      <Card className="p-6 sm:p-7">
        <div className="flex flex-col items-start justify-between gap-4 border-b pb-6 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-amber-200 bg-amber-50 text-amber-600 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-400">
              <LoaderCircle className="h-5 w-5 animate-spin" />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <h2 className="text-xl font-bold tracking-tight sm:text-2xl">Analyzing…</h2>
                <span className="flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-0.5 font-mono text-base font-semibold text-blue-700 dark:border-blue-900 dark:bg-blue-950/50 dark:text-sky-400">
                  <Clock className="h-4 w-4" />
                  {formatElapsed(elapsed)}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                Usually about two minutes. The model says nothing until it has finished thinking.
              </p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        </div>

        <div className="grid grid-cols-2 gap-3 pt-6 sm:grid-cols-4">
          {STEPS.map((step, i) => {
            const state = stepState(run, i)
            return (
              <div
                key={step.key}
                className={cn(
                  "rounded-lg border p-3 transition-all",
                  state === "done" &&
                    "border-emerald-200 bg-emerald-50/70 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200",
                  state === "active" &&
                    "border-blue-300 bg-blue-50/80 text-blue-900 ring-2 ring-blue-200 dark:border-blue-800 dark:bg-blue-950/40 dark:text-sky-300 dark:ring-blue-900/60",
                  state === "pending" && "bg-background text-muted-foreground",
                )}
              >
                <div className="flex items-center gap-2">
                  {state === "done" && <CircleCheck className="h-4 w-4 shrink-0" />}
                  {state === "active" && <LoaderCircle className="h-4 w-4 shrink-0 animate-spin" />}
                  {state === "pending" && (
                    <span className="flex h-4 w-4 items-center justify-center rounded-full border font-mono text-[10px]">
                      {i + 1}
                    </span>
                  )}
                  <span className="text-xs font-bold tracking-wider uppercase">{step.label}</span>
                </div>
                <p className="mt-1.5 text-[11px] opacity-80">{step.detail}</p>
              </div>
            )
          })}
        </div>
      </Card>

      <div className="overflow-hidden rounded-xl border border-[#273445] bg-[#121720] font-mono text-xs text-slate-200 shadow-inner">
        <div className="flex items-center justify-between border-b border-[#273445] bg-[#18212e] px-4 py-2.5">
          <span className="flex items-center gap-2 font-semibold">
            <Terminal className="h-4 w-4 text-emerald-400" />
            Run log
          </span>
          <span className="flex items-center gap-1.5 text-[10px] font-semibold text-emerald-400">
            <span className="h-2 w-2 animate-ping rounded-full bg-emerald-400" />
            LIVE
          </span>
        </div>
        <div className="max-h-48 space-y-1.5 overflow-y-auto p-4 text-[11px]">
          <div className="flex gap-2.5">
            <span className="shrink-0 text-slate-500">[00:00]</span>
            <span className="text-slate-300">[UPLOAD] {run.trigger}: sending the schematics</span>
          </div>
          {run.log.map((line, i) => (
            <div key={i} className="flex gap-2.5">
              <span className="shrink-0 text-slate-500">
                [{formatElapsed(Math.floor((line.at - run.startedAt) / 1000))}]
              </span>
              <span className="text-slate-300">{line.text}</span>
            </div>
          ))}
        </div>
      </div>

      {run.summary && <DesignSummaryCard summary={run.summary} />}
    </div>
  )
}

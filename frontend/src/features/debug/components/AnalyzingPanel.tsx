import { cn } from "@/shared/lib/utils"
import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"

import type { ProgressStage, Run } from "../types"
import { formatElapsed, useElapsed } from "../useDebug"
import { DesignSummaryCard } from "./DesignSummaryCard"
import { SimulationsCard } from "./SimulationsCard"

const STEPS: { key: "upload" | ProgressStage; label: string; detail: string }[] = [
  { key: "upload", label: "Parse + ERC", detail: "kicad-cli netlist export and ERC" },
  { key: "pin_lookup", label: "Pin lookup", detail: "Datasheet pin tables for the ICs" },
  { key: "reasoning", label: "Diagnosis", detail: "The model traces the circuit" },
  { key: "structuring", label: "Write-up", detail: "Claims, citations and bench steps" },
  { key: "checking", label: "Checking", detail: "Every citation checked against the input" },
]

function stepState(run: Run, index: number): "done" | "active" | "pending" {
  // Before the summary arrives, the upload + kicad-cli step is running.
  const current =
    run.summary === null ? 0 : run.stage ? STEPS.findIndex((s) => s.key === run.stage) : 1
  if (index < current) return "done"
  return index === current ? "active" : "pending"
}

const STATE_LABEL = { done: "done", active: "running", pending: "waiting" } as const
const STATE_GLYPH = { done: "✓", active: "●", pending: "·" } as const

export function AnalyzingPanel({ run, onCancel }: { run: Run; onCancel: () => void }) {
  const elapsed = useElapsed(run.startedAt, run.finishedAt)
  return (
    <div className="animate-fade-in space-y-6">
      <Card className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-[1.5ch]">
          <span className="bg-warn px-[1ch] font-bold text-on-state">ANALYZING</span>
          <span className="font-bold tabular-nums">{formatElapsed(elapsed)}</span>
          <span className="text-xs text-muted-foreground">usually 4–5 min</span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={onCancel}>
            Cancel
          </Button>
        </div>

        <ol className="mt-4 border-t pt-3">
          {STEPS.map((step, i) => {
            const state = stepState(run, i)
            return (
              <li
                key={step.key}
                className={cn(
                  "grid grid-cols-[9ch_minmax(0,14ch)_minmax(0,1fr)] gap-x-[1.5ch] py-0.5 text-sm",
                  state === "pending" && "text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex items-center gap-[0.5ch]",
                    state === "done" && "text-ok",
                    state === "active" && "font-bold text-warn",
                  )}
                >
                  <span className={cn("w-[1ch]", state === "active" && "animate-pulse")}>
                    {STATE_GLYPH[state]}
                  </span>
                  {STATE_LABEL[state]}
                </span>
                <span className={cn("truncate", state !== "pending" && "font-bold")}>
                  {step.label}
                </span>
                <span className="truncate text-xs text-muted-foreground">{step.detail}</span>
              </li>
            )
          })}
        </ol>
      </Card>

      <SimulationsCard run={run} />
      {run.summary && <DesignSummaryCard summary={run.summary} />}
    </div>
  )
}

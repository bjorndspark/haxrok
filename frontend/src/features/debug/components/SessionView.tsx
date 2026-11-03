import { BookOpen, ChevronDown, ChevronUp, CircleHelp, Download, TriangleAlert } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/shared/ui/badge"
import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"

import { useDebugStore } from "../store"
import type { Run } from "../types"
import { formatElapsed, type DebugActions } from "../useDebug"
import { AnalyzingPanel } from "./AnalyzingPanel"
import { DesignSummaryCard } from "./DesignSummaryCard"
import { GlossaryText } from "./GlossaryText"
import { HypothesisCard } from "./HypothesisCard"

function RunHeader({ run, total }: { run: Run; total: number }) {
  const latest = run.number === total
  const took =
    run.finishedAt !== null
      ? formatElapsed(Math.floor((run.finishedAt - run.startedAt) / 1000))
      : null
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2.5">
        <span
          className={
            latest
              ? "flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground"
              : "flex h-6 w-6 items-center justify-center rounded-full bg-muted text-xs font-bold text-muted-foreground"
          }
        >
          {run.number}
        </span>
        <span className="font-mono text-sm font-bold sm:text-base">Run #{run.number}</span>
        <span className="text-xs text-muted-foreground">
          · {new Date(run.startedAt).toLocaleTimeString()}
          {took && ` · took ${took}`}
        </span>
      </div>
      <span className="rounded-md border bg-card px-2.5 py-1 font-mono text-xs text-muted-foreground">
        {run.trigger}
      </span>
    </div>
  )
}

function RunBody({
  run,
  latest,
  busy,
  actions,
}: {
  run: Run
  latest: boolean
  busy: boolean
  actions: DebugActions
}) {
  const editInput = useDebugStore((s) => s.editInput)
  if (run.finishedAt === null) return <AnalyzingPanel run={run} onCancel={actions.cancel} />
  if (run.error !== null || run.result === null) {
    return (
      <div className="space-y-6">
        <Card className="space-y-3 border-rose-200 p-6 dark:border-rose-900">
          <p className="flex items-center gap-2 font-semibold text-rose-800 dark:text-rose-300">
            <TriangleAlert className="h-4 w-4" />
            This run didn&apos;t finish
          </p>
          <p className="text-sm">{run.error ?? "No result arrived."}</p>
          {latest && (
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => void actions.analyze()}>
                Try again
              </Button>
              <Button size="sm" variant="outline" onClick={editInput}>
                Edit input
              </Button>
            </div>
          )}
        </Card>
        {run.summary && latest && <DesignSummaryCard summary={run.summary} />}
      </div>
    )
  }
  const result = run.result
  const placed = new Set(result.hypotheses.flatMap((h) => [h.id, ...h.confirm.map((s) => s.id)]))
  const loose = result.problems.filter((p) => !placed.has(p.where))
  return (
    <div className="space-y-5">
      {result.hypotheses.length === 0 && (
        <Card className="p-6 text-sm">The model returned no hypotheses for this input.</Card>
      )}
      {result.hypotheses.map((h) => (
        <HypothesisCard
          key={h.id}
          hypothesis={h}
          diagram={result.diagrams[h.id] ?? null}
          summary={run.summary}
          glossary={result.glossary}
          problems={result.problems}
          latest={latest}
          busy={busy}
          onRerun={(step, value) => void actions.rerunWithMeasurement(step, value)}
        />
      ))}

      {loose.length > 0 && (
        <Card className="space-y-1 p-4 text-xs text-amber-800 dark:text-amber-300">
          {loose.map((p, i) => (
            <p key={i}>
              {p.where}: {p.message}
            </p>
          ))}
        </Card>
      )}

      {result.open_questions.length > 0 && (
        <Card className="space-y-4 p-6 sm:p-7">
          <h3 className="flex items-center gap-2 text-base font-bold sm:text-lg">
            <CircleHelp className="h-5 w-5 text-blue-600 dark:text-sky-400" />
            Open questions
          </h3>
          <p className="text-xs text-muted-foreground">What your input can&apos;t answer yet.</p>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {result.open_questions.map((q, i) => (
              <div key={i} className="space-y-1.5 rounded-lg border bg-background p-3.5 text-xs">
                <p className="font-semibold">
                  <GlossaryText text={q.text} glossary={result.glossary} />
                </p>
                {q.suggested_check && (
                  <p className="leading-relaxed text-muted-foreground">
                    Check: {q.suggested_check}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {result.glossary.length > 0 && (
        <Card className="space-y-4 p-6 sm:p-7">
          <h3 className="flex items-center gap-2 text-base font-bold sm:text-lg">
            <BookOpen className="h-5 w-5 text-muted-foreground" />
            Glossary
          </h3>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3">
            {result.glossary.map((g) => (
              <div key={g.term} className="space-y-1 rounded-lg border bg-background p-3 text-xs">
                <dt className="font-mono font-bold text-blue-700 dark:text-sky-400">{g.term}</dt>
                <dd className="text-[11px] leading-relaxed text-muted-foreground">
                  {g.definition}
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      )}
    </div>
  )
}

function OlderRun({ run, total, actions }: { run: Run; total: number; actions: DebugActions }) {
  const [open, setOpen] = useState(false)
  const top = run.result?.hypotheses[0]
  return (
    <div className="rounded-2xl border bg-muted/40 p-5 sm:p-6">
      <RunHeader run={run} total={total} />
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between rounded-lg border bg-card px-4 py-2.5 text-left text-sm"
      >
        <span className="truncate">
          {top ? (
            <>
              <span className="font-mono text-xs text-muted-foreground">#1 </span>
              {top.title}
            </>
          ) : (
            (run.error ?? "No result")
          )}
        </span>
        {open ? (
          <ChevronUp className="h-4 w-4 shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0" />
        )}
      </button>
      {open && (
        <div className="mt-4">
          <RunBody run={run} latest={false} busy actions={actions} />
        </div>
      )}
    </div>
  )
}

export function SessionView({ actions }: { actions: DebugActions }) {
  const runs = useDebugStore((s) => s.runs)
  const editInput = useDebugStore((s) => s.editInput)
  if (runs.length === 0) return null
  const [latest, ...older] = runs
  const busy = latest.finishedAt === null
  const reportable = runs.find((r) => r.result !== null && r.summary !== null)

  return (
    <div className="animate-fade-in space-y-8">
      <div className="flex flex-col justify-between gap-4 border-b pb-2 sm:flex-row sm:items-center">
        <div>
          <div className="flex items-center gap-2">
            <Badge tone="blue" className="font-bold tracking-wider uppercase">
              {busy ? "Analyzing" : "Diagnosis ready"}
            </Badge>
            <span className="font-mono text-xs text-muted-foreground">
              {runs.length} {runs.length === 1 ? "run" : "runs, newest first"}
            </span>
          </div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">Ranked hypotheses</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {reportable && (
            <Button variant="outline" size="sm" onClick={() => actions.downloadReport(reportable)}>
              <Download />
              Download report
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={editInput}>
            Edit observations
          </Button>
        </div>
      </div>

      <section>
        <RunHeader run={latest} total={runs.length} />
        <RunBody run={latest} latest busy={busy} actions={actions} />
      </section>

      {older.map((run) => (
        <OlderRun key={run.number} run={run} total={runs.length} actions={actions} />
      ))}
    </div>
  )
}

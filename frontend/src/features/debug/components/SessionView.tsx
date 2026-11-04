import { useState } from "react"

import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"
import { Title } from "@/shared/ui/title"

import { isReview } from "../present"
import { useDebugStore } from "../store"
import type { Run } from "../types"
import { formatElapsed, type DebugActions } from "../useDebug"
import { AnalyzingPanel } from "./AnalyzingPanel"
import { DesignSummaryCard } from "./DesignSummaryCard"
import { GlossaryText } from "./GlossaryText"
import { HypothesisCard } from "./HypothesisCard"
import { SimulationsCard } from "./SimulationsCard"

function RunHeader({ run, total }: { run: Run; total: number }) {
  const latest = run.number === total
  const took =
    run.finishedAt !== null
      ? formatElapsed(Math.floor((run.finishedAt - run.startedAt) / 1000))
      : null
  return (
    <div className="mb-4 flex items-center gap-[1ch] text-sm">
      <span className={latest ? "font-bold" : "font-bold text-muted-foreground"}>
        Run #{run.number}
      </span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {new Date(run.startedAt).toLocaleTimeString()}
        {took && ` · took ${took}`}
      </span>
      <span className="min-w-[2ch] flex-1 border-t" aria-hidden />
      <span className="truncate text-xs text-muted-foreground">{run.trigger}</span>
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
        <Card className="space-y-3 border-bad p-4 sm:p-5">
          <p>
            <span className="bg-bad px-[1ch] font-bold text-on-state">FAILED</span> This run
            didn&apos;t finish
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
        <SimulationsCard run={run} />
        {run.summary && latest && <DesignSummaryCard summary={run.summary} />}
      </div>
    )
  }
  const result = run.result
  const placed = new Set(result.hypotheses.flatMap((h) => [h.id, ...h.confirm.map((s) => s.id)]))
  const loose = result.problems.filter((p) => !placed.has(p.where))
  return (
    <div className="space-y-5">
      {result.hypotheses.length === 0 &&
        (isReview(run.summary) ? (
          <Card className="space-y-2 border-ok p-4 sm:p-5">
            <p className="font-bold text-ok">✓ No schematic issues found</p>
            <p className="text-muted-foreground">
              The review traced each block and found nothing to change. It only reads the schematic:
              layout, footprints and assembly aren&apos;t checked.
            </p>
          </Card>
        ) : (
          <Card className="p-4 text-sm sm:p-5">
            The model returned no hypotheses for this input.
          </Card>
        ))}
      {result.hypotheses.map((h) => (
        <HypothesisCard
          key={h.id}
          hypothesis={h}
          diagram={result.diagrams[h.id] ?? null}
          summary={run.summary}
          simulations={run.simulations}
          glossary={result.glossary}
          problems={result.problems}
          latest={latest}
          busy={busy}
          onRerun={(step, value) => void actions.rerunWithMeasurement(step, value)}
        />
      ))}

      <SimulationsCard run={run} />

      {loose.length > 0 && (
        <Card className="space-y-1 border-warn p-4 text-xs text-warn">
          {loose.map((p, i) => (
            <p key={i}>
              {p.where}: {p.message}
            </p>
          ))}
        </Card>
      )}

      {result.open_questions.length > 0 && (
        <Card className="space-y-3 p-4 sm:p-5">
          <Title as="h3">Open questions</Title>
          <div className="divide-y">
            {result.open_questions.map((q, i) => (
              <div key={i} className="space-y-1 py-2.5 text-xs">
                <p className="font-bold">
                  <GlossaryText text={q.text} glossary={result.glossary} />
                </p>
                {q.suggested_check && (
                  <p className="leading-relaxed">
                    <span className="text-info">check:</span> {q.suggested_check}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {result.glossary.length > 0 && (
        <Card className="space-y-3 p-4 sm:p-5">
          <Title as="h3">Glossary</Title>
          <dl className="space-y-1.5 text-xs leading-relaxed">
            {result.glossary.map((g) => (
              <div key={g.term}>
                <dt className="inline font-bold text-info after:text-foreground after:content-[':']">
                  {g.term}
                </dt>{" "}
                <dd className="inline">{g.definition}</dd>
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
    <div>
      <RunHeader run={run} total={total} />
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-[1ch] border px-[1ch] py-1.5 text-left text-sm hover:bg-accent"
      >
        <span className="truncate">
          {top ? (
            <>
              <span className="text-muted-foreground">#1 </span>
              {top.title}
            </>
          ) : (
            (run.error ?? "No result")
          )}
        </span>
        <span className="shrink-0" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
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
  const mode = useDebugStore((s) => s.mode)
  if (runs.length === 0) return null
  const [latest, ...older] = runs
  const busy = latest.finishedAt === null
  const review = latest.summary ? isReview(latest.summary) : mode === "review"
  const reportable = runs.find((r) => r.result !== null && r.summary !== null)

  return (
    <div className="animate-fade-in space-y-8">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs text-muted-foreground">
            <span className={busy ? "font-bold text-warn" : "font-bold text-ok"}>
              {busy ? "analyzing" : review ? "review ready" : "diagnosis ready"}
            </span>
            {" · "}
            {runs.length} {runs.length === 1 ? "run" : "runs, newest first"}
          </p>
          <h1 className="mt-1 font-bold">{review ? "Design review" : "Ranked hypotheses"}</h1>
        </div>
        <div className="flex flex-wrap gap-[1ch]">
          {reportable && (
            <Button variant="outline" size="sm" onClick={() => actions.downloadReport(reportable)}>
              Download report
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={editInput}>
            {review ? "Edit input" : "Edit observations"}
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

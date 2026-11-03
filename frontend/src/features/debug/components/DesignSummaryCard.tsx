import { ChevronDown, ChevronUp, Cpu, ShieldAlert, Zap } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/shared/ui/badge"
import { Card } from "@/shared/ui/card"

import type { DesignSummary } from "../types"

/** What the backend parsed, shown before the model answers so the user
 *  can check the parse and see which ICs have pin data. */
export function DesignSummaryCard({ summary }: { summary: DesignSummary }) {
  const [showErc, setShowErc] = useState(false)
  const [showSymptoms, setShowSymptoms] = useState(true)
  return (
    <Card className="space-y-6 p-6 sm:p-7">
      <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
        <div>
          <h2 className="text-lg font-bold">Design summary</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Parsed from <code className="font-mono text-foreground">{summary.project}</code> by
            kicad-cli
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge>{summary.sheets} sheets</Badge>
          <Badge>{summary.parts} parts</Badge>
          <Badge>{summary.nets} nets</Badge>
          <Badge>{summary.ics.length} ICs</Badge>
          <Badge tone={summary.erc.length > 0 ? "amber" : "green"}>
            {summary.erc.length} ERC findings
          </Badge>
        </div>
      </div>

      <div className="space-y-2.5">
        <h3 className="font-mono text-xs font-bold tracking-wider text-muted-foreground uppercase">
          ICs and pin data
        </h3>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {summary.ics.map((ic) => (
            <div key={ic.ref} className="rounded-lg border bg-background p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-1.5 font-mono font-bold text-blue-700 dark:text-sky-400">
                  <Cpu className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">
                    {ic.ref} · {ic.value}
                  </span>
                </span>
                <Badge tone={ic.pin_data ? "green" : "neutral"}>
                  {ic.pin_data ? "Pin table" : "No pin data"}
                </Badge>
              </div>
              <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
                {ic.footprint || "no footprint"}
              </p>
            </div>
          ))}
        </div>
        {summary.ics.some((ic) => !ic.pin_data) && (
          <p className="text-[11px] text-muted-foreground">
            ICs without pin data are still analyzed; facts about their pins come from the
            model&apos;s memory and are marked unverified.
          </p>
        )}
      </div>

      {summary.wiring.length > 0 && (
        <div className="space-y-2 rounded-lg border border-rose-200 bg-rose-50/60 p-3.5 text-xs dark:border-rose-900 dark:bg-rose-950/30">
          <span className="flex items-center gap-2 font-semibold text-rose-900 dark:text-rose-200">
            <Zap className="h-4 w-4 text-rose-600 dark:text-rose-400" />
            Wired against the datasheet ({summary.wiring.length})
          </span>
          {summary.wiring.map((f) => (
            <p key={f.id} className="leading-relaxed">
              {f.text}
            </p>
          ))}
          <p className="text-[11px] text-muted-foreground">
            Found by checking your netlist against the pin table, before the model runs.
          </p>
        </div>
      )}

      <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3.5 text-xs dark:border-amber-900 dark:bg-amber-950/30">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 font-semibold text-amber-900 dark:text-amber-200">
            <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            KiCad ERC findings ({summary.erc.length})
          </span>
          {summary.erc.length > 0 && (
            <button
              type="button"
              onClick={() => setShowErc(!showErc)}
              className="flex items-center gap-1 font-medium text-amber-800 dark:text-amber-300"
            >
              {showErc ? "Hide" : "Inspect"}
              {showErc ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
        <p className="mt-1 text-[11px] text-amber-800/80 dark:text-amber-300/80">
          Warnings only about missing symbol libraries are dropped; the schematics embed their
          symbols.
        </p>
        {showErc && (
          <ul className="mt-3 max-h-72 space-y-2 overflow-y-auto border-t border-amber-200 pt-2 text-amber-950 dark:border-amber-900 dark:text-amber-100">
            {summary.erc.map((e) => (
              <li key={e.id} className="flex items-start gap-2">
                <span className="shrink-0 font-mono font-bold text-amber-700 dark:text-amber-400">
                  {e.id}
                </span>
                <span>
                  <span className="font-mono text-[11px]">[{e.rule}]</span> {e.message}{" "}
                  <span className="text-amber-700/80 dark:text-amber-300/70">({e.sheet})</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {(summary.symptoms.length > 0 || summary.measurements.length > 0) && (
        <div className="overflow-hidden rounded-lg border">
          <button
            type="button"
            onClick={() => setShowSymptoms(!showSymptoms)}
            className="flex w-full items-center justify-between bg-background p-3.5 text-left hover:bg-accent"
          >
            <span className="text-xs font-bold tracking-wider uppercase">
              {summary.mode === "review" ? "Notes on the board" : "Symptoms"}
              {summary.measurements.length > 0 ? " and measurements" : ""}
            </span>
            {showSymptoms ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>
          {showSymptoms && (
            <ol className="space-y-1 border-t p-4 text-xs">
              {summary.symptoms.map((s) => (
                <li key={s.id} className="flex gap-2">
                  <span className="w-7 shrink-0 font-mono font-semibold text-muted-foreground">
                    {s.id}
                  </span>
                  <span>{s.text}</span>
                </li>
              ))}
              {summary.measurements.map((m) => (
                <li key={m.id} className="flex gap-2">
                  <span className="w-7 shrink-0 font-mono font-semibold text-emerald-700 dark:text-emerald-400">
                    {m.id}
                  </span>
                  <span>
                    {m.value}
                    {m.step_id && (
                      <span className="text-muted-foreground"> (step {m.step_id})</span>
                    )}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Card>
  )
}

import { useState, type ReactNode } from "react"

import { Badge } from "@/shared/ui/badge"
import { Card } from "@/shared/ui/card"
import { Title } from "@/shared/ui/title"

import { calculationBadge } from "../present"
import type { DesignSummary } from "../types"

/** A sub-heading as a named rule: `LABEL ─────`, like aitop's machine rows. */
function Rule({ children }: { children: ReactNode }) {
  return (
    <h3 className="flex items-center gap-[1ch] text-xs font-bold tracking-wider uppercase">
      <span className="shrink-0">{children}</span>
      <span className="min-w-[2ch] flex-1 border-t" aria-hidden />
    </h3>
  )
}

/** What the backend parsed, shown before the model answers so the user
 *  can check the parse and see which ICs have pin data. */
export function DesignSummaryCard({ summary }: { summary: DesignSummary }) {
  const [showErc, setShowErc] = useState(false)
  const [showSymptoms, setShowSymptoms] = useState(true)
  const [showRefs, setShowRefs] = useState(false)
  const refParts = new Set(summary.references.map((n) => n.part))
  return (
    <Card className="space-y-5 p-4 sm:p-5">
      <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
        <div>
          <Title>Design summary</Title>
          <span className="ml-[1ch] font-bold text-info">{summary.project}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          {summary.sheets} sheets · {summary.parts} parts · {summary.nets} nets ·{" "}
          {summary.ics.length} ICs ·{" "}
          <span className={summary.erc.length > 0 ? "text-warn" : "text-ok"}>
            {summary.erc.length} ERC
          </span>
        </p>
      </div>

      <div className="space-y-2">
        <Rule>ICs and pin data</Rule>
        <ul className="space-y-0.5 text-xs">
          {summary.ics.map((ic) => (
            <li key={ic.ref} className="flex min-w-0 items-baseline gap-[2ch]">
              <span className="shrink-0 font-bold text-info">
                {ic.ref} · {ic.value}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {ic.footprint || "no footprint"}
              </span>
              <span className="flex shrink-0 gap-[1ch]">
                {refParts.has(ic.value) && <Badge tone="ok">ref design</Badge>}
                <Badge tone={ic.pin_data ? "ok" : "neutral"}>
                  {ic.pin_data ? "pin table" : "no pin data"}
                </Badge>
              </span>
            </li>
          ))}
        </ul>
        {summary.ics.some((ic) => !ic.pin_data) && (
          <p className="text-xs text-muted-foreground">
            ICs without pin data are still analyzed; facts about their pins come from the
            model&apos;s memory and are marked unverified.
          </p>
        )}
      </div>

      {summary.wiring.length > 0 && (
        <div className="space-y-2 border-l-2 border-bad pl-[1ch] text-xs">
          <span className="font-bold text-bad">
            ! Wired against the datasheet ({summary.wiring.length})
          </span>
          {summary.wiring.map((f) => (
            <p key={f.id} className="leading-relaxed">
              {f.text}
            </p>
          ))}
        </div>
      )}

      {summary.pin_warnings.length > 0 && (
        <div className="space-y-2 border-l-2 border-warn pl-[1ch] text-xs">
          <span className="font-bold text-warn">
            ! Pin order to check against the package&apos;s usual pinout (
            {summary.pin_warnings.length})
          </span>
          {summary.pin_warnings.map((w) => (
            <p key={w.id} className="leading-relaxed">
              {w.text}
            </p>
          ))}
        </div>
      )}

      {summary.calculations.length > 0 && (
        <div className="space-y-2 text-xs">
          <Rule>Calculated from your netlist ({summary.calculations.length})</Rule>
          {summary.calculations.map((c) => (
            <div
              key={c.id}
              className={
                c.flagged
                  ? "flex flex-col gap-1 border-l-2 border-bad pl-[1ch] sm:flex-row sm:items-start sm:gap-[1ch]"
                  : "flex flex-col gap-1 border-l-2 border-transparent pl-[1ch] sm:flex-row sm:items-start sm:gap-[1ch]"
              }
            >
              <Badge tone={c.flagged ? "alert" : "ok"} className="shrink-0 self-start">
                {calculationBadge(c)}
              </Badge>
              <p className="min-w-0 flex-1 leading-relaxed">{c.text}</p>
            </div>
          ))}
        </div>
      )}

      {summary.references.length > 0 && (
        <div className="border">
          <button
            type="button"
            onClick={() => setShowRefs(!showRefs)}
            aria-expanded={showRefs}
            className="flex w-full items-center justify-between gap-[1ch] px-[1ch] py-2 text-left hover:bg-accent"
          >
            <span className="text-xs font-bold">
              <span className="mr-[1ch]">{showRefs ? "▾" : "▸"}</span>
              Reference designs ({summary.references.length} notes for{" "}
              <span className="text-info">{[...refParts].join(", ")}</span>)
            </span>
          </button>
          {showRefs && (
            <ul className="space-y-2 border-t p-[1ch] text-xs">
              {summary.references.map((n) => (
                <li key={n.id} className="space-y-0.5">
                  <p className="leading-relaxed">
                    <span className="mr-[1ch] font-bold text-muted-foreground">{n.id}</span>
                    {n.text}
                  </p>
                  <p className="text-muted-foreground">
                    <span className="text-info">{n.part}</span> · {n.source}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="border-l-2 border-warn pl-[1ch] text-xs">
        <div className="flex items-center justify-between gap-[1ch]">
          <span className="font-bold text-warn">KiCad ERC findings ({summary.erc.length})</span>
          {summary.erc.length > 0 && (
            <button
              type="button"
              onClick={() => setShowErc(!showErc)}
              aria-expanded={showErc}
              className="text-warn hover:underline"
            >
              {showErc ? "▾ hide" : "▸ inspect"}
            </button>
          )}
        </div>
        {showErc && (
          <ul className="mt-2 max-h-72 space-y-1.5 overflow-y-auto border-t pt-2">
            {summary.erc.map((e) => (
              <li key={e.id} className="flex items-start gap-[1ch]">
                <span className="shrink-0 font-bold text-info">{e.id}</span>
                <span>
                  <span className="text-muted-foreground">{e.rule}</span> {e.message}{" "}
                  <span className="text-muted-foreground">({e.sheet})</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {(summary.symptoms.length > 0 || summary.measurements.length > 0) && (
        <div className="border">
          <button
            type="button"
            onClick={() => setShowSymptoms(!showSymptoms)}
            aria-expanded={showSymptoms}
            className="flex w-full items-center justify-between gap-[1ch] px-[1ch] py-2 text-left hover:bg-accent"
          >
            <span className="text-xs font-bold tracking-wider uppercase">
              <span className="mr-[1ch]">{showSymptoms ? "▾" : "▸"}</span>
              {summary.mode === "review" ? "Notes on the board" : "Symptoms"}
              {summary.measurements.length > 0 ? " and measurements" : ""}
            </span>
          </button>
          {showSymptoms && (
            <ol className="space-y-1 border-t p-[1ch] text-xs">
              {summary.symptoms.map((s) => (
                <li key={s.id} className="flex gap-[1ch]">
                  <span className="w-[4ch] shrink-0 font-bold text-muted-foreground">{s.id}</span>
                  <span>{s.text}</span>
                </li>
              ))}
              {summary.measurements.map((m) => (
                <li key={m.id} className="flex gap-[1ch]">
                  <span className="w-[4ch] shrink-0 font-bold text-muted-foreground">{m.id}</span>
                  <span>
                    <span className="text-value">{m.value}</span>
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

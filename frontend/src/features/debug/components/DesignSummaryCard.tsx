import { useState, type ReactNode } from "react"

import { Badge } from "@/shared/ui/badge"
import { Card } from "@/shared/ui/card"
import { Title } from "@/shared/ui/title"

import { useMessages } from "../messages"
import {
  calculationBadge,
  datasheetBadge,
  datasheetLines,
  findingTokens,
  modelBadge,
  powerRows,
  type StatusBadge,
} from "../present"
import type { DesignSummary } from "../types"
import { DatasheetLineText } from "./DatasheetLineText"
import { MarkedText } from "./MarkedText"

/** A sub-heading as a named rule: `LABEL ─────`, like aitop's machine rows. */
function Rule({ children }: { children: ReactNode }) {
  return (
    <h3 className="flex items-center gap-[1ch] text-xs font-bold tracking-wider uppercase">
      <span className="shrink-0">{children}</span>
      <span className="min-w-[2ch] flex-1 border-t" aria-hidden />
    </h3>
  )
}

/** An IC's datasheet or model status: the badge (a link to the PDF when
 *  there is one), the pages used, and the reason in muted text. */
function StatusText({ badge }: { badge: StatusBadge }) {
  return (
    <span className="min-w-0">
      {badge.url ? (
        <a href={badge.url} target="_blank" rel="noreferrer" className="hover:underline">
          <Badge tone={badge.tone}>{badge.label}</Badge>
        </a>
      ) : (
        <Badge tone={badge.tone}>{badge.label}</Badge>
      )}
      {badge.pages && <span className="ml-[1ch] text-muted-foreground">{badge.pages}</span>}
      {badge.reason && <span className="ml-[1ch] text-muted-foreground">({badge.reason})</span>}
    </span>
  )
}

/** What the backend parsed, shown before the model answers so the user
 *  can check the parse and see which ICs have pin data. */
export function DesignSummaryCard({ summary }: { summary: DesignSummary }) {
  const [showErc, setShowErc] = useState(false)
  const [showSymptoms, setShowSymptoms] = useState(true)
  const [showRefs, setShowRefs] = useState(false)
  const [showFacts, setShowFacts] = useState(false)
  const read = datasheetLines(summary)
  const factCount = (summary.datasheet_facts ?? []).length + read.deviations.length
  const refParts = new Set(summary.references.map((n) => n.part))
  const m = useMessages()
  const t = m.summary
  const power = summary.power ? powerRows(summary.power, m) : []
  return (
    <Card className="space-y-5 p-4 sm:p-5">
      <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-start">
        <div>
          <Title>{t.title}</Title>
          <span className="ml-[1ch] font-bold text-info">{summary.project}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          {t.counts(summary.sheets, summary.parts, summary.nets, summary.ics.length)}
          <span className={summary.erc.length > 0 ? "text-warn" : "text-ok"}>
            {t.erc(summary.erc.length)}
          </span>
        </p>
      </div>

      <div className="space-y-2">
        <Rule>{t.icsAndPinData}</Rule>
        <ul className="space-y-1.5 text-xs">
          {summary.ics.map((ic) => {
            const sheet = datasheetBadge(ic.ref, summary, m)
            const model = modelBadge(ic.ref, summary, m)
            return (
              <li key={ic.ref} className="space-y-0.5">
                <div className="flex min-w-0 items-baseline gap-[2ch]">
                  <span className="shrink-0 font-bold text-info">
                    {ic.ref} · {ic.value}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {ic.footprint || t.noFootprint}
                  </span>
                  <span className="flex shrink-0 gap-[1ch]">
                    {refParts.has(ic.value) && <Badge tone="ok">{t.refDesign}</Badge>}
                    <Badge tone={ic.pin_data ? "ok" : "neutral"}>
                      {ic.pin_data ? t.pinTable : t.noPinData}
                    </Badge>
                  </span>
                </div>
                {(sheet ?? model) && (
                  <div className="flex flex-col gap-0.5 pl-[2ch] sm:flex-row sm:gap-[2ch]">
                    {sheet && <StatusText badge={sheet} />}
                    {model && <StatusText badge={model} />}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
        {summary.ics.some((ic) => !ic.pin_data) && (
          <p className="text-xs text-muted-foreground">{t.noPinDataNote}</p>
        )}
      </div>

      {summary.wiring.length > 0 && (
        <div className="space-y-2 border-l-2 border-bad pl-[1ch] text-xs">
          <span className="font-bold text-bad">{t.wired(summary.wiring.length)}</span>
          {summary.wiring.map((f) => (
            <p key={f.id} className="leading-relaxed">
              <MarkedText text={f.text} tokens={findingTokens(f)} />
            </p>
          ))}
        </div>
      )}

      {summary.pin_warnings.length > 0 && (
        <div className="space-y-2 border-l-2 border-warn pl-[1ch] text-xs">
          <span className="font-bold text-warn">{t.pinOrder(summary.pin_warnings.length)}</span>
          {summary.pin_warnings.map((w) => (
            <p key={w.id} className="leading-relaxed">
              <MarkedText text={w.text} tokens={findingTokens(w)} />
            </p>
          ))}
        </div>
      )}

      {summary.calculations.length > 0 && (
        <div className="space-y-2 text-xs">
          <Rule>{t.calculated(summary.calculations.length)}</Rule>
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
                {calculationBadge(c, m)}
              </Badge>
              <p className="min-w-0 flex-1 leading-relaxed">{c.text}</p>
            </div>
          ))}
        </div>
      )}

      {power.length > 0 && (
        <div className="space-y-2 text-xs">
          <Rule>{t.powerTree(power.length)}</Rule>
          <ul className="space-y-1.5">
            {power.map((r) => (
              <li key={r.net} className="space-y-0.5">
                <div className="flex flex-wrap items-baseline gap-x-[2ch]">
                  <span className="font-bold text-info">{r.net}</span>
                  {r.source && <Badge bracket>{r.source}</Badge>}
                  <span className="text-value">{r.voltage}</span>
                  {r.inferred && <Badge tone="warn">{t.inferred}</Badge>}
                  <span className="min-w-0 text-muted-foreground">{r.basis}</span>
                </div>
                {(r.from.length > 0 || r.loads.length > 0 || r.budget) && (
                  <div className="space-y-0.5 pl-[2ch] text-muted-foreground">
                    {r.from.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    {r.loads.length > 0 && (
                      <p>
                        {t.supplies} {r.loads.join(", ")}
                      </p>
                    )}
                    {r.budget && (
                      <p className="flex flex-wrap items-baseline gap-x-[1ch]">
                        {r.over && <Badge tone="warn">{t.overBudget}</Badge>}
                        <span className="min-w-0">{r.budget}</span>
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">{t.powerNote}</p>
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
              {t.references(summary.references.length)}
              <span className="text-info">{[...refParts].join(", ")}</span>
              {t.referencesEnd}
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

      {factCount > 0 && (
        <div className="border">
          <button
            type="button"
            onClick={() => setShowFacts(!showFacts)}
            aria-expanded={showFacts}
            className="flex w-full items-center justify-between gap-[1ch] px-[1ch] py-2 text-left hover:bg-accent"
          >
            <span className="text-xs font-bold">
              <span className="mr-[1ch]">{showFacts ? "▾" : "▸"}</span>
              {t.datasheetFacts(factCount)}
            </span>
          </button>
          {showFacts && (
            <div className="space-y-3 border-t p-[1ch] text-xs">
              {read.facts.map((group) => (
                <div key={group.ref} className="space-y-0.5">
                  <p className="font-bold text-info">{group.ref}</p>
                  <ul className="space-y-0.5">
                    {group.lines.map((line) => (
                      <li key={line.id} className="leading-relaxed">
                        <DatasheetLineText line={line} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {read.deviations.length > 0 && (
                <div className="space-y-0.5 border-l-2 border-warn pl-[1ch]">
                  <p className="font-bold text-warn">{t.deviations(read.deviations.length)}</p>
                  <ul className="space-y-0.5">
                    {read.deviations.map((line) => (
                      <li key={line.id} className="leading-relaxed">
                        <DatasheetLineText line={line} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="border-l-2 border-warn pl-[1ch] text-xs">
        <div className="flex items-center justify-between gap-[1ch]">
          <span className="font-bold text-warn">{t.ercFindings(summary.erc.length)}</span>
          {summary.erc.length > 0 && (
            <button
              type="button"
              onClick={() => setShowErc(!showErc)}
              aria-expanded={showErc}
              className="text-warn hover:underline"
            >
              {showErc ? t.hide : t.inspect}
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
              {summary.mode === "review" ? t.notes : t.symptoms}
              {summary.measurements.length > 0 ? t.andMeasurements : ""}
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
              {summary.measurements.map((x) => (
                <li key={x.id} className="flex gap-[1ch]">
                  <span className="w-[4ch] shrink-0 font-bold text-muted-foreground">{x.id}</span>
                  <span>
                    <span className="text-value">{x.value}</span>
                    {x.step_id && (
                      <span className="text-muted-foreground">{t.step(x.step_id)}</span>
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

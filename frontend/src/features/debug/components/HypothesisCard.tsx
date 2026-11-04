import { useState, type ReactNode } from "react"

import { cn } from "@/shared/lib/utils"
import { Badge, type BadgeProps } from "@/shared/ui/badge"
import { Button } from "@/shared/ui/button"
import { Prompt } from "@/shared/ui/prompt"

import {
  assess,
  confidenceLabel,
  EVIDENCE_LABEL,
  isReview,
  SEVERITY_LABEL,
  symptomText,
  type Assessment,
  type Confidence,
} from "../present"
import type {
  CheckedClaim,
  ConfirmStep,
  DesignSummary,
  Diagram,
  Evidence,
  GlossaryEntry,
  Hypothesis,
  Problem,
  Severity,
  Simulation,
} from "../types"
import { GlossaryText } from "./GlossaryText"
import { SimulationPlot } from "./SimulationPlot"
import { WiringDiagram } from "./WiringDiagram"

const EVIDENCE_TONE: Record<Evidence, BadgeProps["tone"]> = {
  schematic: "info",
  erc: "warn",
  datasheet: "ok",
  reference: "ok",
  measurement: "ok",
  none: "neutral",
}

const CONFIDENCE_TONE: Record<Confidence, BadgeProps["tone"]> = {
  verified: "ok",
  fits: "info",
  speculative: "neutral",
}

const SEVERITY_TONE: Record<Severity, BadgeProps["tone"]> = {
  will_fail: "alert",
  may_fail: "warn",
  improve: "neutral",
}

const CONFIDENCE_GLYPH: Record<Confidence, string> = {
  verified: "✓",
  fits: "●",
  speculative: "?",
}

const METER_CELLS = 10

/** "N of M" as text plus an htop-style bar, `[|||||     ]`. Counts, never a
 *  percentage. */
function Meter({ label, value, of }: { label: string; value: number; of: number }) {
  const share = of > 0 ? Math.min(value / of, 1) : 0
  const filled = Math.round(share * METER_CELLS)
  return (
    <p
      className="text-xs whitespace-pre text-muted-foreground"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={of}
      aria-valuenow={value}
    >
      {label} [<span className="text-foreground">{"|".repeat(filled)}</span>
      {" ".repeat(METER_CELLS - filled)}]{" "}
      <span className="font-bold text-foreground tabular-nums">
        {value}/{of}
      </span>
    </p>
  )
}

function ConfidenceLine({ assessment: a, review }: { assessment: Assessment; review: boolean }) {
  return (
    <div className="mt-3 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-[3ch]">
      <Badge tone={CONFIDENCE_TONE[a.confidence]} className="self-start font-bold" title={a.reason}>
        {CONFIDENCE_GLYPH[a.confidence]} {confidenceLabel(a.confidence, review)}
      </Badge>
      <div className="flex flex-wrap gap-x-[3ch] gap-y-1">
        {!review && <Meter label="Explains" value={a.explained} of={a.symptoms} />}
        <Meter label="Claims backed" value={a.backed} of={a.claims} />
      </div>
    </div>
  )
}

function Disclosure({
  title,
  aside,
  defaultOpen = false,
  accent = false,
  children,
}: {
  title: string
  aside?: string
  defaultOpen?: boolean
  accent?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-[1ch] px-4 py-2 text-left hover:bg-accent sm:px-5"
      >
        <span
          className={cn(
            "flex items-center gap-[1ch] text-xs font-bold tracking-wider uppercase",
            accent && "text-info",
          )}
        >
          <span>{open ? "▾" : "▸"}</span>
          {title}
        </span>
        {aside && <span className="text-right text-xs text-muted-foreground">{aside}</span>}
      </button>
      {open && <div className="space-y-3 px-4 pt-1 pb-4 text-xs sm:px-5">{children}</div>}
    </div>
  )
}

function ClaimRow({ claim }: { claim: CheckedClaim }) {
  return (
    <div className="flex flex-col gap-1 border-l-2 pl-[1ch] sm:flex-row sm:items-start sm:gap-[1ch]">
      <Badge tone={EVIDENCE_TONE[claim.evidence]} bracket className="self-start">
        {EVIDENCE_LABEL[claim.evidence]}
      </Badge>
      <div className="space-y-1">
        <p className="leading-relaxed">{claim.text}</p>
        {claim.downgraded && claim.claimed_evidence && (
          <p className="text-warn">
            ! Labelled &ldquo;{EVIDENCE_LABEL[claim.claimed_evidence]}&rdquo; by the model, but the
            check failed: {claim.reason}
          </p>
        )}
      </div>
    </div>
  )
}

function StepView({
  step,
  busy,
  review,
  onRerun,
}: {
  step: ConfirmStep
  busy: boolean
  /** No board yet, so no reading to enter. */
  review: boolean
  onRerun: (step: ConfirmStep, value: string) => void
}) {
  const [value, setValue] = useState("")
  return (
    <div className="space-y-3 border p-3">
      <div className="flex flex-wrap items-center gap-[1ch]">
        <Badge tone="solid">{step.id}</Badge>
        <Badge tone={step.power === "off" ? "neutral" : "bad"}>power {step.power}</Badge>
        <Badge>· {step.tool === "look" ? "look" : step.tool}</Badge>
        <Badge tone={step.purpose === "test_cause" ? "info" : "neutral"}>
          ·{" "}
          {step.purpose === "test_cause"
            ? "tests the cause"
            : "checks the board matches the schematic"}
        </Badge>
        {!step.practical && <Badge tone="warn">! hard by hand</Badge>}
      </div>
      <p className="text-sm leading-relaxed">{step.instruction}</p>
      {step.practical_note && <p className="text-xs text-warn">{step.practical_note}</p>}
      {step.setup.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-bold tracking-wider text-muted-foreground uppercase">
            Rewire before power
          </p>
          <ul className="space-y-0.5 text-xs">
            {step.setup.map((c, i) => (
              <li key={i}>
                {c.action === "disconnect" ? "✂ " : "↔ "}
                <span className="text-info">{c.pin}</span>
                {c.action === "disconnect" ? " from " : " to "}
                {c.action === "connect" && c.to_pin ? (
                  <span className="text-info">{c.to_pin}</span>
                ) : (
                  <span className="text-net">{c.net ?? "?"}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {step.readings.length > 0 && (
        <table className="w-full border-collapse text-left">
          <thead className="bg-foreground text-xs text-background uppercase">
            <tr>
              <th className="px-[1ch] py-0.5 font-bold">If you see</th>
              <th className="px-[1ch] py-0.5 font-bold">It means</th>
            </tr>
          </thead>
          <tbody>
            {step.readings.map((r, i) => (
              <tr key={i} className="align-top">
                <td className="border-b px-[1ch] py-1.5 font-bold text-value">{r.reading}</td>
                <td className="border-b px-[1ch] py-1.5">
                  {r.establishes}
                  {(r.supports.length > 0 || r.rules_out.length > 0) && (
                    <span className="mt-1 flex flex-wrap gap-[1ch]">
                      {r.supports.map((id) => (
                        <Badge key={`s-${id}`} tone="ok">
                          supports {id}
                        </Badge>
                      ))}
                      {r.rules_out.map((id) => (
                        <Badge key={`r-${id}`} tone="bad">
                          rules out {id}
                        </Badge>
                      ))}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!review && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (!value.trim()) return
            onRerun(step, value.trim())
            setValue("")
          }}
        >
          <label className="block">
            <span className="block text-xs font-bold tracking-wider text-muted-foreground uppercase">
              Your reading
            </span>
            <span className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Prompt
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="e.g. 3.28 V at TP11"
                maxLength={500}
              />
              <Button type="submit" size="sm" disabled={busy || !value.trim()}>
                Re-run with this reading
              </Button>
            </span>
          </label>
        </form>
      )}
    </div>
  )
}

interface HypothesisCardProps {
  hypothesis: Hypothesis
  diagram: Diagram | null
  summary: DesignSummary | null
  simulations: Simulation[]
  glossary: GlossaryEntry[]
  problems: Problem[]
  latest: boolean
  busy: boolean
  onRerun: (step: ConfirmStep, value: string) => void
}

export function HypothesisCard({
  hypothesis: h,
  diagram,
  summary,
  simulations,
  glossary,
  problems,
  latest,
  busy,
  onRerun,
}: HypothesisCardProps) {
  const primary = h.rank === 1
  const stepIds = new Set(h.confirm.map((s) => s.id))
  const ownProblems = problems.filter((p) => p.where === h.id || stepIds.has(p.where))
  const assessment = assess(h, summary)
  const review = isReview(summary)
  return (
    <div className={cn("border bg-card", primary && "border-foreground")}>
      <div className="p-4 sm:p-5">
        <div className="flex items-start gap-[1ch]">
          <Badge tone={primary ? "solid" : "neutral"} className="mt-0.5 font-bold">
            #{h.rank}
          </Badge>
          {h.severity && (
            <Badge tone={SEVERITY_TONE[h.severity]} className="mt-0.5 shrink-0 font-bold">
              {SEVERITY_LABEL[h.severity]}
            </Badge>
          )}
          <h3 className="text-sm font-bold">{h.title}</h3>
        </div>
        <p className="pt-2 text-sm leading-relaxed">
          <GlossaryText text={h.summary} glossary={glossary} />
        </p>
        <ConfidenceLine assessment={assessment} review={review} />
        {diagram && (
          <div className="mt-4 border p-3">
            <WiringDiagram diagram={diagram} />
          </div>
        )}
      </div>

      <div className="divide-y border-t">
        {(h.explains.length > 0 || h.does_not_explain.length > 0) && (
          <Disclosure
            title="Symptoms"
            aside={`${String(h.explains.length)} explained, ${String(h.does_not_explain.length)} not`}
          >
            <ul className="space-y-1.5 leading-relaxed">
              {h.explains.map((id) => (
                <li key={id} className="flex gap-[1ch]" title={id}>
                  <span className="shrink-0 text-ok">✓</span>
                  <span>{symptomText(summary, id)}</span>
                </li>
              ))}
              {h.does_not_explain.map((id) => (
                <li key={id} className="flex gap-[1ch]" title={id}>
                  <span className="shrink-0 font-bold text-bad">!</span>
                  <span>
                    <span className="font-bold">Doesn&apos;t explain:</span>{" "}
                    {symptomText(summary, id)}
                  </span>
                </li>
              ))}
            </ul>
          </Disclosure>
        )}

        <Disclosure
          title="Evidence and claims"
          aside={`${String(assessment.backed)} of ${String(assessment.claims)} backed by your input`}
        >
          {assessment.findings.map((f) => (
            <div
              key={f.id}
              className="flex gap-[1ch] border-l-2 border-ok pl-[1ch] leading-relaxed"
            >
              <span className="shrink-0 text-ok">✓</span>
              <span>
                <span className="font-bold text-ok">Backend wiring check:</span> {f.text}
              </span>
            </div>
          ))}
          {assessment.calculations.map((c) => (
            <div
              key={c.id}
              className="flex gap-[1ch] border-l-2 border-ok pl-[1ch] leading-relaxed"
            >
              <span className="shrink-0 text-ok">✓</span>
              <div className="min-w-0 flex-1 space-y-2">
                <p>
                  <span className="font-bold text-ok">Backend calculation:</span> {c.text}
                </p>
                {simulations
                  .filter((s) => s.calc_id === c.id)
                  .map((s) => (
                    <SimulationPlot key={s.id} sim={s} />
                  ))}
              </div>
            </div>
          ))}
          {h.claims.map((c, i) => (
            <ClaimRow key={i} claim={c} />
          ))}
        </Disclosure>

        {h.confirm.length > 0 && (
          <Disclosure
            title={review ? "Check before you order" : "Check this with a multimeter"}
            aside={`${String(h.confirm.length)} ${h.confirm.length === 1 ? "step" : "steps"}`}
            defaultOpen={primary && latest}
            accent
          >
            {h.confirm.map((step) => (
              <StepView
                key={step.id}
                step={step}
                busy={busy || !latest}
                review={review}
                onRerun={onRerun}
              />
            ))}
            {!latest && !review && (
              <p className="text-muted-foreground">Enter readings on the latest run.</p>
            )}
          </Disclosure>
        )}

        <Disclosure
          title={
            review
              ? "Fix in the schematic before ordering"
              : "Fix on the bench and in the next revision"
          }
        >
          {h.fix.rework.length > 0 && (
            <div className="space-y-1.5 border-l-2 border-warn pl-[1ch]">
              <span className="font-bold text-warn">Rework on this board</span>
              <ol className="list-decimal space-y-1 pl-4 leading-relaxed">
                {h.fix.rework.map((r, i) => (
                  <li key={i}>
                    {r.text}
                    {!r.practical && r.practical_note && (
                      <span className="block text-warn">! Hard by hand: {r.practical_note}</span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}
          {h.fix.schematic.length > 0 && (
            <div className="space-y-1.5 border-l-2 border-info pl-[1ch]">
              <span className="font-bold text-info">
                {review ? "Schematic change" : "Next schematic revision"}
              </span>
              <ul className="list-disc space-y-1 pl-4 leading-relaxed">
                {h.fix.schematic.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-muted-foreground">
            {review
              ? "Unverified: settle the issue with the checks above before changing the design."
              : "Rework is unverified: confirm the cause with the steps above first."}
          </p>
        </Disclosure>
      </div>

      {ownProblems.length > 0 && (
        <div className="border-t px-4 py-2.5 text-xs text-warn sm:px-5">
          {ownProblems.map((p, i) => (
            <p key={i}>
              {p.where}: {p.message}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

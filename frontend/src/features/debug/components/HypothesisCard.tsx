import {
  CircleAlert,
  CircleCheck,
  CircleHelp,
  FileCode,
  Gauge,
  Hand,
  RotateCcw,
  ShieldCheck,
  Wrench,
} from "lucide-react"
import { useState, type ReactNode } from "react"

import { cn } from "@/shared/lib/utils"
import { Badge, type BadgeProps } from "@/shared/ui/badge"
import { Button } from "@/shared/ui/button"

import {
  assess,
  CONFIDENCE_LABEL,
  EVIDENCE_LABEL,
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
} from "../types"
import { GlossaryText } from "./GlossaryText"
import { WiringDiagram } from "./WiringDiagram"

const EVIDENCE_TONE: Record<Evidence, BadgeProps["tone"]> = {
  schematic: "blue",
  erc: "amber",
  datasheet: "green",
  measurement: "green",
  none: "neutral",
}

const CONFIDENCE_TONE: Record<Confidence, BadgeProps["tone"]> = {
  verified: "green",
  fits: "blue",
  speculative: "neutral",
}

const CONFIDENCE_ICON: Record<Confidence, typeof ShieldCheck> = {
  verified: ShieldCheck,
  fits: CircleCheck,
  speculative: CircleHelp,
}

/** "N of M" as text plus a thin bar. Counts, never a percentage. */
function Meter({ label, value, of }: { label: string; value: number; of: number }) {
  const share = of > 0 ? Math.min(value / of, 1) : 0
  return (
    <div className="min-w-0 flex-1 space-y-1">
      <p className="text-[11px] text-muted-foreground">
        {label}{" "}
        <span className="font-semibold text-foreground tabular-nums">
          {value} of {of}
        </span>
      </p>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={of}
        aria-valuenow={value}
      >
        <div
          className="h-full rounded-full bg-slate-600 dark:bg-slate-300"
          style={{ width: `${String(share * 100)}%` }}
        />
      </div>
    </div>
  )
}

function ConfidenceLine({ assessment: a }: { assessment: Assessment }) {
  const Icon = CONFIDENCE_ICON[a.confidence]
  return (
    <div className="mt-3.5 flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-5">
      <Badge tone={CONFIDENCE_TONE[a.confidence]} className="self-start text-xs" title={a.reason}>
        <Icon />
        {CONFIDENCE_LABEL[a.confidence]}
      </Badge>
      <div className="flex flex-1 gap-5">
        <Meter label="Explains" value={a.explained} of={a.symptoms} />
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
        className={cn(
          "flex w-full items-center justify-between px-5 py-3 text-left transition-colors sm:px-6",
          accent
            ? "bg-blue-50/40 hover:bg-blue-50/80 dark:bg-blue-950/20 dark:hover:bg-blue-950/40"
            : "bg-background/60 hover:bg-accent",
        )}
      >
        <span
          className={cn(
            "flex items-center gap-1.5 text-xs font-bold tracking-wider uppercase",
            accent && "text-blue-900 dark:text-sky-300",
          )}
        >
          <span>{open ? "▾" : "▸"}</span>
          {title}
        </span>
        {aside && <span className="text-xs font-medium text-muted-foreground">{aside}</span>}
      </button>
      {open && <div className="space-y-3 p-5 text-xs sm:p-6">{children}</div>}
    </div>
  )
}

function ClaimRow({ claim }: { claim: CheckedClaim }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-background p-3 sm:flex-row sm:items-start sm:gap-3">
      <Badge tone={EVIDENCE_TONE[claim.evidence]} className="self-start">
        {EVIDENCE_LABEL[claim.evidence]}
      </Badge>
      <div className="space-y-1">
        <p className="leading-relaxed">{claim.text}</p>
        {claim.downgraded && claim.claimed_evidence && (
          <p className="flex items-start gap-1 text-[11px] text-amber-700 dark:text-amber-300">
            <CircleAlert className="mt-px h-3 w-3 shrink-0" />
            <span>
              Labelled &ldquo;{EVIDENCE_LABEL[claim.claimed_evidence]}&rdquo; by the model, but the
              check failed: {claim.reason}
            </span>
          </p>
        )}
      </div>
    </div>
  )
}

function StepView({
  step,
  busy,
  onRerun,
}: {
  step: ConfirmStep
  busy: boolean
  onRerun: (step: ConfirmStep, value: string) => void
}) {
  const [value, setValue] = useState("")
  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="solid">{step.id}</Badge>
        <Badge tone={step.power === "off" ? "neutral" : "red"}>Power {step.power}</Badge>
        <Badge>{step.tool === "look" ? "Look" : step.tool}</Badge>
        <Badge tone={step.purpose === "test_cause" ? "blue" : "neutral"}>
          {step.purpose === "test_cause"
            ? "Tests the cause"
            : "Checks the board matches the schematic"}
        </Badge>
        {!step.practical && (
          <Badge tone="amber">
            <Hand />
            Hard by hand
          </Badge>
        )}
      </div>
      <p className="text-sm leading-relaxed">{step.instruction}</p>
      {step.practical_note && (
        <p className="text-[11px] text-muted-foreground italic">{step.practical_note}</p>
      )}
      {step.setup.length > 0 && (
        <div>
          <p className="mb-1 font-mono text-[11px] font-bold tracking-wider text-muted-foreground uppercase">
            Rewire before power
          </p>
          <ul className="space-y-0.5 font-mono text-[11px]">
            {step.setup.map((c, i) => (
              <li key={i}>
                {c.action === "disconnect"
                  ? `✂ ${c.pin} from ${c.net ?? "?"}`
                  : `↔ ${c.pin} to ${c.to_pin ?? c.net ?? "?"}`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {step.readings.length > 0 && (
        <table className="w-full border-separate border-spacing-0 overflow-hidden rounded-md border text-left">
          <thead className="bg-background text-[11px] text-muted-foreground uppercase">
            <tr>
              <th className="px-3 py-1.5 font-semibold">If you see</th>
              <th className="px-3 py-1.5 font-semibold">It means</th>
            </tr>
          </thead>
          <tbody>
            {step.readings.map((r, i) => (
              <tr key={i} className="align-top">
                <td className="border-t px-3 py-2 font-mono">{r.reading}</td>
                <td className="border-t px-3 py-2">
                  {r.establishes}
                  {(r.supports.length > 0 || r.rules_out.length > 0) && (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {r.supports.map((id) => (
                        <Badge key={`s-${id}`} tone="green">
                          supports {id}
                        </Badge>
                      ))}
                      {r.rules_out.map((id) => (
                        <Badge key={`r-${id}`} tone="red">
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
      <form
        className="flex flex-col gap-2 rounded-lg border bg-background p-3 sm:flex-row sm:items-end"
        onSubmit={(e) => {
          e.preventDefault()
          if (!value.trim()) return
          onRerun(step, value.trim())
          setValue("")
        }}
      >
        <label className="flex-1">
          <span className="mb-1 block text-[11px] font-bold tracking-wider text-muted-foreground uppercase">
            Your reading
          </span>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="e.g. 3.28 V at TP11"
            maxLength={500}
            className="w-full rounded-md border bg-card px-3 py-1.5 font-mono text-sm focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none"
          />
        </label>
        <Button type="submit" size="sm" disabled={busy || !value.trim()}>
          <RotateCcw />
          Re-run with this reading
        </Button>
      </form>
    </div>
  )
}

interface HypothesisCardProps {
  hypothesis: Hypothesis
  diagram: Diagram | null
  summary: DesignSummary | null
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
  return (
    <div
      className={cn(
        "rounded-xl border bg-card elevation-1",
        primary &&
          "border-blue-400 ring-1 ring-blue-200 dark:border-blue-500 dark:ring-blue-900/60",
      )}
    >
      <div className="p-5 sm:p-6">
        <div className="flex items-start gap-2.5">
          <Badge tone={primary ? "solid" : "neutral"} className="mt-0.5 text-xs">
            #{h.rank}
          </Badge>
          <h3 className="text-base font-bold tracking-tight sm:text-lg">{h.title}</h3>
        </div>
        <p className="pt-2 text-sm leading-relaxed text-foreground/90">
          <GlossaryText text={h.summary} glossary={glossary} />
        </p>
        <ConfidenceLine assessment={assessment} />
        {diagram && (
          <div className="mt-4 rounded-lg border bg-background p-3">
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
                <li key={id} className="flex gap-2" title={id}>
                  <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  <span>{symptomText(summary, id)}</span>
                </li>
              ))}
              {h.does_not_explain.map((id) => (
                <li key={id} className="flex gap-2" title={id}>
                  <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600 dark:text-rose-400" />
                  <span>
                    <span className="font-semibold">Doesn&apos;t explain:</span>{" "}
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
              className="flex gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 leading-relaxed dark:border-emerald-900 dark:bg-emerald-950/30"
            >
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-700 dark:text-emerald-400" />
              <span>
                <span className="font-semibold">Backend wiring check:</span> {f.text}
              </span>
            </div>
          ))}
          {h.claims.map((c, i) => (
            <ClaimRow key={i} claim={c} />
          ))}
          <p className="text-[11px] text-muted-foreground">
            A label means the cited pins, nets, ERC entries or readings exist in your input. It
            doesn&apos;t mean the conclusion was verified.
          </p>
        </Disclosure>

        {h.confirm.length > 0 && (
          <Disclosure
            title="Check this with a multimeter"
            aside={`${String(h.confirm.length)} ${h.confirm.length === 1 ? "step" : "steps"}`}
            defaultOpen={primary && latest}
            accent
          >
            {h.confirm.map((step) => (
              <StepView key={step.id} step={step} busy={busy || !latest} onRerun={onRerun} />
            ))}
            {!latest && (
              <p className="text-[11px] text-muted-foreground">Enter readings on the latest run.</p>
            )}
          </Disclosure>
        )}

        <Disclosure title="Fix on the bench and in the next revision">
          {h.fix.rework.length > 0 && (
            <div className="space-y-1.5 border-l-2 border-amber-400 pl-3">
              <span className="flex items-center gap-1.5 font-bold text-amber-800 dark:text-amber-300">
                <Wrench className="h-3.5 w-3.5" />
                Rework on this board
              </span>
              <ol className="list-decimal space-y-1 pl-4 leading-relaxed">
                {h.fix.rework.map((r, i) => (
                  <li key={i}>
                    {r.text}
                    {!r.practical && r.practical_note && (
                      <span className="block text-[11px] text-amber-700 dark:text-amber-300">
                        Hard by hand: {r.practical_note}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}
          {h.fix.schematic.length > 0 && (
            <div className="space-y-1.5 border-l-2 border-blue-400 pl-3">
              <span className="flex items-center gap-1.5 font-bold text-blue-800 dark:text-sky-300">
                <FileCode className="h-3.5 w-3.5" />
                Next schematic revision
              </span>
              <ul className="list-disc space-y-1 pl-4 leading-relaxed">
                {h.fix.schematic.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Gauge className="h-3 w-3" />
            Rework is unverified: confirm the cause with the steps above first.
          </p>
        </Disclosure>
      </div>

      {ownProblems.length > 0 && (
        <div className="border-t bg-amber-50/50 px-5 py-3 text-[11px] text-amber-800 sm:px-6 dark:bg-amber-950/20 dark:text-amber-300">
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

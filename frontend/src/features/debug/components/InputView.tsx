import { useRef, useState } from "react"

import { cn } from "@/shared/lib/utils"
import { Badge } from "@/shared/ui/badge"
import { Button, buttonVariants } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"
import { Textarea } from "@/shared/ui/textarea"
import { Title } from "@/shared/ui/title"

import { projectName } from "../files"
import { useMessages } from "../messages"
import { SIM_ANALYSES, useDebugStore } from "../store"
import { fragmentLines, symptomHints, symptomLines } from "../symptoms"
import type { Mode } from "../types"
import type { DebugActions } from "../useDebug"

const MODES: Mode[] = ["review", "debug"]

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  const t = useMessages().input
  return (
    <div
      role="radiogroup"
      aria-label={t.modeAria}
      className="grid grid-cols-1 gap-2 sm:grid-cols-2"
    >
      {MODES.map((key) => {
        const active = key === mode
        const m = t.modes[key]
        return (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(key)}
            className={cn(
              "flex items-start gap-[1ch] border px-[1ch] py-2 text-left hover:bg-accent",
              active && "border-foreground",
            )}
          >
            <span className={cn("shrink-0", active ? "font-bold" : "text-muted-foreground")}>
              {active ? "(•)" : "( )"}
            </span>
            <span>
              <span className={cn("block text-sm", active && "font-bold")}>{m.label}</span>
              <span className="block text-xs text-muted-foreground">{m.detail}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** A checkbox per vendor-model analysis (start-up ticked by default). */
function AnalysisPicker() {
  const m = useMessages()
  const analyses = useDebugStore((s) => s.analyses)
  const toggle = useDebugStore((s) => s.toggleAnalysis)
  return (
    <fieldset className="space-y-1.5 text-xs">
      <legend className="font-bold tracking-wider text-muted-foreground uppercase">
        {m.input.analysesTitle}
      </legend>
      <div className="flex flex-wrap gap-x-[2ch] gap-y-1">
        {SIM_ANALYSES.map((a) => (
          <label key={a} className="flex cursor-pointer items-center gap-[1ch]">
            <input
              type="checkbox"
              checked={analyses.includes(a)}
              onChange={() => toggle(a)}
              className="accent-foreground"
            />
            {m.simulations.analysis[a]}
          </label>
        ))}
      </div>
      <p className="text-muted-foreground">{m.input.analysesNote}</p>
    </fieldset>
  )
}

export function InputView({ actions }: { actions: DebugActions }) {
  const mode = useDebugStore((s) => s.mode)
  const setMode = useDebugStore((s) => s.setMode)
  const review = mode === "review"
  const files = useDebugStore((s) => s.files)
  const symptoms = useDebugStore((s) => s.symptoms)
  const setSymptoms = useDebugStore((s) => s.setSymptoms)
  const setFiles = useDebugStore((s) => s.setFiles)
  const inputError = useDebugStore((s) => s.inputError)
  const hasRuns = useDebugStore((s) => s.runs.length > 0)
  const showSession = useDebugStore((s) => s.showSession)
  const [dragging, setDragging] = useState(false)
  const [showFiles, setShowFiles] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messages = useMessages()
  const t = messages.input
  const kind = review ? "review" : "debug"

  const name = projectName(files)
  const sheets = files.filter((f) => f.path.endsWith(".kicad_sch")).length
  const hints = symptomHints(symptoms, messages)
  const lines = symptomLines(symptoms)
  const fragments = fragmentLines(symptoms)

  const insertPrompt = (prefix: string) => {
    const current = symptoms.trimEnd()
    setSymptoms(current ? `${current}\n${prefix} ` : `${prefix} `)
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (!el) return
      el.focus()
      el.setSelectionRange(el.value.length, el.value.length)
      el.scrollTop = el.scrollHeight
    })
  }

  return (
    <div className="animate-fade-in space-y-6 sm:space-y-8">
      <div>
        <h1 className="font-bold">{t.heading[kind]}</h1>
        <p className="mt-1 text-muted-foreground">{t.intro[kind]}</p>
      </div>

      <ModeSwitch mode={mode} onChange={setMode} />

      <Card
        className={cn(
          "border-dashed p-4 sm:p-5",
          dragging && "border-solid border-foreground bg-accent",
        )}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          void actions.dropFiles(e.dataTransfer)
        }}
      >
        <div className="flex flex-wrap items-center gap-[1ch]">
          <h2 className="text-sm font-bold">{t.dropTitle}</h2>
          <span className="text-xs text-muted-foreground">{t.or}</span>
          <label
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "focus-within:outline-1 focus-within:outline-offset-2 focus-within:outline-foreground",
            )}
          >
            {t.chooseFolder}
            <input
              type="file"
              className="hidden"
              // Non-standard but supported by every current browser.
              {...{ webkitdirectory: "" }}
              multiple
              onChange={(e) => {
                if (e.target.files) actions.pickFiles(e.target.files)
                e.target.value = ""
              }}
            />
          </label>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {t.sentBefore}
          <span className="text-foreground">.kicad_pro</span>
          {t.sentAnd}
          <span className="text-foreground">.kicad_sch</span>
          {t.sentAfter}
        </p>

        {name && (
          <div className="mt-2.5 flex flex-wrap items-center gap-[2ch] text-xs">
            <span>
              <span className="text-ok">✓</span> <span className="font-bold text-info">{name}</span>{" "}
              <span className="text-muted-foreground">· {t.sheets(sheets)}</span>
            </span>
            <button
              type="button"
              onClick={() => setShowFiles(!showFiles)}
              aria-expanded={showFiles}
              className="text-muted-foreground hover:text-foreground"
            >
              {showFiles ? t.hideFiles : t.showFiles}
            </button>
            <button
              type="button"
              onClick={() => setFiles([])}
              className="text-muted-foreground hover:text-bad"
            >
              {t.remove}
            </button>
          </div>
        )}

        {showFiles && files.length > 0 && (
          <ul className="mt-3 space-y-0.5 border-t pt-3 text-xs">
            {files.map((f) => (
              <li key={f.path} className="truncate">
                {f.path}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="space-y-4 p-4 sm:p-5">
        <div className="flex items-start justify-between gap-[2ch]">
          <div>
            <Title>{review ? t.notesTitle : t.benchTitle}</Title>
            {!review && <p className="mt-1.5 text-xs text-muted-foreground">{t.oneObservation}</p>}
          </div>
          <button
            type="button"
            onClick={() => setSymptoms("")}
            className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
          >
            {t.clear}
          </button>
        </div>

        {!review && (
          <>
            <div className="flex flex-col gap-1 text-xs sm:flex-row sm:items-baseline sm:gap-[2ch]">
              <span className="shrink-0 font-bold tracking-wider text-muted-foreground uppercase">
                {t.covered(hints.filter((h) => h.done).length, hints.length)}
              </span>
              <div className="flex flex-wrap gap-x-[1ch]">
                {hints.map((h) => (
                  <Badge key={h.key} tone={h.done ? "ok" : "neutral"}>
                    {h.done ? "✓" : "○"} {h.label}
                  </Badge>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-baseline gap-x-[2ch] gap-y-1 text-xs">
              <span className="font-bold tracking-wider text-muted-foreground uppercase">
                {t.startLine}
              </span>
              {messages.symptoms.prompts.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => insertPrompt(p.prefix)}
                  className="text-info hover:underline"
                >
                  +{p.label}
                </button>
              ))}
            </div>
          </>
        )}

        <Textarea
          ref={textareaRef}
          rows={9}
          value={symptoms}
          onChange={(e) => setSymptoms(e.target.value)}
          placeholder={review ? messages.symptoms.reviewPlaceholder : messages.symptoms.placeholder}
          className="leading-relaxed"
        />

        {!review && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>{t.symptoms(lines.length)}</span>
            {fragments.length > 0 && <span className="text-warn">{t.shortLines(fragments)}</span>}
          </div>
        )}

        <AnalysisPicker />

        {inputError && (
          <div className="text-sm" role="alert">
            <span className="bg-bad px-[1ch] font-bold text-on-state">{t.error}</span> {inputError}
          </div>
        )}

        <div className="flex justify-end border-t pt-3">
          <div className="flex w-full gap-[1ch] sm:w-auto">
            {hasRuns && (
              <Button variant="outline" onClick={showSession}>
                {t.backToResults}
              </Button>
            )}
            <Button size="lg" className="w-full sm:w-auto" onClick={() => void actions.analyze()}>
              {t.submit[kind]} →
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}

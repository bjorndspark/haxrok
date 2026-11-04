import { useRef, useState } from "react"

import { cn } from "@/shared/lib/utils"
import { Badge } from "@/shared/ui/badge"
import { Button, buttonVariants } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"
import { Textarea } from "@/shared/ui/textarea"
import { Title } from "@/shared/ui/title"

import { projectName } from "../files"
import { useDebugStore } from "../store"
import {
  REVIEW_PLACEHOLDER,
  SYMPTOM_PLACEHOLDER,
  SYMPTOM_PROMPTS,
  fragmentLines,
  symptomHints,
  symptomLines,
} from "../symptoms"
import type { Mode } from "../types"
import type { DebugActions } from "../useDebug"

const MODES: { mode: Mode; label: string; detail: string }[] = [
  {
    mode: "review",
    label: "Review before you order",
    detail: "Catch schematic mistakes before you pay for boards.",
  },
  {
    mode: "debug",
    label: "Debug a board on the bench",
    detail: "It's built and misbehaves. Find the cause.",
  },
]

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div role="radiogroup" aria-label="Mode" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {MODES.map((m) => {
        const active = m.mode === mode
        return (
          <button
            key={m.mode}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(m.mode)}
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

  const name = projectName(files)
  const sheets = files.filter((f) => f.path.endsWith(".kicad_sch")).length
  const hints = symptomHints(symptoms)
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
        <h1 className="font-bold">
          {review ? "Review a schematic before you order" : "Diagnose a board problem"}
        </h1>
        <p className="mt-1 text-muted-foreground">
          {review
            ? "Drop your KiCad project folder. You get the design mistakes a reviewer would flag, most severe first, each with the evidence and the schematic fix."
            : "Drop your KiCad project folder and describe what you see on the bench. You get the likely causes in your schematic, ranked, and how to check each one with a multimeter."}
        </p>
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
          <h2 className="text-sm font-bold">Drop a KiCad project folder</h2>
          <span className="text-xs text-muted-foreground">or</span>
          <label
            className={cn(
              buttonVariants({ variant: "outline", size: "sm" }),
              "focus-within:outline-1 focus-within:outline-offset-2 focus-within:outline-foreground",
            )}
          >
            Choose folder
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
          Only <span className="text-foreground">.kicad_pro</span> and{" "}
          <span className="text-foreground">.kicad_sch</span> are sent; the PCB stays here.
        </p>

        {name && (
          <div className="mt-2.5 flex flex-wrap items-center gap-[2ch] text-xs">
            <span>
              <span className="text-ok">✓</span> <span className="font-bold text-info">{name}</span>{" "}
              <span className="text-muted-foreground">
                · {sheets} {sheets === 1 ? "sheet" : "sheets"}
              </span>
            </span>
            <button
              type="button"
              onClick={() => setShowFiles(!showFiles)}
              aria-expanded={showFiles}
              className="text-muted-foreground hover:text-foreground"
            >
              {showFiles ? "▾ hide files" : "▸ show files"}
            </button>
            <button
              type="button"
              onClick={() => setFiles([])}
              className="text-muted-foreground hover:text-bad"
            >
              remove
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
            <Title>
              {review ? "What the board is for (optional)" : "What you see on the bench"}
            </Title>
            {!review && (
              <p className="mt-1.5 text-xs text-muted-foreground">
                One observation per line: &ldquo;Fails on battery&rdquo;, not &ldquo;battery&rdquo;.
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => setSymptoms("")}
            className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
          >
            clear
          </button>
        </div>

        {!review && (
          <>
            <div className="flex flex-col gap-1 text-xs sm:flex-row sm:items-baseline sm:gap-[2ch]">
              <span className="shrink-0 font-bold tracking-wider text-muted-foreground uppercase">
                Covered {hints.filter((h) => h.done).length}/{hints.length}
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
                Start a line
              </span>
              {SYMPTOM_PROMPTS.map((p) => (
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
          placeholder={review ? REVIEW_PLACEHOLDER : SYMPTOM_PLACEHOLDER}
          className="leading-relaxed"
        />

        {!review && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>
              {lines.length} {lines.length === 1 ? "symptom" : "symptoms"}
            </span>
            {fragments.length > 0 && (
              <span className="text-warn">
                Short line{fragments.length > 1 ? "s" : ""} &ldquo;
                {fragments.slice(0, 2).join("”, “")}
                &rdquo; may not stand on {fragments.length > 1 ? "their" : "its"} own.
              </span>
            )}
          </div>
        )}

        {inputError && (
          <div className="text-sm" role="alert">
            <span className="bg-bad px-[1ch] font-bold text-on-state">ERROR</span> {inputError}
          </div>
        )}

        <div className="flex justify-end border-t pt-3">
          <div className="flex w-full gap-[1ch] sm:w-auto">
            {hasRuns && (
              <Button variant="outline" onClick={showSession}>
                Back to results
              </Button>
            )}
            <Button size="lg" className="w-full sm:w-auto" onClick={() => void actions.analyze()}>
              {review ? "Review schematic" : "Analyze schematic"} →
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}

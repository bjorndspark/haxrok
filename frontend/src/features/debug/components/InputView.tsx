import {
  ArrowRight,
  ClipboardCheck,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  FolderCheck,
  FolderOpen,
  PlusCircle,
  RotateCcw,
  Stethoscope,
  Upload,
} from "lucide-react"
import { useRef, useState } from "react"

import { cn } from "@/shared/lib/utils"
import { Badge } from "@/shared/ui/badge"
import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"
import { Textarea } from "@/shared/ui/textarea"

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

const MODES: { mode: Mode; label: string; detail: string; icon: typeof Stethoscope }[] = [
  {
    mode: "debug",
    label: "Debug a board on the bench",
    detail: "It's built and misbehaves. Find the cause.",
    icon: Stethoscope,
  },
  {
    mode: "review",
    label: "Review before you order",
    detail: "Catch schematic mistakes before you pay for boards.",
    icon: ClipboardCheck,
  },
]

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  return (
    <div role="radiogroup" aria-label="Mode" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {MODES.map((m) => {
        const Icon = m.icon
        const active = m.mode === mode
        return (
          <button
            key={m.mode}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(m.mode)}
            className={cn(
              "flex items-start gap-3 rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent",
              active &&
                "border-blue-500 bg-blue-50/60 ring-1 ring-blue-200 hover:bg-blue-50/80 dark:bg-blue-950/30 dark:ring-blue-900/60",
            )}
          >
            <Icon
              className={cn(
                "mt-0.5 h-5 w-5 shrink-0",
                active ? "text-blue-600 dark:text-sky-400" : "text-muted-foreground",
              )}
            />
            <span>
              <span className="block text-sm font-semibold">{m.label}</span>
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
        <div className="mb-1 flex items-center gap-2 font-mono text-xs font-semibold text-blue-600 dark:text-sky-400">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          {review ? "STEP 1: PROJECT AND NOTES" : "STEP 1: PROJECT AND BENCH NOTES"}
        </div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          {review ? "Review a schematic before you order" : "Diagnose a board problem"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground sm:text-base">
          {review
            ? "Drop your KiCad project folder. You get the design mistakes a reviewer would flag, most severe first, each with the evidence and the schematic fix."
            : "Drop your KiCad project folder and describe what you see on the bench. You get the likely causes in your schematic, ranked, and how to check each one with a multimeter."}
        </p>
      </div>

      <ModeSwitch mode={mode} onChange={setMode} />

      <Card
        className={cn(
          "p-5 transition-colors sm:p-6",
          dragging && "border-blue-500 bg-blue-50/60 dark:bg-blue-950/30",
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
        <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
          <div className="flex items-start gap-4">
            <div className="mt-0.5 flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-blue-200 bg-blue-50 text-blue-600 dark:border-blue-900 dark:bg-blue-950/50 dark:text-sky-400">
              <Upload className="h-6 w-6" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold sm:text-base">Drop a KiCad project folder</h2>
                <span className="text-xs text-muted-foreground">or</span>
                <label className="cursor-pointer rounded border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700 transition-colors hover:bg-blue-100 dark:border-blue-900 dark:bg-blue-950/50 dark:text-sky-300">
                  <FolderOpen className="mr-1 inline h-3.5 w-3.5" />
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
              <p className="mt-1 text-xs text-muted-foreground">
                Only the <code className="font-mono">.kicad_pro</code> and{" "}
                <code className="font-mono">.kicad_sch</code> files are sent. The PCB, backups and
                3D models stay on your machine. The server runs KiCad&apos;s netlist export and ERC.
                &ldquo;Choose folder&rdquo; makes your browser ask to upload every file in the
                folder; it counts them all, but only the schematics are sent. Dropping the folder
                skips that prompt.
              </p>

              {name && (
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <Badge tone="green" className="text-xs">
                    <FolderCheck />
                    {name}
                    <span className="opacity-75">
                      · {sheets} {sheets === 1 ? "sheet" : "sheets"}
                    </span>
                  </Badge>
                  <button
                    type="button"
                    onClick={() => setShowFiles(!showFiles)}
                    className="flex items-center gap-1 rounded border bg-secondary px-2 py-1 font-mono text-[11px] text-secondary-foreground hover:bg-accent"
                  >
                    {showFiles ? "Hide files" : "Show files"}
                    {showFiles ? (
                      <ChevronUp className="h-3 w-3" />
                    ) : (
                      <ChevronDown className="h-3 w-3" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => setFiles([])}
                    className="text-[11px] text-muted-foreground hover:text-foreground"
                  >
                    Remove
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {showFiles && files.length > 0 && (
          <ul className="animate-fade-in mt-4 grid grid-cols-1 gap-1.5 border-t pt-4 font-mono text-xs sm:grid-cols-2">
            {files.map((f) => (
              <li key={f.path} className="truncate rounded border bg-background px-2 py-1">
                {f.path}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="space-y-4 p-6 sm:p-7">
        <div className="flex items-start justify-between gap-3 border-b pb-3">
          <div>
            <h2 className="text-base font-bold">
              {review ? "What the board is for (optional)" : "What you see on the bench"}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {review ? (
                "Supplies, loads and conditions the board must handle. The review checks the design against them."
              ) : (
                <>
                  One observation per line. Each line becomes a numbered symptom, so make each one
                  stand on its own: &ldquo;Fails on battery&rdquo;, not &ldquo;battery&rdquo;.
                </>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setSymptoms("")}
            className="flex shrink-0 items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="h-3 w-3" />
            Clear
          </button>
        </div>

        {!review && (
          <>
            <div className="flex flex-col justify-between gap-2 rounded-lg border bg-background p-2.5 text-xs sm:flex-row sm:items-center">
              <span className="font-mono text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
                Covered ({hints.filter((h) => h.done).length}/{hints.length})
              </span>
              <div className="flex flex-wrap gap-1.5">
                {hints.map((h) => (
                  <Badge
                    key={h.key}
                    tone={h.done ? "green" : "neutral"}
                    className={cn(!h.done && "opacity-60")}
                  >
                    {h.done ? "✓" : "○"} {h.label}
                  </Badge>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs font-medium text-muted-foreground">Start a line:</span>
              {SYMPTOM_PROMPTS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => insertPrompt(p.prefix)}
                  className="flex items-center gap-1 rounded-md border bg-secondary px-2.5 py-1 text-xs text-secondary-foreground transition-colors hover:bg-blue-50 hover:text-blue-700 dark:hover:bg-blue-950/40 dark:hover:text-sky-300"
                >
                  <PlusCircle className="h-3 w-3 text-blue-500" />
                  {p.label}
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
          className="bg-background p-3.5 text-sm leading-relaxed sm:p-4"
        />

        {!review && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="font-mono">
              {lines.length} {lines.length === 1 ? "symptom" : "symptoms"}
            </span>
            {fragments.length > 0 && (
              <span className="text-amber-700 dark:text-amber-300">
                Short line{fragments.length > 1 ? "s" : ""} &ldquo;
                {fragments.slice(0, 2).join("”, “")}
                &rdquo; may not stand on {fragments.length > 1 ? "their" : "its"} own.
              </span>
            )}
          </div>
        )}

        {inputError && (
          <div className="flex items-center gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-200">
            <CircleAlert className="h-4 w-4 shrink-0" />
            {inputError}
          </div>
        )}

        <div className="flex flex-col items-center justify-between gap-4 border-t pt-3 sm:flex-row">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CircleAlert className="h-4 w-4 shrink-0 text-blue-500" />
            Uses the netlist, KiCad&apos;s ERC and datasheet pin tables. A run takes about two
            minutes.
          </p>
          <div className="flex w-full gap-2 sm:w-auto">
            {hasRuns && (
              <Button variant="outline" onClick={showSession}>
                Back to results
              </Button>
            )}
            <Button
              size="lg"
              className="w-full px-6 sm:w-auto"
              onClick={() => void actions.analyze()}
            >
              {review ? "Review schematic" : "Analyze schematic"}
              <ArrowRight />
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}

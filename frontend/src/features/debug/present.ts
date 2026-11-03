/** Presentation logic for results: evidence wording, glossary matching and
 *  the downloadable Markdown report. Pure functions, no React. */
import type {
  CheckedAnalysis,
  DesignSummary,
  Evidence,
  GlossaryEntry,
  Hypothesis,
  Run,
  WiringFinding,
} from "./types"

/** Labels say what a claim rests on, never that it was verified. */
export const EVIDENCE_LABEL: Record<Evidence, string> = {
  schematic: "Based on your schematic",
  erc: "Based on KiCad's checker",
  datasheet: "Based on the datasheet pin table",
  measurement: "Based on your measurement",
  none: "No direct evidence",
}

export type Confidence = "verified" | "fits" | "speculative"

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  verified: "Verified wiring fault",
  fits: "Fits the symptoms",
  speculative: "Speculative",
}

/** How much a hypothesis rests on, counted from the checked result. No
 *  model-made percentages (plan 001, "Trust"): every number is a count. */
export interface Assessment {
  confidence: Confidence
  /** Why the label was chosen, in one sentence. */
  reason: string
  explained: number
  symptoms: number
  backed: number
  claims: number
  /** Backend wiring findings this hypothesis cites with a backed claim. */
  findings: WiringFinding[]
}

/** The label rule:
 *  - verified: a claim that kept its evidence label cites a pin the
 *    backend's wiring check flagged. The fault is verified; whether it is
 *    the cause is what the confirm steps test.
 *  - fits: explains at least half the symptoms and at least one claim is
 *    backed by the input.
 *  - speculative: anything else. */
export function assess(h: Hypothesis, summary: DesignSummary | null): Assessment {
  const backedClaims = h.claims.filter((c) => c.evidence !== "none")
  const citedPins = new Set(backedClaims.flatMap((c) => c.cites.map((x) => x.pin ?? "")))
  const findings = (summary?.wiring ?? []).filter((f) => citedPins.has(f.pin))
  const symptoms = summary?.symptoms.length ?? h.explains.length + h.does_not_explain.length
  const base = {
    explained: h.explains.length,
    symptoms,
    backed: backedClaims.length,
    claims: h.claims.length,
    findings,
  }
  if (findings.length > 0) {
    const pins = findings.map((f) => `${f.pin} (${f.pin_name})`).join(", ")
    return {
      ...base,
      confidence: "verified",
      reason: `The backend's wiring check found ${pins} wired against the datasheet, and this hypothesis cites it.`,
    }
  }
  if (backedClaims.length > 0 && symptoms > 0 && h.explains.length * 2 >= symptoms) {
    return {
      ...base,
      confidence: "fits",
      reason: "Explains at least half the symptoms, with claims backed by your input.",
    }
  }
  return {
    ...base,
    confidence: "speculative",
    reason:
      backedClaims.length === 0
        ? "None of its claims is backed by your input."
        : "Explains fewer than half the symptoms.",
  }
}

export interface TextPart {
  text: string
  term: GlossaryEntry | null
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** Split `text` so each glossary term (first occurrence, whole word,
 *  case-insensitive) can carry a hover definition. */
export function splitGlossary(text: string, glossary: GlossaryEntry[]): TextPart[] {
  const terms = glossary.filter((g) => g.term.trim()).sort((a, b) => b.term.length - a.term.length)
  if (terms.length === 0) return [{ text, term: null }]
  const pattern = new RegExp(
    `(?<![\\w-])(${terms.map((t) => escapeRegExp(t.term)).join("|")})(?![\\w-])`,
    "gi",
  )
  const parts: TextPart[] = []
  const used = new Set<string>()
  let last = 0
  for (const m of text.matchAll(pattern)) {
    const entry = terms.find((t) => t.term.toLowerCase() === m[0].toLowerCase())
    if (!entry || used.has(entry.term)) continue
    used.add(entry.term)
    if (m.index > last) parts.push({ text: text.slice(last, m.index), term: null })
    parts.push({ text: m[0], term: entry })
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), term: null })
  return parts
}

export function symptomText(summary: DesignSummary | null, id: string): string {
  return summary?.symptoms.find((s) => s.id === id)?.text ?? id
}

/** The latest finished run as a Markdown bench report. */
export function toMarkdown(run: Run, summary: DesignSummary, result: CheckedAnalysis): string {
  const lines: string[] = [
    `# Häxrök report: ${summary.project}`,
    "",
    `Run #${String(run.number)} (${run.trigger}), ${new Date(run.startedAt).toLocaleString()}`,
    `${String(summary.parts)} parts, ${String(summary.nets)} nets, ${String(summary.erc.length)} ERC findings.`,
    "",
    "## Symptoms",
    ...summary.symptoms.map((s) => `- ${s.id}: ${s.text}`),
  ]
  if (summary.measurements.length > 0) {
    lines.push("", "## Measurements", ...summary.measurements.map((m) => `- ${m.id}: ${m.value}`))
  }
  for (const h of result.hypotheses) {
    const a = assess(h, summary)
    lines.push(
      "",
      `## #${String(h.rank)} ${h.title}`,
      "",
      `**${CONFIDENCE_LABEL[a.confidence]}** · explains ${String(a.explained)} of ${String(a.symptoms)} symptoms · ${String(a.backed)} of ${String(a.claims)} claims backed`,
      "",
      h.summary,
      "",
    )
    if (h.explains.length > 0)
      lines.push(`Explains: ${h.explains.map((id) => symptomText(summary, id)).join("; ")}`)
    if (h.does_not_explain.length > 0)
      lines.push(
        `Doesn't explain: ${h.does_not_explain.map((id) => symptomText(summary, id)).join("; ")}`,
      )
    lines.push("", "### Evidence")
    for (const c of h.claims) {
      const note = c.downgraded && c.reason ? ` (label removed: ${c.reason})` : ""
      lines.push(`- ${c.text} — *${EVIDENCE_LABEL[c.evidence]}*${note}`)
    }
    lines.push("", "### Confirm")
    for (const s of h.confirm) {
      lines.push(`- **${s.id}** (${s.tool}, power ${s.power}): ${s.instruction}`)
      for (const r of s.readings) lines.push(`  - ${r.reading} → ${r.establishes}`)
    }
    lines.push("", "### Fix")
    for (const r of h.fix.rework) lines.push(`- Rework: ${r.text}`)
    for (const s of h.fix.schematic) lines.push(`- Next revision: ${s}`)
  }
  if (result.open_questions.length > 0) {
    lines.push("", "## Open questions")
    for (const q of result.open_questions)
      lines.push(`- ${q.text}${q.suggested_check ? ` (check: ${q.suggested_check})` : ""}`)
  }
  if (result.glossary.length > 0) {
    lines.push("", "## Glossary", ...result.glossary.map((g) => `- **${g.term}**: ${g.definition}`))
  }
  return lines.join("\n") + "\n"
}

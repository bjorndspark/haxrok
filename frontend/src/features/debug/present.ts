/** Presentation logic for results: evidence wording, glossary matching and
 *  the downloadable Markdown report. Pure functions, no React. */
import type { CheckedAnalysis, DesignSummary, Evidence, GlossaryEntry, Run } from "./types"

/** Labels say what a claim rests on, never that it was verified. */
export const EVIDENCE_LABEL: Record<Evidence, string> = {
  schematic: "Based on your schematic",
  erc: "Based on KiCad's checker",
  datasheet: "Based on the datasheet pin table",
  measurement: "Based on your measurement",
  none: "No direct evidence",
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
    lines.push("", `## #${String(h.rank)} ${h.title}`, "", h.summary, "")
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

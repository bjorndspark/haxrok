/** Presentation logic for results: evidence wording, glossary matching and
 *  the downloadable Markdown report. Pure functions, no React. */
import type {
  Calculation,
  Simulation,
  CheckedAnalysis,
  DesignSummary,
  Diagram,
  Evidence,
  GlossaryEntry,
  Hypothesis,
  Run,
  Severity,
  WiringFinding,
} from "./types"

/** Labels say what a claim rests on, never that it was verified. */
export const EVIDENCE_LABEL: Record<Evidence, string> = {
  schematic: "Based on your schematic",
  erc: "Based on KiCad's checker",
  datasheet: "Based on the datasheet pin table",
  reference: "Based on the manufacturer's design",
  measurement: "Based on your measurement",
  none: "No direct evidence",
}

export type Confidence = "verified" | "fits" | "speculative"

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  verified: "Verified design fault",
  fits: "Fits the symptoms",
  speculative: "Speculative",
}

/** In review mode there are no symptoms to fit, and the cited pins only
 *  show the topology, not that it is wrong: in the review evals a real fault
 *  and a false alarm cited the same mix of facts. So only the backend's
 *  wiring check verifies an issue; the rest is the model's reasoning. */
export function confidenceLabel(c: Confidence, review: boolean): string {
  return review && c === "speculative" ? "Reasoned: check it" : CONFIDENCE_LABEL[c]
}

/** The badge on a calculation: its value and unit, or what's wrong when
 *  there is no value (a UART check has nothing to measure; a missing
 *  pull-up has nothing to compute), plus the expected value when given. */
export function calculationBadge(c: Calculation): string {
  const value =
    c.value !== null
      ? `${String(c.value)} ${c.unit}`
      : c.kind === "uart" || c.kind === "enable_pin"
        ? "miswired"
        : "missing"
  if (c.expected === null) return value
  return value + (c.flagged ? ` ≠ ${String(c.expected)} ${c.unit}` : " ✓")
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  will_fail: "Won't work as drawn",
  may_fail: "May fail",
  improve: "Improvement",
}

export function isReview(summary: DesignSummary | null): boolean {
  return summary?.mode === "review"
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
  /** Flagged backend calculations it cites, by id or by one of their pins. */
  calculations: Calculation[]
}

/** The label rule:
 *  - verified: a claim that kept its evidence label cites a pin the
 *    backend's wiring check flagged. The fault is verified; whether it is
 *    the cause is what the confirm steps test.
 *  - fits: explains at least half the symptoms and at least one claim is
 *    backed by the input. Never in review mode (see confidenceLabel).
 *  - speculative: anything else. */
export function assess(h: Hypothesis, summary: DesignSummary | null): Assessment {
  const backedClaims = h.claims.filter((c) => c.evidence !== "none")
  const citedPins = new Set(backedClaims.flatMap((c) => c.cites.map((x) => x.pin ?? "")))
  const findings = (summary?.wiring ?? []).filter((f) => citedPins.has(f.pin))
  const citedIds = new Set(backedClaims.flatMap((c) => c.cites.map((x) => x.id ?? "")))
  const calculations = (summary?.calculations ?? []).filter(
    (c) => c.flagged && (citedIds.has(c.id) || c.pins.some((p) => citedPins.has(p))),
  )
  const symptoms = summary?.symptoms.length ?? h.explains.length + h.does_not_explain.length
  const base = {
    explained: h.explains.length,
    symptoms,
    backed: backedClaims.length,
    claims: h.claims.length,
    findings,
    calculations,
  }
  if (findings.length > 0) {
    const pins = findings.map((f) => `${f.pin} (${f.pin_name})`).join(", ")
    return {
      ...base,
      confidence: "verified",
      reason: `The backend's wiring check found ${pins} wired against the datasheet, and this hypothesis cites it.`,
    }
  }
  if (calculations.length > 0) {
    return {
      ...base,
      confidence: "verified",
      reason: `The backend's calculation from your netlist flagged ${calculations.map((c) => c.ic).join(", ")}, and this hypothesis cites it.`,
    }
  }
  if (isReview(summary)) {
    return {
      ...base,
      confidence: "speculative",
      reason:
        "The pins it cites are in your schematic, but that they are wrong is the model's reasoning. Settle it with the checks below.",
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
  const review = isReview(summary)
  const lines: string[] = [
    `# Häxrök ${review ? "design review" : "report"}: ${summary.project}`,
    "",
    `Run #${String(run.number)} (${run.trigger}), ${new Date(run.startedAt).toLocaleString()}`,
    `${String(summary.parts)} parts, ${String(summary.nets)} nets, ${String(summary.erc.length)} ERC findings.`,
  ]
  if (summary.symptoms.length > 0) {
    lines.push(
      "",
      review ? "## Notes on the board" : "## Symptoms",
      ...summary.symptoms.map((s) => `- ${s.id}: ${s.text}`),
    )
  }
  if (review && result.hypotheses.length === 0) {
    lines.push("", "No schematic issues found.")
  }
  if (summary.measurements.length > 0) {
    lines.push("", "## Measurements", ...summary.measurements.map((m) => `- ${m.id}: ${m.value}`))
  }
  for (const h of result.hypotheses) {
    const a = assess(h, summary)
    lines.push(
      "",
      `## #${String(h.rank)} ${h.title}`,
      "",
      review
        ? `**${h.severity ? SEVERITY_LABEL[h.severity] : "Issue"}** · ${confidenceLabel(a.confidence, true)} · ${String(a.backed)} of ${String(a.claims)} claims backed`
        : `**${CONFIDENCE_LABEL[a.confidence]}** · explains ${String(a.explained)} of ${String(a.symptoms)} symptoms · ${String(a.backed)} of ${String(a.claims)} claims backed`,
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
    lines.push("", review ? "### Check before ordering" : "### Confirm")
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

export interface DiagramLayout {
  width: number
  height: number
  box: { x: number; y: number; width: number; height: number }
  busX: number
  pins: { pin: string; label: string; y: number; flagged: boolean }[]
  nets: {
    name: string
    label: string
    members: string
    top: number
    bottom: number
    flagged: boolean
  }[]
}

const ROW = 26
const GROUP_GAP = 14
const HEADER = 50
// Characters of member text that fit right of the bus at 11px mono.
const MEMBER_CHARS = 44
// Width of one 11px mono character, and the narrowest the IC box gets.
const CHAR_W = 6.7
const MIN_BOX = 150

/** Where everything in a wiring diagram goes: the IC's pins down the right
 *  edge of its box, grouped by net, each group joined by a bus with the
 *  net's name and its other notable parts beside it. */
export function diagramLayout(d: Diagram): DiagramLayout {
  const pins: DiagramLayout["pins"] = []
  const nets: DiagramLayout["nets"] = []
  let y = HEADER
  for (const net of d.nets) {
    const own = d.pins.filter((p) => p.net === net.name)
    if (own.length === 0) continue
    const top = y
    for (const p of own) {
      const number = p.pin.split(".")[1] ?? p.pin
      pins.push({ pin: p.pin, label: `${number} ${p.name}`.trim(), y, flagged: p.flagged })
      y += ROW
    }
    const labels = net.members.map((m) =>
      m.inductor ? `${m.pin} (inductor)` : m.name ? `${m.pin}(${m.name})` : m.pin,
    )
    // Fit the line beside the bus; whatever doesn't fit joins "+N more".
    const members: string[] = []
    for (const label of labels) {
      if ([...members, label].join(" · ").length > MEMBER_CHARS) break
      members.push(label)
    }
    const more = net.more + labels.length - members.length
    if (more > 0) members.push(`+${String(more)} more`)
    nets.push({
      name: net.name,
      label: net.name.replace(/\/+$/, "").split("/").pop() ?? net.name,
      members: members.join(" · "),
      top,
      bottom: y - ROW,
      flagged: own.some((p) => p.flagged),
    })
    y = Math.max(y, top + 2 * ROW) + GROUP_GAP
  }
  const boxHeight = y - GROUP_GAP - ROW / 2
  // Long pin names (GPIO0/ADC1_CH0/XTAL_32K_P) widen the box so they aren't clipped.
  const longest = Math.max(d.ic.length, d.value.length, ...pins.map((p) => p.label.length))
  const boxWidth = Math.max(MIN_BOX, Math.ceil(longest * CHAR_W) + 20)
  const busX = boxWidth + 70
  return {
    width: busX + 340,
    height: boxHeight + 8,
    box: { x: 4, y: 4, width: boxWidth, height: boxHeight - 4 },
    busX,
    pins,
    nets,
  }
}

const PLOT = { width: 440, height: 180, left: 40, right: 96, top: 22, bottom: 28 }

export interface SimulationLayout {
  width: number
  height: number
  /** The plot area: the waveform's box inside the axes. */
  plot: { x0: number; x1: number; y0: number; y1: number }
  path: string
  points: { x: number; y: number; t: number; v: number }[]
  /** Horizontal reference lines, labelled at the right edge. */
  levels: { y: number; label: string; danger: boolean }[]
  xTicks: { x: number; label: string }[]
  yTicks: { y: number; label: string }[]
}

/** Where the waveform of a simulated I²C line goes: volts up, nanoseconds
 *  across, with the pull-up rail and the IC's supply + 0.3 V (its usual
 *  absolute maximum) as reference lines. */
export function simulationLayout(sim: Simulation): SimulationLayout {
  const { width, height, left, right, top, bottom } = PLOT
  const plot = { x0: left, x1: width - right, y0: height - bottom, y1: top }
  const tMax = Math.max(...sim.t_ns, 1)
  const vMax = Math.ceil(Math.max(sim.v_pull, sim.v_supply + 0.3, ...sim.v))
  const x = (t: number) => plot.x0 + (t / tMax) * (plot.x1 - plot.x0)
  const y = (v: number) => plot.y0 - (v / vMax) * (plot.y0 - plot.y1)
  const points = sim.t_ns.map((t, i) => {
    const v = sim.v[i] ?? 0
    return { x: x(t), y: y(v), t, v }
  })
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ")
  const abs = Math.round((sim.v_supply + 0.3) * 10) / 10
  const levels = [
    { y: y(sim.v_pull), label: `rail ${String(sim.v_pull)} V`, danger: false },
    { y: y(abs), label: `VDD+0.3 ${String(abs)} V`, danger: true },
  ]
  const step = niceStep(tMax / 4)
  const xTicks = []
  for (let t = 0; t <= tMax + 1e-9; t += step) {
    xTicks.push({ x: x(t), label: String(Math.round(t)) })
  }
  const yTicks = []
  for (let v = 0; v <= vMax; v += 1) yTicks.push({ y: y(v), label: String(v) })
  return { width, height, plot, path, points, levels, xTicks, yTicks }
}

/** The sample nearest to ``x`` on the plot, for the hover readout. */
export function nearestPoint(
  layout: SimulationLayout,
  x: number,
): SimulationLayout["points"][0] | null {
  let best: SimulationLayout["points"][0] | null = null
  for (const p of layout.points) {
    if (best === null || Math.abs(p.x - x) < Math.abs(best.x - x)) best = p
  }
  return best
}

function niceStep(raw: number): number {
  const mag = 10 ** Math.floor(Math.log10(raw))
  const n = raw / mag
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag
}

/** Presentation logic for results: evidence wording, glossary matching and
 *  the downloadable Markdown report. Pure functions, no React; wording comes
 *  from the `Messages` table passed in. */
import type { Messages } from "./messages"
import type {
  Calculation,
  Simulation,
  CheckedAnalysis,
  DesignSummary,
  Diagram,
  GlossaryEntry,
  Hypothesis,
  Run,
  PinWarning,
  WiringFinding,
} from "./types"

export type Confidence = "verified" | "fits" | "speculative"

/** In review mode there are no symptoms to fit, and the cited pins only
 *  show the topology, not that it is wrong: in the review evals a real fault
 *  and a false alarm cited the same mix of facts. So only the backend's
 *  wiring check verifies an issue; the rest is the model's reasoning. */
export function confidenceLabel(c: Confidence, review: boolean, m: Messages): string {
  return review && c === "speculative" ? m.present.reasoned : m.present.confidence[c]
}

/** The badge on a calculation: its value and unit, or what's wrong when
 *  there is no value (a UART check has nothing to measure; a missing
 *  pull-up has nothing to compute), plus the expected value when given. */
export function calculationBadge(c: Calculation, m: Messages): string {
  const value =
    c.value !== null
      ? `${String(c.value)} ${c.unit}`
      : c.kind === "uart" || c.kind === "enable_pin"
        ? m.present.miswired
        : m.present.missing
  if (c.expected === null) return value
  return value + (c.flagged ? ` ≠ ${String(c.expected)} ${c.unit}` : " ✓")
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
export function assess(h: Hypothesis, summary: DesignSummary | null, m: Messages): Assessment {
  const reason = m.present.reason
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
      reason: reason.wiring(pins),
    }
  }
  if (calculations.length > 0) {
    return {
      ...base,
      confidence: "verified",
      reason: reason.calculation(calculations.map((c) => c.ic).join(", ")),
    }
  }
  if (isReview(summary)) {
    return {
      ...base,
      confidence: "speculative",
      reason: reason.review,
    }
  }
  if (backedClaims.length > 0 && symptoms > 0 && h.explains.length * 2 >= symptoms) {
    return {
      ...base,
      confidence: "fits",
      reason: reason.fits,
    }
  }
  return {
    ...base,
    confidence: "speculative",
    reason: backedClaims.length === 0 ? reason.unbacked : reason.fewer,
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

/** The latest finished run as a Markdown bench report, headed with `app`
 *  (the project name, which is config). */
export function toMarkdown(
  run: Run,
  summary: DesignSummary,
  result: CheckedAnalysis,
  m: Messages,
  app: string,
): string {
  const t = m.report
  const review = isReview(summary)
  const lines: string[] = [
    t.title(app, review, summary.project),
    "",
    t.run(run.number, run.trigger, new Date(run.startedAt).toLocaleString()),
    t.counts(summary.parts, summary.nets, summary.erc.length),
  ]
  if (summary.symptoms.length > 0) {
    lines.push(
      "",
      review ? t.notes : t.symptoms,
      ...summary.symptoms.map((s) => `- ${s.id}: ${s.text}`),
    )
  }
  if (review && result.hypotheses.length === 0) {
    lines.push("", t.noIssues)
  }
  if (summary.measurements.length > 0) {
    lines.push("", t.measurements, ...summary.measurements.map((x) => `- ${x.id}: ${x.value}`))
  }
  for (const h of result.hypotheses) {
    const a = assess(h, summary, m)
    lines.push(
      "",
      `## #${String(h.rank)} ${h.title}`,
      "",
      review
        ? t.reviewLine(
            h.severity ? m.present.severity[h.severity] : t.issue,
            confidenceLabel(a.confidence, true, m),
            a.backed,
            a.claims,
          )
        : t.debugLine(
            m.present.confidence[a.confidence],
            a.explained,
            a.symptoms,
            a.backed,
            a.claims,
          ),
      "",
      h.summary,
      "",
    )
    if (h.explains.length > 0)
      lines.push(`${t.explains} ${h.explains.map((id) => symptomText(summary, id)).join("; ")}`)
    if (h.does_not_explain.length > 0)
      lines.push(
        `${t.doesntExplain} ${h.does_not_explain.map((id) => symptomText(summary, id)).join("; ")}`,
      )
    lines.push("", t.evidence)
    for (const c of h.claims) {
      const note = c.downgraded && c.reason ? t.labelRemoved(c.reason) : ""
      lines.push(`- ${c.text} — *${m.present.evidence[c.evidence]}*${note}`)
    }
    lines.push("", review ? t.check : t.confirm)
    for (const s of h.confirm) {
      lines.push(
        t.step(s.id, m.hypothesis.tool[s.tool], m.hypothesis.power(s.power), s.instruction),
      )
      for (const r of s.readings) lines.push(`  - ${r.reading} → ${r.establishes}`)
    }
    lines.push("", t.fix)
    for (const r of h.fix.rework) lines.push(`- ${t.rework} ${r.text}`)
    for (const s of h.fix.schematic) lines.push(`- ${t.nextRevision} ${s}`)
  }
  if (run.simulations.length > 0) {
    lines.push("", t.simulations)
    for (const sim of run.simulations) {
      lines.push("", `### ${sim.title}`, "", sim.text, "", "```spice", sim.deck.trim(), "```")
      for (const q of run.chats[sim.id] ?? []) lines.push("", `**Q:** ${q.question}`, "", q.answer)
    }
  }
  if (result.open_questions.length > 0) {
    lines.push("", t.openQuestions)
    for (const q of result.open_questions)
      lines.push(`- ${q.text}${q.suggested_check ? t.suggestedCheck(q.suggested_check) : ""}`)
  }
  if (result.glossary.length > 0) {
    lines.push("", t.glossary, ...result.glossary.map((g) => `- **${g.term}**: ${g.definition}`))
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

const ROW = 28
const GROUP_GAP = 14
const HEADER = 54
// Characters of member text that fit right of the bus at 13px mono, leaving
// room for the " · +NN more" that may follow them.
const MEMBER_CHARS = 34
// Width of one 13px mono character, and the narrowest the IC box gets.
const CHAR_W = 7.9
const MIN_BOX = 170

/** Where everything in a wiring diagram goes: the IC's pins down the right
 *  edge of its box, grouped by net, each group joined by a bus with the
 *  net's name and its other notable parts beside it. */
export function diagramLayout(d: Diagram, m: Messages): DiagramLayout {
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
    const labels = net.members.map((x) =>
      x.inductor ? `${x.pin} ${m.diagram.inductor}` : x.name ? `${x.pin}(${x.name})` : x.pin,
    )
    // Fit the line beside the bus; whatever doesn't fit joins "+N more".
    const members: string[] = []
    for (const label of labels) {
      if ([...members, label].join(" · ").length > MEMBER_CHARS) break
      members.push(label)
    }
    const more = net.more + labels.length - members.length
    if (more > 0) members.push(m.diagram.more(more))
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
    width: busX + 380,
    height: boxHeight + 8,
    box: { x: 4, y: 4, width: boxWidth, height: boxHeight - 4 },
    busX,
    pins,
    nets,
  }
}

const PLOT = { width: 520, height: 200, left: 44, right: 128, top: 24, bottom: 36 }

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

/** Where a simulated waveform goes: volts up, time across in the
 *  simulation's own unit, with its reference lines (a flagged I²C line's
 *  rail and the IC's supply + 0.3 V; an RC delay's rail and 63% point). */
export function simulationLayout(sim: Simulation): SimulationLayout {
  const { width, height, left, right, top, bottom } = PLOT
  const plot = { x0: left, x1: width - right, y0: height - bottom, y1: top }
  const tMax = Math.max(...sim.t, 1e-9)
  const vMax = Math.ceil(Math.max(...sim.levels.map((l) => l.v), ...sim.v))
  const x = (t: number) => plot.x0 + (t / tMax) * (plot.x1 - plot.x0)
  const y = (v: number) => plot.y0 - (v / vMax) * (plot.y0 - plot.y1)
  const points = sim.t.map((t, i) => {
    const v = sim.v[i] ?? 0
    return { x: x(t), y: y(v), t, v }
  })
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ")
  const levels = sim.levels.map((l) => ({ y: y(l.v), label: l.label, danger: l.danger }))
  const step = niceStep(tMax / 4)
  const xTicks = []
  for (let t = 0; t <= tMax + 1e-9; t += step) {
    xTicks.push({ x: x(t), label: String(Number(t.toPrecision(3))) })
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

export type MarkKind = "ref" | "net"

/** ``text`` split so each listed ref (U5, U5.9) and net can be coloured. A
 *  token counts only as a whole word: U1.2 doesn't mark inside U1.23 or
 *  /A/U1.2X. Longer tokens win where two start at the same place. */
export function markTokens(
  text: string,
  tokens: { ref: string[]; net: string[] },
): { text: string; kind?: MarkKind }[] {
  const all = [
    ...tokens.ref.map((t) => ({ t, kind: "ref" as const })),
    ...tokens.net.map((t) => ({ t, kind: "net" as const })),
  ]
    .filter(({ t }) => t !== "")
    .sort((a, b) => b.t.length - a.t.length)
  const word = /[A-Za-z0-9_]/
  const parts: { text: string; kind?: MarkKind }[] = []
  let plain = ""
  let i = 0
  while (i < text.length) {
    const before = text[i - 1] ?? ""
    const hit = all.find(({ t }) => {
      if (!text.startsWith(t, i)) return false
      const after = text[i + t.length] ?? ""
      const next = text[i + t.length + 1] ?? ""
      // Not glued to a word on either side, and not REF in front of .PIN.
      return (
        !word.test(before) &&
        before !== "/" &&
        !word.test(after) &&
        !(after === "." && word.test(next))
      )
    })
    if (hit) {
      if (plain) parts.push({ text: plain })
      parts.push({ text: hit.t, kind: hit.kind })
      plain = ""
      i += hit.t.length
    } else {
      plain += text[i]
      i += 1
    }
  }
  if (plain) parts.push({ text: plain })
  return parts
}

/** The refs and nets a wiring finding or pin warning names, to mark. */
export function findingTokens(f: WiringFinding | PinWarning): { ref: string[]; net: string[] } {
  const pins = "ref" in f ? f.pins : [f.pin, ...f.conflicts, ...f.inductors]
  const parts = pins.map((p) => p.split(".")[0] ?? p)
  return { ref: [...new Set([...pins, ...parts])], net: f.nets ?? [] }
}

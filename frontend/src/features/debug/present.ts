/** Presentation logic for results: evidence wording, glossary matching and
 *  the downloadable Markdown report. Pure functions, no React; wording comes
 *  from the `Messages` table passed in. */
import type { Messages } from "./messages"
import type {
  Calculation,
  Simulation,
  CheckedAnalysis,
  CheckedClaim,
  DatasheetLine,
  DesignSummary,
  Diagram,
  GlossaryEntry,
  Hypothesis,
  Run,
  PinWarning,
  PowerTree,
  RailBudget,
  SimCircuit,
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

/** A datasheet PDF opened at `page`, or null when there is no URL. */
export function pageUrl(url: string | null | undefined, page: number): string | null {
  return url ? `${url}#page=${String(page)}` : null
}

/** A regulator run with its vendor model (plan 004), as opposed to an RC
 *  node's transient (plan 003). Keyed on the modelled IC: a skipped
 *  regulator has no analysis. */
export function isVendorModel(sim: Simulation): boolean {
  return sim.ref != null
}

/** A vendor-model run and, when a wiring finding's fix touched its chain,
 *  the same run with the fix (plan 006). */
export interface SimPair {
  sim: Simulation
  fixed: Simulation | null
}

/** Pair each with-fix run with the as-built run it names in `fix_of`; a
 *  with-fix run whose as-built run isn't there stays on its own. */
export function pairVendorSims(sims: Simulation[]): SimPair[] {
  const ids = new Set(sims.map((s) => s.id))
  const fixed = new Map<string, Simulation>()
  for (const s of sims) if (s.fix_of && ids.has(s.fix_of)) fixed.set(s.fix_of, s)
  return sims
    .filter((s) => !(s.fix_of && ids.has(s.fix_of)))
    .map((sim) => ({ sim, fixed: fixed.get(sim.id) ?? null }))
}

export interface PeakRow {
  name: string
  label: string
  /** "12.5 A", or null when that run has no such ammeter. */
  built: string | null
  fixed: string | null
}

/** Amps with an SI prefix: 12.5 → `12.5 A`, 0.0021 → `2.1 mA`. */
function amps(a: number): string {
  if (a === 0) return "0 A"
  const si = formatSi(Math.abs(a))
  const prefix = /\D$/.test(si) ? si.slice(-1) : ""
  return `${si.slice(0, si.length - prefix.length)} ${prefix}A`
}

/** Each ammeter's peak as built beside the same ammeter with the fix,
 *  keyed by name; the as-built label wins, since the fix renames the net. */
export function peakRows(built: Simulation, fixed: Simulation | null): PeakRow[] {
  const rows = new Map<string, PeakRow>()
  for (const p of built.peaks ?? [])
    rows.set(p.name, { name: p.name, label: p.label, built: amps(p.a), fixed: null })
  for (const p of fixed?.peaks ?? []) {
    const row = rows.get(p.name)
    if (row) row.fixed = amps(p.a)
    else rows.set(p.name, { name: p.name, label: p.label, built: null, fixed: amps(p.a) })
  }
  return [...rows.values()]
}

export type StatusTone = "ok" | "warn" | "neutral"

/** An IC's datasheet or model status in the summary card: a label, the PDF
 *  to link (datasheet only), the pages its facts came from, and why there
 *  is none or it failed. */
export interface StatusBadge {
  label: string
  tone: StatusTone
  url: string | null
  pages: string | null
  reason: string | null
}

/** Where `ref`'s datasheet stands. Null in runs recorded before datasheets
 *  were looked up. A found datasheet whose read failed is shown in warn
 *  with the reason; one that was read lists the pages its facts cite. */
export function datasheetBadge(
  ref: string,
  summary: DesignSummary,
  m: Messages,
): StatusBadge | null {
  const status = summary.datasheets?.find((d) => d.ref === ref)
  if (!status) return null
  const t = m.summary
  if (status.status !== "found")
    return { label: t.noDatasheet, tone: "neutral", url: null, pages: null, reason: status.reason }
  const failed = summary.datasheet_failures?.find((f) => f.ref === ref)
  const pages = [
    ...new Set((summary.datasheet_facts ?? []).filter((f) => f.ref === ref).map((f) => f.page)),
  ].sort((a, b) => a - b)
  return {
    label: t.datasheet,
    tone: failed ? "warn" : "ok",
    url: status.url,
    pages: pages.length > 0 ? t.pages(pages) : null,
    reason: failed?.reason ?? null,
  }
}

/** Where `ref`'s SPICE model stands: its format and whether ngspice loads
 *  it, encrypted, or none (with the reason). Null in older runs. */
export function modelBadge(ref: string, summary: DesignSummary, m: Messages): StatusBadge | null {
  const model = summary.spice_models?.find((s) => s.ref === ref)
  if (!model) return null
  const t = m.summary
  const loadable =
    model.status === "ngspice" || model.status === "pspice" || model.status === "ltspice"
  const loads = loadable ? model.loads : null
  return {
    label:
      loads === null
        ? t.model[model.status]
        : `${t.model[model.status]} · ${loads ? t.loads : t.doesntLoad}`,
    tone:
      loads === true ? "ok" : loads === false || model.status === "encrypted" ? "warn" : "neutral",
    url: null,
    pages: null,
    reason: loads === true ? null : model.reason,
  }
}

/** A datasheet fact or deviation a claim cites, with its page's link. */
export interface CitedLine extends DatasheetLine {
  url: string | null
}

function linkPage(line: DatasheetLine, summary: DesignSummary): CitedLine {
  const sheet = summary.datasheets?.find((d) => d.ref === line.ref)
  return { ...line, url: pageUrl(sheet?.url, line.page) }
}

/** The datasheet facts (ds…) and deviations (dv…) `claim` cites, in the
 *  order cited, each linking to its datasheet at its page. */
export function citedLines(claim: CheckedClaim, summary: DesignSummary | null): CitedLine[] {
  if (!summary) return []
  const lines = [...(summary.datasheet_facts ?? []), ...(summary.deviations ?? [])]
  const ids = [...new Set(claim.cites.flatMap((c) => (c.id ? [c.id] : [])))]
  return ids.flatMap((id) => {
    const line = lines.find((l) => l.id === id)
    return line ? [linkPage(line, summary)] : []
  })
}

/** A rail of the power tree, worded for the summary card. */
export interface PowerRow {
  net: string
  /** "USB", "battery", "input"; null when not a root. */
  source: string | null
  /** "3.3 V", "3–4.2 V", or "voltage unknown". */
  voltage: string
  inferred: boolean
  /** Where the voltage comes from: "calculated, U5 feedback (cf1)". */
  basis: string
  /** One per part that makes it: "U3 TPS2121 (mux/switch) ← VBUS, VBAT". */
  from: string[]
  loads: string[]
  /** "load at least 550 mA (1 unknown: U4) against 2000 mA rated (…)";
   *  null before the datasheets are read. */
  budget: string | null
  over: boolean
}

/** Volts with at most three significant digits, so 3.2999 reads 3.3. */
function volt(v: number): string {
  return String(Number(v.toPrecision(3)))
}

/** Milliamps with at most three significant digits. */
function milliamps(v: number): string {
  return String(Number(v.toPrecision(3)))
}

/** A rail's budget in one line, or null when it has none. */
function budgetLine(b: RailBudget | null | undefined, m: Messages): string | null {
  if (!b) return null
  const t = m.summary
  const load =
    b.unknown.length > 0
      ? t.loadAtLeast(milliamps(b.load_ma), b.unknown)
      : t.load(milliamps(b.load_ma))
  return b.rated_ma === null ? load : `${load} ${t.rated(milliamps(b.rated_ma), b.rated_note)}`
}

/** The power tree as rows, roots first (the backend's order). */
export function powerRows(tree: PowerTree, m: Messages): PowerRow[] {
  const t = m.summary
  const parts = new Map(tree.parts.map((p) => [p.ref, p]))
  return tree.rails.map((r) => {
    const voltage =
      r.min === null || r.max === null
        ? t.voltageUnknown
        : r.min === r.max
          ? `${volt(r.min)} V`
          : `${volt(r.min)}–${volt(r.max)} V`
    const from = r.fed_by.map((ref) => {
      const p = parts.get(ref)
      return p ? `${ref} ${p.value} (${t.partKind[p.kind]}) ← ${p.inputs.join(", ")}` : ref
    })
    return {
      net: r.net,
      source: r.source && t.source[r.source],
      voltage,
      inferred: r.inferred,
      basis: r.note ? `${t.basis[r.basis]}, ${r.note}` : t.basis[r.basis],
      from,
      loads: r.loads,
      budget: budgetLine(r.budget, m),
      over: r.budget?.over ?? false,
    }
  })
}

/** An operating state, worded for the summary card. */
export interface StateRow {
  /** "VBUS only", "VBUS + VBAT". */
  name: string
  up: string[]
  down: string[]
  /** Parts held off by their enable, in the backend's words. */
  off: string[]
  /** "VBUS may be back-powered through R17, U1.9". */
  backPowered: string[]
}

/** A net's name without its sheet path: "/power/VSYS" reads "VSYS". */
function shortNet(net: string): string {
  return net.slice(net.lastIndexOf("/") + 1) || net
}

/** The power tree's operating states as rows, in the backend's order. */
export function stateRows(tree: PowerTree, m: Messages): StateRow[] {
  const t = m.summary
  return (tree.states ?? []).map((s) => {
    const names = s.sources.map(shortNet)
    return {
      name: names.length === 1 ? t.stateOnly(names[0] ?? "") : names.join(" + "),
      up: s.up.map(shortNet),
      down: s.down.map(shortNet),
      off: s.off,
      backPowered: s.back_powered.map((b) => t.backPower(shortNet(b.rail), b.via)),
    }
  })
}

/** Everything read from the datasheets, for the summary card: the facts
 *  grouped by IC (in reading order), then the deviations. */
export function datasheetLines(summary: DesignSummary): {
  facts: { ref: string; lines: CitedLine[] }[]
  deviations: CitedLine[]
} {
  const facts: { ref: string; lines: CitedLine[] }[] = []
  for (const fact of summary.datasheet_facts ?? []) {
    const group = facts.find((g) => g.ref === fact.ref)
    if (group) group.lines.push(linkPage(fact, summary))
    else facts.push({ ref: fact.ref, lines: [linkPage(fact, summary)] })
  }
  return { facts, deviations: (summary.deviations ?? []).map((d) => linkPage(d, summary)) }
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
      for (const line of citedLines(c, summary)) {
        const page = line.url ? `[p.${String(line.page)}](${line.url})` : `p.${String(line.page)}`
        lines.push(`  - ${line.id} · ${page} ${line.text}`)
      }
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
  const rc = run.simulations.filter((sim) => !isVendorModel(sim))
  const vendor = run.simulations.filter(isVendorModel)
  for (const [heading, sims] of [
    [t.simulations, rc],
    [t.vendorSimulations, vendor],
  ] as const) {
    if (sims.length === 0) continue
    lines.push("", heading)
    for (const sim of sims) {
      lines.push("", `### ${sim.title}`, "")
      if (sim.verdict) lines.push(t.verdict(m.simulations.verdict[sim.verdict]), "")
      lines.push(sim.text)
      if (sim.deck.trim()) lines.push("", "```spice", sim.deck.trim(), "```")
      for (const q of run.chats[sim.id] ?? []) lines.push("", `**Q:** ${q.question}`, "", q.answer)
    }
  }
  if (run.resultChat.length > 0) {
    lines.push("", t.followUps)
    for (const q of run.resultChat) lines.push("", `**Q:** ${q.question}`, "", q.answer)
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

interface Point {
  x: number
  y: number
  t: number
  v: number
}

export interface SimulationLayout {
  width: number
  height: number
  /** The plot area: the waveform's box inside the axes. */
  plot: { x0: number; x1: number; y0: number; y1: number }
  /** The time axis' unit; the original's own unless a what-if is drawn. */
  unit: Simulation["t_unit"]
  path: string
  points: Point[]
  /** The what-if re-run, drawn dashed over the original. */
  whatIf: { path: string; points: Point[] } | null
  /** Horizontal reference lines, labelled at the right edge. */
  levels: { y: number; label: string; danger: boolean }[]
  xTicks: { x: number; label: string }[]
  yTicks: { y: number; label: string }[]
}

/** Per second, smallest first: the backend's time units. */
const TIME_UNITS: [Simulation["t_unit"], number][] = [
  ["ns", 1e9],
  ["µs", 1e6],
  ["ms", 1e3],
]

function unitScale(unit: Simulation["t_unit"]): number {
  return TIME_UNITS.find(([u]) => u === unit)?.[1] ?? 1
}

/** The smallest unit that keeps `seconds` under 10,000 of it, as the
 *  backend picks a simulation's own unit. */
function timeUnit(seconds: number): [Simulation["t_unit"], number] {
  return TIME_UNITS.find(([, scale]) => seconds * scale <= 10_000) ?? ["ms", 1e3]
}

/** Where a simulated waveform goes: volts up, time across, with its
 *  reference lines (a flagged I²C line's rail and the IC's supply + 0.3 V;
 *  an RC delay's rail and 63% point). A what-if re-run shares the axes,
 *  which run long enough for the slower of the two. */
export function simulationLayout(sim: Simulation, whatIf?: Simulation | null): SimulationLayout {
  const { width, height, left, right, top, bottom } = PLOT
  const plot = { x0: left, x1: width - right, y0: height - bottom, y1: top }
  const runs = whatIf ? [sim, whatIf] : [sim]
  // Each run's times in seconds, then in the shared unit.
  const seconds = runs.map((r) => r.t.map((t) => t / unitScale(r.t_unit)))
  const [unit, scale] = whatIf
    ? timeUnit(Math.max(...seconds.flat()))
    : [sim.t_unit, unitScale(sim.t_unit)]
  const tMax = Math.max(...seconds.flat().map((t) => t * scale), 1e-9)
  const vMax = Math.ceil(Math.max(...sim.levels.map((l) => l.v), ...runs.flatMap((r) => r.v)))
  const x = (t: number) => plot.x0 + (t / tMax) * (plot.x1 - plot.x0)
  const y = (v: number) => plot.y0 - (v / vMax) * (plot.y0 - plot.y1)
  const trace = (r: Simulation, ts: number[]) => {
    const points = ts.map((sec, i) => {
      const t = Number((sec * scale).toPrecision(6))
      const v = r.v[i] ?? 0
      return { x: x(t), y: y(v), t, v }
    })
    const path = points
      .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(" ")
    return { path, points }
  }
  const { path, points } = trace(sim, seconds[0] ?? [])
  const levels = sim.levels.map((l) => ({ y: y(l.v), label: l.label, danger: l.danger }))
  const step = niceStep(tMax / 4)
  const xTicks = []
  for (let t = 0; t <= tMax + 1e-9; t += step) {
    xTicks.push({ x: x(t), label: String(Number(t.toPrecision(3))) })
  }
  const yTicks = []
  for (let v = 0; v <= vMax; v += 1) yTicks.push({ y: y(v), label: String(v) })
  return {
    width,
    height,
    plot,
    unit,
    path,
    points,
    whatIf: whatIf ? trace(whatIf, seconds[1] ?? []) : null,
    levels,
    xTicks,
    yTicks,
  }
}

/** The sample nearest to ``x`` on the plot, for the hover readout. */
export function nearestPoint(points: Point[], x: number): Point | null {
  let best: Point | null = null
  for (const p of points) {
    if (best === null || Math.abs(p.x - x) < Math.abs(best.x - x)) best = p
  }
  return best
}

/** The hover readout at ``x``: the original's sample, the what-if's when
 *  one is drawn, and "1.20 V → 2.41 V @ 10 ms". */
export function hoverReadout(
  layout: SimulationLayout,
  x: number,
): { x: number; dots: { y: number; dashed: boolean }[]; label: string } | null {
  const p = nearestPoint(layout.points, x)
  if (!p) return null
  const w = layout.whatIf ? nearestPoint(layout.whatIf.points, x) : null
  const volts = w ? `${p.v.toFixed(2)} V → ${w.v.toFixed(2)} V` : `${p.v.toFixed(2)} V`
  return {
    x: p.x,
    dots: [{ y: p.y, dashed: false }, ...(w ? [{ y: w.y, dashed: true }] : [])],
    label: `${volts} @ ${String(Number(p.t.toPrecision(3)))} ${layout.unit}`,
  }
}

const SI_PREFIX: Record<string, number> = {
  p: 1e-12,
  n: 1e-9,
  u: 1e-6,
  µ: 1e-6,
  μ: 1e-6,
  m: 1e-3,
  k: 1e3,
  K: 1e3,
  M: 1e6,
  meg: 1e6,
  Meg: 1e6,
  MEG: 1e6,
}

/** A part value as typed: `4.7k`, `4k7`, `1u`, `1µ`, `100n`, `2.2M`,
 *  `1e-6`, with an optional `Ω`/`ohm`/`F`. `m` is milli and `M` mega, as
 *  on a schematic. Null when it isn't a positive number. */
export function parseSi(text: string): number | null {
  const s = text.trim().replace(/\s*(Ω|[oO]hms?|F)$/, "")
  // Every group always matches, possibly empty.
  const m = /^(\d+(?:\.\d+)?|\.\d+)((?:e[+-]?\d+)?)\s*((?:meg|Meg|MEG|[pnuµμmkKM])?)(\d*)$/.exec(s)
  if (!m) return null
  const [, num, exp, prefix, tail] = m
  // 4k7: the prefix stands for the decimal point.
  if (tail && (!prefix || num.includes(".") || exp)) return null
  const mantissa = tail ? `${num}.${tail}` : num
  const value = Number(`${mantissa}${exp}`) * (prefix ? (SI_PREFIX[prefix] ?? 1) : 1)
  return Number.isFinite(value) && value > 0 ? value : null
}

/** A value with its SI prefix, three significant digits: 4700 → `4.7k`,
 *  3e-11 → `30p`; what a what-if field starts with. */
export function formatSi(value: number): string {
  const prefixes: [string, number][] = [
    ["M", 1e6],
    ["k", 1e3],
    ["", 1],
    ["m", 1e-3],
    ["µ", 1e-6],
    ["n", 1e-9],
    ["p", 1e-12],
  ]
  const [prefix, scale] = prefixes.find(([, sc]) => value >= sc * 0.9995) ?? ["p", 1e-12]
  return `${String(Number((value / scale).toPrecision(3)))}${prefix}`
}

/** The what-if fields' labels: the part's ref where the netlist has a
 *  single part (`R5`), else the quantity; an I²C line's C is the estimated
 *  bus capacitance, never a part. */
export function whatIfLabels(circuit: SimCircuit, m: Messages): { r: string; c: string } {
  const t = m.simulations.whatIf
  return {
    r: circuit.r_ref ?? t.r,
    c: circuit.kind === "i2c" ? t.busC : (circuit.c_ref ?? t.c),
  }
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

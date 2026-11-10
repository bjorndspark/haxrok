import { describe, expect, it } from "vitest"

import { isSchematicFile, projectName } from "./files"
import { MESSAGES } from "./messages"
import {
  findingTokens,
  markTokens,
  assess,
  calculationBadge,
  citedLines,
  datasheetBadge,
  datasheetLines,
  diagramLayout,
  formatSi,
  hoverReadout,
  isVendorModel,
  modelBadge,
  nearestPoint,
  parseSi,
  simulationLayout,
  splitGlossary,
  toMarkdown,
  whatIfLabels,
} from "./present"
import { fragmentLines, symptomHints, symptomLines } from "./symptoms"
import type {
  Calculation,
  CheckedAnalysis,
  CheckedClaim,
  DesignSummary,
  Hypothesis,
  Run,
  SimCircuit,
  Simulation,
} from "./types"

const en = MESSAGES.en

describe("symptoms", () => {
  it("numbers non-empty lines the way the backend does", () => {
    expect(symptomLines("- Fails on battery\n\n* Works on USB 5 V ")).toEqual([
      "Fails on battery",
      "Works on USB 5 V",
    ])
  })

  it("flags lines too short to stand on their own", () => {
    expect(fragmentLines("Fails on battery\nbattery")).toEqual(["battery"])
  })

  it("judges Japanese lines, which have no spaces, by length", () => {
    expect(fragmentLines("電池だと起動しない\n電池")).toEqual(["電池"])
  })

  it("notices Japanese answers too", () => {
    const done = symptomHints("USB では動く。電池だと起動しない。レギュレータが熱い。", en)
      .filter((h) => h.done)
      .map((h) => h.key)
    expect(done).toEqual(["power", "works", "fails", "hot"])
  })

  it("notices which questions the text answers", () => {
    const done = symptomHints("Powered from USB. When it fails, +3V3 reads 2.3 V.", en)
      .filter((h) => h.done)
      .map((h) => h.key)
    expect(done).toEqual(["power", "fails", "measured"])
  })
})

describe("splitGlossary", () => {
  const glossary = [
    { term: "buck-boost", definition: "Steps up or down." },
    { term: "ERC", definition: "KiCad's checker." },
  ]

  it("marks the first whole-word occurrence of each term", () => {
    const parts = splitGlossary("The buck-boost failed; ERC and buck-boost again.", glossary)
    expect(parts.filter((p) => p.term).map((p) => p.text)).toEqual(["buck-boost", "ERC"])
    expect(parts.map((p) => p.text).join("")).toBe(
      "The buck-boost failed; ERC and buck-boost again.",
    )
  })

  it("ignores terms inside other words", () => {
    expect(splitGlossary("HERCULES", glossary).every((p) => p.term === null)).toBe(true)
  })
})

describe("project files", () => {
  it("keeps only schematics and the project file", () => {
    expect(isSchematicFile("b/b.kicad_pro")).toBe(true)
    expect(isSchematicFile("b/b.kicad_pcb")).toBe(false)
  })

  it("names the project after its .kicad_pro", () => {
    const blob = new Blob([])
    expect(
      projectName([
        { path: "x/a.kicad_sch", file: blob },
        { path: "x/board.kicad_pro", file: blob },
      ]),
    ).toBe("board")
  })
})

describe("assess", () => {
  const claim = (evidence: CheckedClaim["evidence"], pin: string | null): CheckedClaim => ({
    text: "t",
    evidence,
    cites: pin ? [{ kind: "pin_on_net", pin, net: "+3V3", id: null }] : [],
    downgraded: false,
    claimed_evidence: null,
    reason: null,
  })
  const hypothesis = (claims: CheckedClaim[], explains: string[]): Hypothesis => ({
    id: "h1",
    rank: 1,
    severity: null,
    title: "t",
    summary: "s",
    claims,
    explains,
    does_not_explain: [],
    confirm: [],
    fix: { schematic: [], rework: [] },
  })
  const summary = {
    mode: "debug",
    symptoms: ["s1", "s2", "s3", "s4"].map((id) => ({ id, text: id })),
    wiring: [
      {
        id: "wf1",
        pin: "U2.9",
        pin_name: "L2",
        net: "+3V3",
        conflicts: ["U2.7"],
        inductors: ["L1.2"],
        text: "",
      },
    ],
  } as DesignSummary

  it("calls a backed claim on a flagged pin a verified wiring fault", () => {
    const a = assess(
      hypothesis([claim("schematic", "U2.9"), claim("none", null)], ["s1"]),
      summary,
      en,
    )
    expect(a.confidence).toBe("verified")
    expect([a.explained, a.symptoms, a.backed, a.claims]).toEqual([1, 4, 1, 2])
  })

  it("ignores a flagged pin cited only by a downgraded claim", () => {
    expect(assess(hypothesis([claim("none", "U2.9")], ["s1"]), summary, en).confidence).toBe(
      "speculative",
    )
  })

  it("verifies an issue that cites a flagged calculation", () => {
    const calc = {
      id: "cf1",
      kind: "regulator_output" as const,
      ic: "U5",
      pins: ["U5.5", "R2.1", "R2.2", "R3.1", "R3.2"],
      value: 0.73,
      unit: "V",
      expected: 3.3,
      flagged: true,
      text: "",
    }
    const review = {
      ...summary,
      mode: "review",
      symptoms: [],
      calculations: [calc],
    } as DesignSummary
    const byId: CheckedClaim = {
      ...claim("reference", null),
      cites: [{ kind: "reference", pin: null, net: null, id: "cf1" }],
    }
    expect(assess(hypothesis([byId], []), review, en).confidence).toBe("verified")
    expect(assess(hypothesis([claim("schematic", "R3.1")], []), review, en).confidence).toBe(
      "verified",
    )
  })

  it("verifies a review issue only through the wiring check", () => {
    const review = { ...summary, mode: "review", symptoms: [] } as DesignSummary
    expect(assess(hypothesis([claim("schematic", "U2.9")], []), review, en).confidence).toBe(
      "verified",
    )
    expect(assess(hypothesis([claim("schematic", "U1.2")], []), review, en).confidence).toBe(
      "speculative",
    )
  })

  it("needs half the symptoms and a backed claim to fit", () => {
    const backed = [claim("schematic", "U1.2")]
    expect(assess(hypothesis(backed, ["s1", "s2"]), summary, en).confidence).toBe("fits")
    expect(assess(hypothesis(backed, ["s1"]), summary, en).confidence).toBe("speculative")
    expect(assess(hypothesis([claim("none", null)], ["s1", "s2"]), summary, en).confidence).toBe(
      "speculative",
    )
  })
})

describe("diagramLayout", () => {
  it("groups pins by net, one bus per net, flagged net marked", () => {
    const layout = diagramLayout(
      {
        ic: "U2",
        value: "TPS63070",
        pins: [
          { pin: "U2.9", name: "L2", net: "+3V3", flagged: true },
          { pin: "U2.7", name: "VOUT", net: "+3V3", flagged: false },
          { pin: "U2.11", name: "L1", net: "/Power/SW1", flagged: false },
        ],
        nets: [
          { name: "+3V3", members: [{ pin: "L1.2", name: "", inductor: true }], more: 30 },
          { name: "/Power/SW1", members: [{ pin: "L1.1", name: "", inductor: true }], more: 1 },
        ],
      },
      en,
    )
    expect(layout.pins.map((p) => p.label)).toEqual(["9 L2", "7 VOUT", "11 L1"])
    const [rail, node] = layout.nets
    expect(rail.label).toBe("+3V3")
    expect(rail.flagged).toBe(true)
    expect(rail.members).toBe("L1.2 (inductor) · +30 more")
    expect(node.label).toBe("SW1")
    expect(node.flagged).toBe(false)
    expect(node.top).toBeGreaterThan(rail.bottom)
    expect(node.bottom).toBe(node.top)
    expect(layout.box.y + layout.box.height).toBeLessThanOrEqual(layout.height)
  })
})

describe("diagramLayout members", () => {
  it("folds members that don't fit into +N more", () => {
    const member = (n: number) => ({ pin: `U${String(n)}.1`, name: "VDD", inductor: false })
    const layout = diagramLayout(
      {
        ic: "U2",
        value: "",
        pins: [{ pin: "U2.7", name: "VOUT", net: "+3V3", flagged: false }],
        nets: [{ name: "+3V3", members: [1, 2, 3, 4, 5, 6].map(member), more: 30 }],
      },
      en,
    )
    const text = layout.nets[0].members
    expect(text.endsWith("+33 more")).toBe(true)
    expect(text.length).toBeLessThanOrEqual(56)
  })
})

describe("diagramLayout box", () => {
  it("widens the IC box so a long pin name isn't clipped", () => {
    const diagram = (name: string) =>
      diagramLayout(
        {
          ic: "U3",
          value: "ESP32-C3-MINI-1",
          pins: [{ pin: "U3.6", name, net: "IO0", flagged: false }],
          nets: [{ name: "IO0", members: [], more: 0 }],
        },
        en,
      )
    const short = diagram("EN")
    const long = diagram("GPIO0/ADC1_CH0/XTAL_32K_P")
    expect(short.box.width).toBe(170)
    expect(long.box.width).toBeGreaterThanOrEqual("6 GPIO0/ADC1_CH0/XTAL_32K_P".length * 7.9)
    expect(long.busX).toBeGreaterThan(long.box.x + long.box.width)
    expect(long.width).toBeGreaterThan(long.busX)
  })
})

describe("simulationLayout", () => {
  const sim: Simulation = {
    id: "sim1",
    calc_id: "cf1",
    title: "I²C SDA rising after release",
    deck: "",
    t: [0, 100, 200, 400, 700],
    t_unit: "ns",
    v: [0, 2.5, 3.8, 4.7, 4.97],
    levels: [
      { v: 5, label: "rail 5 V", danger: false },
      { v: 3.6, label: "VDD+0.3 3.6 V", danger: true },
    ],
    text: "",
  }

  it("spans the plot and marks the rail and the IC's limit", () => {
    const layout = simulationLayout(sim)
    const first = layout.points[0]
    const last = layout.points[layout.points.length - 1]
    expect([first.x, first.y, last.x]).toEqual([layout.plot.x0, layout.plot.y0, layout.plot.x1])
    expect(layout.path.startsWith("M")).toBe(true)
    expect(layout.levels.map((l) => l.label)).toEqual(["rail 5 V", "VDD+0.3 3.6 V"])
    const [rail, limit] = layout.levels.map((l) => l.y)
    expect(rail).toBeLessThan(limit) // 5 V sits above 3.6 V
    expect(layout.xTicks.map((t) => t.label)).toEqual(["0", "200", "400", "600"])
  })

  it("labels short spans with fractions, not rounded integers", () => {
    const short = { ...sim, t: [0, 0.5, 1, 2], t_unit: "µs" as const, v: [0, 2, 3, 3.3] }
    expect(simulationLayout(short).xTicks.map((t) => t.label)).toEqual([
      "0",
      "0.5",
      "1",
      "1.5",
      "2",
    ])
  })

  it("finds the sample under the cursor", () => {
    const layout = simulationLayout(sim)
    const third = layout.points[2].x
    expect(nearestPoint(layout.points, third + 1)?.v).toBe(3.8)
    expect(hoverReadout(layout, third + 1)?.label).toBe("3.80 V @ 200 ns")
  })

  it("draws a what-if on one time axis, long enough for the slower run", () => {
    // A what-if slower (22 µs) than the original (700 ns): past 10,000 ns, so µs.
    const whatIf: Simulation = { ...sim, t: [0, 1, 22], t_unit: "µs", v: [0, 4, 4.99] }
    const layout = simulationLayout(sim, whatIf)
    expect(layout.unit).toBe("µs")
    expect(layout.points.map((p) => p.t)).toEqual([0, 0.1, 0.2, 0.4, 0.7])
    const last = layout.whatIf?.points[2]
    expect(last?.x).toBe(layout.plot.x1) // the slower run spans the axis
    expect(layout.points[4].x).toBeCloseTo(
      layout.plot.x0 + (0.7 / 22) * (layout.plot.x1 - layout.plot.x0),
    )
    expect(layout.whatIf?.path.startsWith("M")).toBe(true)
    const hover = hoverReadout(layout, layout.points[4].x)
    expect(hover?.dots.map((d) => d.dashed)).toEqual([false, true])
    expect(hover?.label).toBe("4.97 V → 4.00 V @ 0.7 µs")
    // A faster what-if keeps the original's unit and span.
    const fast = simulationLayout(sim, { ...sim, t: [0, 300], v: [0, 4.9] })
    expect(fast.unit).toBe("ns")
    expect(fast.points[4].x).toBe(fast.plot.x1)
    expect(simulationLayout(sim).whatIf).toBeNull()
  })
})

describe("parseSi", () => {
  it("reads the values a schematic writes", () => {
    const cases: [string, number][] = [
      ["4.7k", 4700],
      ["4k7", 4700],
      ["10K", 10_000],
      ["1u", 1e-6],
      ["1µ", 1e-6],
      ["1μF", 1e-6],
      ["100n", 100e-9],
      ["30p", 30e-12],
      ["2.2M", 2.2e6],
      ["1meg", 1e6],
      ["470m", 0.47],
      ["4.7 kΩ", 4700],
      ["220ohm", 220],
      ["1e-6", 1e-6],
      [" 47 ", 47],
      [".5", 0.5],
    ]
    for (const [text, value] of cases) expect(parseSi(text), text).toBeCloseTo(value, 15)
  })

  it("refuses what isn't a positive value", () => {
    for (const text of ["", "k", "abc", "-1k", "0", "4.7k7", "1x", "1f", "1e5k7"]) {
      expect(parseSi(text), text).toBeNull()
    }
  })
})

describe("formatSi", () => {
  it("writes a value with its prefix, three digits", () => {
    expect([4700, 10_000, 30e-12, 1e-6, 100e-9, 2.2e6, 47, 0.47].map(formatSi)).toEqual([
      "4.7k",
      "10k",
      "30p",
      "1µ",
      "100n",
      "2.2M",
      "47",
      "470m",
    ])
    for (const v of [4700, 33e-12, 1e-6, 0.1]) expect(parseSi(formatSi(v))).toBeCloseTo(v, 15)
  })
})

describe("whatIfLabels", () => {
  const circuit: SimCircuit = {
    kind: "rc_delay",
    net: "EN",
    r_ohm: 10_000,
    c_farad: 1e-6,
    v_rail: 3.3,
    v_supply: null,
    r_ref: "R5",
    c_ref: "C5",
  }

  it("names the parts, or the bus C on an I²C line", () => {
    const m = en
    expect(whatIfLabels(circuit, m)).toEqual({ r: "R5", c: "C5" })
    expect(whatIfLabels({ ...circuit, r_ref: null, c_ref: null }, m)).toEqual({ r: "R", c: "C" })
    expect(whatIfLabels({ ...circuit, kind: "i2c", c_ref: null }, m)).toEqual({
      r: "R5",
      c: "bus C (estimated)",
    })
  })
})

describe("calculationBadge", () => {
  const calc = (over: Partial<Calculation>): Calculation => ({
    id: "cf1",
    kind: "usb_cc",
    ic: "J1",
    pins: [],
    value: 56000,
    unit: "Ω",
    expected: 5100,
    flagged: true,
    text: "",
    ...over,
  })

  it("compares against the expected value in the calculation's own unit", () => {
    expect(calculationBadge(calc({}), en)).toBe("56000 Ω ≠ 5100 Ω")
    expect(calculationBadge(calc({ value: 5100, flagged: false }), en)).toBe("5100 Ω ✓")
  })

  it("says miswired for a UART flag and missing for a value that isn't there", () => {
    expect(
      calculationBadge(calc({ kind: "uart", value: null, unit: "", expected: null }), en),
    ).toBe("miswired")
    expect(calculationBadge(calc({ kind: "i2c_pullup", value: null, expected: null }), en)).toBe(
      "missing",
    )
  })
})

describe("toMarkdown", () => {
  it("writes each simulation with its netlist and the questions asked", () => {
    const sim: Simulation = {
      id: "sim1",
      calc_id: "cf2",
      title: "EN rising through its RC delay",
      deck: "RC on EN\nRpull rail pin 10000\n",
      t: [0, 50],
      t_unit: "ms",
      v: [0, 3.28],
      levels: [],
      text: "ngspice: 10 kΩ from 3.3 V into 1 µF.",
    }
    const run: Run = {
      number: 1,
      trigger: "Review before ordering",
      startedAt: 0,
      finishedAt: 1,
      summary: null,
      stage: null,
      result: null,
      error: null,
      streaming: false,
      simulations: [sim],
      chats: { sim1: [{ question: "When does it pass 63%?", answer: "At 10 ms." }] },
      whatIfs: { sim1: { ...sim, text: "What-if: R1 10 kΩ → 4.7 kΩ." } },
      resultChat: [{ question: "Which finding first?", answer: "None were found." }],
    }
    const summary = {
      mode: "review",
      project: "board",
      parts: 3,
      nets: 2,
      erc: [],
      symptoms: [],
      measurements: [],
    } as unknown as DesignSummary
    const result = {
      hypotheses: [],
      open_questions: [],
      glossary: [],
    } as unknown as CheckedAnalysis
    const md = toMarkdown(run, summary, result, en, "Häxrök")
    expect(md).toContain("## Simulations\n\n### EN rising through its RC delay")
    expect(md).toContain("```spice\nRC on EN\nRpull rail pin 10000\n```")
    expect(md).toContain("**Q:** When does it pass 63%?\n\nAt 10 ms.")
    expect(md).not.toContain("What-if") // the report leaves what-ifs out
    expect(md).toContain(
      "## Follow-up questions\n\n**Q:** Which finding first?\n\nNone were found.",
    )
  })
})

const sheetSummary = {
  mode: "review",
  project: "board",
  parts: 3,
  nets: 2,
  erc: [],
  symptoms: [],
  measurements: [],
  datasheets: [
    {
      ref: "U2",
      value: "STM32G0",
      status: "found",
      source: "lcsc",
      url: "https://x.test/u2.pdf",
      part_number: null,
      reason: null,
    },
    {
      ref: "U3",
      value: "TPS63020",
      status: "found",
      source: "lcsc",
      url: "https://x.test/u3.pdf",
      part_number: null,
      reason: null,
    },
    {
      ref: "U4",
      value: "ESP32",
      status: "not_found",
      source: null,
      url: null,
      part_number: null,
      reason: "no LCSC part number",
    },
  ],
  spice_models: [
    {
      ref: "U2",
      value: "STM32G0",
      status: "none",
      source: null,
      url: null,
      file: null,
      subckt: null,
      part_number: null,
      reason: "no model on the vendor's page",
      loads: null,
    },
    {
      ref: "U3",
      value: "TPS63020",
      status: "pspice",
      source: "web",
      url: null,
      file: "tps63020.lib",
      subckt: "TPS63020",
      part_number: null,
      reason: "ngspice: unknown parameter",
      loads: false,
    },
  ],
  datasheet_facts: [
    { id: "ds1", ref: "U2", page: 44, text: "VDD supply voltage: 1.7 to 3.6 V" },
    { id: "ds2", ref: "U2", page: 4, text: "VDDA must equal VDD" },
    { id: "ds3", ref: "U2", page: 44, text: "VDDA filter: 1 µF" },
  ],
  deviations: [{ id: "dv1", ref: "U2", page: 44, text: "VDDA has no 1 µF capacitor" }],
  datasheet_failures: [{ ref: "U3", reason: "the PDF has no text (a scan?)" }],
} as unknown as DesignSummary

const datasheetClaim: CheckedClaim = {
  text: "VDDA has no filter capacitor.",
  evidence: "datasheet",
  cites: [
    { kind: "datasheet", pin: null, net: null, id: "dv1" },
    { kind: "datasheet", pin: null, net: null, id: "ds3" },
    { kind: "datasheet", pin: null, net: null, id: "ds9" },
    { kind: "pin_on_net", pin: "U2.9", net: "+3V3", id: null },
  ],
  downgraded: false,
  claimed_evidence: null,
  reason: null,
}

describe("datasheetBadge", () => {
  it("links a read datasheet and lists the pages its facts came from", () => {
    expect(datasheetBadge("U2", sheetSummary, en)).toEqual({
      label: "datasheet",
      tone: "ok",
      url: "https://x.test/u2.pdf",
      pages: "pp. 4, 44",
      reason: null,
    })
  })

  it("warns about a datasheet that was found but couldn't be read", () => {
    const badge = datasheetBadge("U3", sheetSummary, en)
    expect([badge?.tone, badge?.pages, badge?.reason]).toEqual([
      "warn",
      null,
      "the PDF has no text (a scan?)",
    ])
  })

  it("says why there is no datasheet, and nothing for older runs", () => {
    expect(datasheetBadge("U4", sheetSummary, en)?.reason).toBe("no LCSC part number")
    expect(datasheetBadge("U9", sheetSummary, en)).toBeNull()
  })
})

describe("modelBadge", () => {
  it("names the format and that ngspice doesn't load it, with why", () => {
    expect(modelBadge("U3", sheetSummary, en)).toMatchObject({
      label: "PSpice model · doesn't load",
      tone: "warn",
      reason: "ngspice: unknown parameter",
    })
  })

  it("shows no model with the reason", () => {
    expect(modelBadge("U2", sheetSummary, en)).toMatchObject({
      label: "no model",
      tone: "neutral",
      reason: "no model on the vendor's page",
    })
  })
})

describe("citedLines", () => {
  it("lists the cited facts and deviations in order, linked to their page", () => {
    expect(citedLines(datasheetClaim, sheetSummary).map((l) => [l.id, l.url])).toEqual([
      ["dv1", "https://x.test/u2.pdf#page=44"],
      ["ds3", "https://x.test/u2.pdf#page=44"],
    ])
  })

  it("is empty without a summary", () => {
    expect(citedLines(datasheetClaim, null)).toEqual([])
  })
})

describe("datasheetLines", () => {
  it("groups the facts by IC and keeps the deviations apart", () => {
    const read = datasheetLines(sheetSummary)
    expect(read.facts.map((g) => [g.ref, g.lines.map((l) => l.id)])).toEqual([
      ["U2", ["ds1", "ds2", "ds3"]],
    ])
    expect(read.deviations[0].url).toBe("https://x.test/u2.pdf#page=44")
  })
})

describe("toMarkdown with datasheets and vendor models", () => {
  const vendor: Simulation = {
    id: "vm1",
    calc_id: null,
    ref: "U3",
    analysis: "startup",
    verdict: "flagged",
    title: "U3 start-up with its vendor model",
    deck: "",
    t: [],
    t_unit: "ms",
    v: [],
    levels: [],
    text: "+3V3 overshoots to 4.3 V, past U2's 3.6 V.",
  }
  const run: Run = {
    number: 1,
    trigger: "Review before ordering",
    startedAt: 0,
    finishedAt: 1,
    summary: null,
    stage: null,
    result: null,
    error: null,
    streaming: false,
    simulations: [vendor],
    chats: {},
    whatIfs: {},
    resultChat: [],
  }
  const result = {
    hypotheses: [
      {
        id: "h1",
        rank: 1,
        severity: "will_fail",
        title: "VDDA unfiltered",
        summary: "s",
        claims: [datasheetClaim],
        explains: [],
        does_not_explain: [],
        confirm: [],
        fix: { schematic: [], rework: [] },
      },
    ],
    open_questions: [],
    glossary: [],
  } as unknown as CheckedAnalysis
  const md = toMarkdown(run, sheetSummary, result, en, "Häxrök")

  it("lists each claim's cited datasheet lines with a page link", () => {
    expect(md).toContain(
      "- VDDA has no filter capacitor. — *Based on the datasheet*\n" +
        "  - dv1 · [p.44](https://x.test/u2.pdf#page=44) VDDA has no 1 µF capacitor\n" +
        "  - ds3 · [p.44](https://x.test/u2.pdf#page=44) VDDA filter: 1 µF\n",
    )
  })

  it("counts a skipped regulator, which has no analysis, as a vendor model", () => {
    const skipped = { ...vendor, analysis: null, verdict: "skipped" as const }
    expect(isVendorModel(skipped)).toBe(true)
    expect(isVendorModel({ ...vendor, ref: null, analysis: null, calc_id: "cf1" })).toBe(false)
  })

  it("writes vendor-model runs under their own heading with the verdict", () => {
    expect(md).toContain(
      "## Simulated with vendor models\n\n### U3 start-up with its vendor model\n\n" +
        "Verdict: **flagged**\n\n+3V3 overshoots to 4.3 V, past U2's 3.6 V.",
    )
    expect(md).not.toContain("## Simulations\n")
    expect(md).not.toContain("```spice")
  })
})

describe("markTokens", () => {
  const marked = (text: string, ref: string[], net: string[]) =>
    markTokens(text, { ref, net })
      .filter((p) => p.kind)
      .map((p) => `${p.kind ?? ""}:${p.text}`)

  it("marks whole refs and nets, including nets with spaces", () => {
    expect(
      marked(
        "Q1 (AO3400): net /Power Control/Q_GATE (drawn on G) lands on the S pad (Q1.2); net GND",
        ["Q1", "Q1.2"],
        ["/Power Control/Q_GATE", "GND"],
      ),
    ).toEqual(["ref:Q1", "net:/Power Control/Q_GATE", "ref:Q1.2", "net:GND"])
  })

  it("doesn't mark inside a longer word, ref or path", () => {
    expect(marked("U1.23 U1.2X /A/GND GNDA U1.2", ["U1.2", "U1"], ["GND"])).toEqual(["ref:U1.2"])
  })

  it("keeps the text intact", () => {
    const text = "U5.9 (L2) on +3V3 also connects U5.7(VOUT)."
    const parts = markTokens(text, { ref: ["U5.9", "U5.7"], net: ["+3V3"] })
    expect(parts.map((p) => p.text).join("")).toBe(text)
    expect(parts.filter((p) => p.kind).map((p) => p.text)).toEqual(["U5.9", "+3V3", "U5.7"])
  })
})

describe("findingTokens", () => {
  it("takes pins, their parts and nets, and tolerates runs without nets", () => {
    expect(findingTokens({ id: "pw1", ref: "Q1", pins: ["Q1.1"], text: "" })).toEqual({
      ref: ["Q1.1", "Q1"],
      net: [],
    })
  })
})

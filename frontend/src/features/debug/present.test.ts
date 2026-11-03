import { describe, expect, it } from "vitest"

import { isSchematicFile, projectName } from "./files"
import { assess, diagramLayout, nearestPoint, simulationLayout, splitGlossary } from "./present"
import { fragmentLines, symptomHints, symptomLines } from "./symptoms"
import type { CheckedClaim, DesignSummary, Hypothesis, Simulation } from "./types"

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

  it("notices which questions the text answers", () => {
    const done = symptomHints("Powered from USB. When it fails, +3V3 reads 2.3 V.")
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
    const a = assess(hypothesis([claim("schematic", "U2.9"), claim("none", null)], ["s1"]), summary)
    expect(a.confidence).toBe("verified")
    expect([a.explained, a.symptoms, a.backed, a.claims]).toEqual([1, 4, 1, 2])
  })

  it("ignores a flagged pin cited only by a downgraded claim", () => {
    expect(assess(hypothesis([claim("none", "U2.9")], ["s1"]), summary).confidence).toBe(
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
      simulation: null,
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
    expect(assess(hypothesis([byId], []), review).confidence).toBe("verified")
    expect(assess(hypothesis([claim("schematic", "R3.1")], []), review).confidence).toBe("verified")
  })

  it("verifies a review issue only through the wiring check", () => {
    const review = { ...summary, mode: "review", symptoms: [] } as DesignSummary
    expect(assess(hypothesis([claim("schematic", "U2.9")], []), review).confidence).toBe("verified")
    expect(assess(hypothesis([claim("schematic", "U1.2")], []), review).confidence).toBe(
      "speculative",
    )
  })

  it("needs half the symptoms and a backed claim to fit", () => {
    const backed = [claim("schematic", "U1.2")]
    expect(assess(hypothesis(backed, ["s1", "s2"]), summary).confidence).toBe("fits")
    expect(assess(hypothesis(backed, ["s1"]), summary).confidence).toBe("speculative")
    expect(assess(hypothesis([claim("none", null)], ["s1", "s2"]), summary).confidence).toBe(
      "speculative",
    )
  })
})

describe("diagramLayout", () => {
  it("groups pins by net, one bus per net, flagged net marked", () => {
    const layout = diagramLayout({
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
    })
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
    const layout = diagramLayout({
      ic: "U2",
      value: "",
      pins: [{ pin: "U2.7", name: "VOUT", net: "+3V3", flagged: false }],
      nets: [{ name: "+3V3", members: [1, 2, 3, 4, 5, 6].map(member), more: 30 }],
    })
    const text = layout.nets[0].members
    expect(text.endsWith("+33 more")).toBe(true)
    expect(text.length).toBeLessThanOrEqual(56)
  })
})

describe("diagramLayout box", () => {
  it("widens the IC box so a long pin name isn't clipped", () => {
    const diagram = (name: string) =>
      diagramLayout({
        ic: "U3",
        value: "ESP32-C3-MINI-1",
        pins: [{ pin: "U3.6", name, net: "IO0", flagged: false }],
        nets: [{ name: "IO0", members: [], more: 0 }],
      })
    const short = diagram("EN")
    const long = diagram("GPIO0/ADC1_CH0/XTAL_32K_P")
    expect(short.box.width).toBe(150)
    expect(long.box.width).toBeGreaterThanOrEqual("6 GPIO0/ADC1_CH0/XTAL_32K_P".length * 6.7)
    expect(long.busX).toBeGreaterThan(long.box.x + long.box.width)
    expect(long.width).toBeGreaterThan(long.busX)
  })
})

describe("simulationLayout", () => {
  const sim: Simulation = {
    t_ns: [0, 100, 200, 400, 700],
    v: [0, 2.5, 3.8, 4.7, 4.97],
    v_pull: 5,
    v_supply: 3.3,
    rise_ns: 120,
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

  it("finds the sample under the cursor", () => {
    const layout = simulationLayout(sim)
    const third = layout.points[2].x
    expect(nearestPoint(layout, third + 1)?.v).toBe(3.8)
  })
})

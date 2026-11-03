import { describe, expect, it } from "vitest"

import { isSchematicFile, projectName } from "./files"
import { assess, splitGlossary } from "./present"
import { fragmentLines, symptomHints, symptomLines } from "./symptoms"
import type { CheckedClaim, DesignSummary, Hypothesis } from "./types"

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
    title: "t",
    summary: "s",
    claims,
    explains,
    does_not_explain: [],
    confirm: [],
    fix: { schematic: [], rework: [] },
  })
  const summary = {
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

  it("needs half the symptoms and a backed claim to fit", () => {
    const backed = [claim("schematic", "U1.2")]
    expect(assess(hypothesis(backed, ["s1", "s2"]), summary).confidence).toBe("fits")
    expect(assess(hypothesis(backed, ["s1"]), summary).confidence).toBe("speculative")
    expect(assess(hypothesis([claim("none", null)], ["s1", "s2"]), summary).confidence).toBe(
      "speculative",
    )
  })
})

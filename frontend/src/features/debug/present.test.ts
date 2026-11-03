import { describe, expect, it } from "vitest"

import { isSchematicFile, projectName } from "./files"
import { splitGlossary } from "./present"
import { fragmentLines, symptomHints, symptomLines } from "./symptoms"

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

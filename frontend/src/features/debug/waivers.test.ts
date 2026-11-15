import { describe, expect, it } from "vitest"

import type { ReviewDiff, Waiver } from "./types"
import { accept, quietIds, removeWaiver, waiverOn } from "./waivers"

const DATE = new Date("2026-11-15T09:00:00Z")

function waiver(key: string, pins: Record<string, string>): Waiver {
  return {
    key,
    source: "issue",
    pins,
    title: key,
    reason: "ok",
    by: "tm",
    date: DATE.toISOString(),
  }
}

const OLD = waiver("issue:U1.1", { "U1.1": "old" })
const KEPT = waiver("issue:J9.1", { "J9.1": "j" })

/** h1 waived but lapsed (U1.1 changed), h2 not waived, KEPT finds nothing. */
function diff(): ReviewDiff {
  return {
    baseline_run_id: "r1",
    baseline_date: DATE.toISOString(),
    note: null,
    marks: [],
    fixed: [],
    waived: [{ id: "h1", index: 0, waiver: OLD, lapsed_pins: ["U1.1"] }],
    found: [
      { source: "issue", id: "h1", key: "issue:U1.1", pins: { "U1.1": "new" } },
      { source: "issue", id: "h2", key: "issue:R2.1", pins: { "R2.1": "r" } },
    ],
    next: {
      version: 2,
      project: "board",
      run_id: "r2",
      date: DATE.toISOString(),
      findings: [],
      waivers: [OLD, KEPT],
    },
  }
}

describe("waivers", () => {
  it("accepting an issue writes a waiver at its current pins and quiets it", () => {
    const got = accept(diff(), "h2", "R2 pull-up", "fine", "tm", DATE)
    expect(got.next.waivers[got.next.waivers.length - 1]).toEqual({
      key: "issue:R2.1",
      source: "issue",
      pins: { "R2.1": "r" },
      title: "R2 pull-up",
      reason: "fine",
      by: "tm",
      date: DATE.toISOString(),
    })
    expect(waiverOn(got, "h2")?.index).toBe(2)
    expect([...quietIds(got)]).toEqual(["h2"])
  })

  it("accepting a lapsed issue again replaces its waiver", () => {
    const got = accept(diff(), "h1", "U1 boot", "still fine", "tm", DATE)
    expect(got.next.waivers.map((w) => w.key)).toEqual(["issue:J9.1", "issue:U1.1"])
    expect(got.next.waivers[1].pins).toEqual({ "U1.1": "new" })
    expect(waiverOn(got, "h1")).toMatchObject({ index: 1, lapsed_pins: [] })
    expect(quietIds(got).has("h1")).toBe(true)
  })

  it("removing a waiver keeps the others and moves their hits", () => {
    const both = accept(diff(), "h2", "R2", "fine", "tm", DATE)
    const got = removeWaiver(both, "h1")
    expect(got.next.waivers.map((w) => w.key)).toEqual(["issue:J9.1", "issue:R2.1"])
    expect(waiverOn(got, "h1")).toBeNull()
    expect(waiverOn(got, "h2")?.index).toBe(1)
  })

  it("a lapsed waiver doesn't quiet its issue; an unknown id changes nothing", () => {
    expect(quietIds(diff()).size).toBe(0)
    const d = diff()
    expect(accept(d, "h9", "t", "r", "tm", DATE)).toBe(d)
    expect(removeWaiver(d, "h2")).toBe(d)
  })
})

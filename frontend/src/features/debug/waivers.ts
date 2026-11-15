import type { ReviewDiff, Waiver, WaiverHit } from "./types"

/**
 * Waivers on a review (plan 011 step 4). The backend finds which waivers in
 * the uploaded review file match this run's findings (`ReviewDiff.waived`);
 * here the user accepts an issue or removes a waiver, which edits the run's
 * diff in place so the issue folds at once and "Save review file" writes
 * the change. Pure functions, plus the remembered reviewer name.
 */

const REVIEWER_KEY = "reviewer"

/** The waiver on finding `id` this run, or null. */
export function waiverOn(diff: ReviewDiff | null, id: string): WaiverHit | null {
  return diff?.waived.find((h) => h.id === id) ?? null
}

/** Ids of the findings a waiver keeps quiet: waived, and not lapsed. */
export function quietIds(diff: ReviewDiff | null): Set<string> {
  return new Set(diff?.waived.filter((h) => h.lapsed_pins.length === 0).map((h) => h.id))
}

/** Accept finding `id` at its pins' current fingerprints, replacing any
 *  waiver it had (a lapsed one, say). Unknown ids leave the diff as it is. */
export function accept(
  diff: ReviewDiff,
  id: string,
  title: string,
  reason: string,
  by: string,
  date: Date,
): ReviewDiff {
  const found = diff.found.find((f) => f.id === id)
  if (!found) return diff
  const base = removeWaiver(diff, id)
  const waiver: Waiver = {
    key: found.key,
    source: found.source,
    pins: found.pins,
    title,
    reason,
    by,
    date: date.toISOString(),
  }
  const index = base.next.waivers.length
  return {
    ...base,
    waived: [...base.waived, { id, index, waiver, lapsed_pins: [] }],
    next: { ...base.next, waivers: [...base.next.waivers, waiver] },
  }
}

/** Drop the waiver on finding `id` from the file to save; the others keep
 *  their order and their hits point at their new places. */
export function removeWaiver(diff: ReviewDiff, id: string): ReviewDiff {
  const hit = waiverOn(diff, id)
  if (!hit) return diff
  return {
    ...diff,
    waived: diff.waived
      .filter((h) => h.id !== id)
      .map((h) => (h.index > hit.index ? { ...h, index: h.index - 1 } : h)),
    next: { ...diff.next, waivers: diff.next.waivers.filter((_, i) => i !== hit.index) },
  }
}

/** The name last typed into an accept form; "" when none or storage is blocked. */
export function savedReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) ?? ""
  } catch {
    return ""
  }
}

/** Remember the reviewer's name in this browser, a convenience only: the
 *  review file in git is the record of who accepted what. */
export function saveReviewer(name: string): void {
  try {
    localStorage.setItem(REVIEWER_KEY, name)
  } catch {
    // Storage blocked (private mode): typed again next time.
  }
}

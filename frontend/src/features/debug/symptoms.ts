/**
 * Nudges for the symptom box. The backend numbers each non-empty line as a
 * symptom (s1, s2, …), so each line has to stand on its own: "Fails on
 * battery", not "battery". These checks are hints, never a gate.
 */

import type { Messages } from "./messages"

export interface SymptomHint {
  key: keyof Messages["symptoms"]["hints"]
  label: string
  done: boolean
}

// English words need \b so "ok" doesn't match inside "book"; Japanese has no
// word boundaries, so its keywords are a second, unbounded alternative.
const TOPICS: { key: SymptomHint["key"]; pattern: RegExp }[] = [
  {
    key: "power",
    pattern:
      /\b(usb|batter|nimh|li-?ion|lipo|alkaline|bench supply|supply|power(ed)?|\d+(\.\d+)?\s?v)\b|電源|電池|バッテリ|給電|安定化電源/i,
  },
  {
    key: "works",
    pattern: /\b(works?|boots?|ok|fine|runs?)\b|動く|動作する|起動する|正常/i,
  },
  {
    key: "fails",
    pattern:
      /\b(fails?|doesn'?t|does not|won'?t|no(t)? boot|dead|stuck|resets?|drops?|collapses?)\b|動かない|起動しない|しない|止まる|リセット|落ちる|不具合|ダメ/i,
  },
  {
    key: "measured",
    pattern:
      /(\d+(\.\d+)?\s?(m?a|m?v|v|ω|ohm|ohms|k|°c)\b|measur|reads?|multimeter|dmm)|測定|測っ|計測|テスター|マルチメータ/i,
  },
  {
    key: "hot",
    pattern: /\b(hot|warm|heat|burn|smok|°c|cool)\b|熱|温か|発熱|焦げ|煙/i,
  },
]

/** Which of the questions the placeholder asks the text already answers. */
export function symptomHints(text: string, m: Messages): SymptomHint[] {
  return TOPICS.map((t) => ({
    key: t.key,
    label: m.symptoms.hints[t.key],
    done: t.pattern.test(text),
  }))
}

/** Non-empty lines, as the backend will number them. */
export function symptomLines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim().replace(/^[-*•]\s*/, ""))
    .filter((l) => l.length > 0)
}

const CJK = /[\u3040-\u30ff\u3400-\u9fff]/

/** Lines too short to stand on their own (likely a fragment of the line
 *  above): under three words, or for Japanese, which has no spaces, under
 *  six characters. */
export function fragmentLines(text: string): string[] {
  return symptomLines(text).filter((l) =>
    CJK.test(l) ? l.replace(/\s/g, "").length < 6 : l.split(/\s+/).length < 3,
  )
}

/**
 * Nudges for the symptom box. The backend numbers each non-empty line as a
 * symptom (s1, s2, …), so each line has to stand on its own: "Fails on
 * battery", not "battery". These checks are hints, never a gate.
 */

export interface SymptomHint {
  key: string
  label: string
  done: boolean
}

const TOPICS: { key: string; label: string; pattern: RegExp }[] = [
  {
    key: "power",
    label: "What powers it",
    pattern:
      /\b(usb|batter|nimh|li-?ion|lipo|alkaline|bench supply|supply|power(ed)?|\d+(\.\d+)?\s?v)\b/i,
  },
  { key: "works", label: "What works", pattern: /\b(works?|boots?|ok|fine|runs?)\b/i },
  {
    key: "fails",
    label: "What doesn't",
    pattern:
      /\b(fails?|doesn'?t|does not|won'?t|no(t)? boot|dead|stuck|resets?|drops?|collapses?)\b/i,
  },
  {
    key: "measured",
    label: "What you measured",
    pattern: /(\d+(\.\d+)?\s?(m?a|m?v|v|ω|ohm|ohms|k|°c)\b|measur|reads?|multimeter|dmm)/i,
  },
  { key: "hot", label: "Anything hot", pattern: /\b(hot|warm|heat|burn|smok|°c|cool)\b/i },
]

/** Which of the questions the placeholder asks the text already answers. */
export function symptomHints(text: string): SymptomHint[] {
  return TOPICS.map((t) => ({ key: t.key, label: t.label, done: t.pattern.test(text) }))
}

/** Non-empty lines, as the backend will number them. */
export function symptomLines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim().replace(/^[-*•]\s*/, ""))
    .filter((l) => l.length > 0)
}

/** Lines too short to stand on their own (likely a fragment of the line above). */
export function fragmentLines(text: string): string[] {
  return symptomLines(text).filter((l) => l.split(/\s+/).length < 3)
}

export const SYMPTOM_PLACEHOLDER = `One observation per line, each standing on its own:
Powered from a battery through the regulator.
Works on USB 5 V: the microcontroller boots.
Fails on battery: the microcontroller does not boot.
When it fails, the 3.3 V rail measures 2.3 V and the supply draws 400 mA.
When it fails, the regulator gets warm.`

export const SYMPTOM_PROMPTS: { label: string; prefix: string }[] = [
  { label: "Power source", prefix: "Powered from" },
  { label: "What works", prefix: "Works on" },
  { label: "What fails", prefix: "Fails on" },
  { label: "Measurement", prefix: "When it fails, I measured" },
  { label: "Hot part", prefix: "When it fails, this part gets hot:" },
]

import { create } from "zustand"

export type Language = "en" | "ja"

const KEY = "language"

function initialLanguage(): Language {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === "en" || saved === "ja") return saved
  } catch {
    // Storage blocked (private mode): fall back to English.
  }
  return "en"
}

function apply(language: Language): void {
  document.documentElement.lang = language
}

interface LanguageState {
  language: Language
  toggle: () => void
}

/** The UI language (English or Japanese), set as `lang` on <html> and
 *  remembered per browser. Features keep their own message tables. */
export const useLanguage = create<LanguageState>((set, get) => {
  const language = initialLanguage()
  apply(language)
  return {
    language,
    toggle: () => {
      const next: Language = get().language === "en" ? "ja" : "en"
      apply(next)
      try {
        localStorage.setItem(KEY, next)
      } catch {
        // Not remembered; still applied for this visit.
      }
      set({ language: next })
    },
  }
})

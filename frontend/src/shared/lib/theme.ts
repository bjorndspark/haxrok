import { create } from "zustand"

export type Theme = "light" | "dark"

const KEY = "theme"

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === "light" || saved === "dark") return saved
  } catch {
    // Storage blocked (private mode): fall back to the system preference.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

function apply(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark")
}

interface ThemeState {
  theme: Theme
  toggle: () => void
}

/** Light/dark theme, applied as the `dark` class on <html> and remembered
 *  per browser. */
export const useTheme = create<ThemeState>((set, get) => {
  const theme = initialTheme()
  apply(theme)
  return {
    theme,
    toggle: () => {
      const next: Theme = get().theme === "dark" ? "light" : "dark"
      apply(next)
      try {
        localStorage.setItem(KEY, next)
      } catch {
        // Not remembered; still applied for this visit.
      }
      set({ theme: next })
    },
  }
})

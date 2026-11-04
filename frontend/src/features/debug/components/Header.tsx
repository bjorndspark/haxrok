import { useLanguage } from "@/shared/lib/language"
import { useTheme } from "@/shared/lib/theme"
import { Button } from "@/shared/ui/button"

import { useMessages } from "../messages"

// index.html's <title> carries PROJECT_NAME (vite.config.ts), so the name stays config.
const APP_NAME = document.title

interface HeaderProps {
  onNewAnalysis: () => void
  showNew: boolean
}

/** top's first line: the name in reverse video, what it does, then the keys. */
export function Header({ onNewAnalysis, showNew }: HeaderProps) {
  const theme = useTheme((s) => s.theme)
  const toggleTheme = useTheme((s) => s.toggle)
  const toggleLanguage = useLanguage((s) => s.toggle)
  const m = useMessages().header
  return (
    <header className="sticky top-0 z-30 border-b bg-background">
      <div className="mx-auto flex max-w-4xl items-center gap-[1.5ch] px-4 py-3 sm:px-6">
        <span className="bg-foreground px-[1ch] font-bold tracking-wider text-background">
          {APP_NAME}
        </span>
        <span className="hidden min-w-0 truncate text-muted-foreground sm:inline">{m.tagline}</span>
        <div className="ml-auto flex items-center gap-[1ch]">
          {showNew && (
            <Button variant="outline" size="sm" onClick={onNewAnalysis}>
              {m.newAnalysis}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={toggleLanguage}
            title={m.switchLanguage}
            aria-label={m.switchLanguage}
          >
            {m.otherLanguage}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={toggleTheme}
            title={theme === "dark" ? m.toLight : m.toDark}
            aria-label={m.toggleTheme}
          >
            {theme === "dark" ? m.light : m.dark}
          </Button>
        </div>
      </div>
    </header>
  )
}

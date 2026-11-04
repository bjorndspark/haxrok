import { useTheme } from "@/shared/lib/theme"
import { Button } from "@/shared/ui/button"

// index.html's <title> carries PROJECT_NAME (vite.config.ts), so the name stays config.
const APP_NAME = document.title

interface HeaderProps {
  onNewAnalysis: () => void
  showNew: boolean
}

/** top's first line: the name in reverse video, what it does, then the keys. */
export function Header({ onNewAnalysis, showNew }: HeaderProps) {
  const theme = useTheme((s) => s.theme)
  const toggle = useTheme((s) => s.toggle)
  return (
    <header className="sticky top-0 z-30 border-b bg-background">
      <div className="mx-auto flex max-w-4xl items-center gap-[1.5ch] px-4 py-3 sm:px-6">
        <span className="bg-foreground px-[1ch] font-bold tracking-wider text-background">
          {APP_NAME}
        </span>
        <span className="hidden min-w-0 truncate text-muted-foreground sm:inline">
          schematic debugging and pre-fab review for KiCad boards
        </span>
        <div className="ml-auto flex items-center gap-[1ch]">
          {showNew && (
            <Button variant="outline" size="sm" onClick={onNewAnalysis}>
              New analysis
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={toggle}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-label="Toggle theme"
          >
            {theme === "dark" ? "Light" : "Dark"}
          </Button>
        </div>
      </div>
    </header>
  )
}

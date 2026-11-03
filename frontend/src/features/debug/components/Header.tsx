import { Moon, RotateCcw, Sun } from "lucide-react"

import { useTheme } from "@/shared/lib/theme"
import { Button } from "@/shared/ui/button"

// index.html's <title> carries PROJECT_NAME (vite.config.ts), so the name stays config.
const APP_NAME = document.title

interface HeaderProps {
  onNewAnalysis: () => void
  showNew: boolean
}

export function Header({ onNewAnalysis, showNew }: HeaderProps) {
  const theme = useTheme((s) => s.theme)
  const toggle = useTheme((s) => s.toggle)
  return (
    <header className="sticky top-0 z-30 border-b bg-card elevation-1">
      <div className="mx-auto flex h-16 max-w-4xl items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#2b5680] bg-[#193a5c] shadow-sm">
            <svg
              viewBox="0 0 32 32"
              className="h-5 w-5 fill-none"
              strokeWidth="2.2"
              strokeLinecap="round"
              aria-hidden
            >
              <path d="M6 8 L6 24 M6 16 L18 16 M18 8 L18 24 M18 16 L21 16" stroke="#4fc3f7" />
              <circle cx="24" cy="16" r="3" fill="#00d084" stroke="#00d084" />
            </svg>
          </div>
          <div>
            <span className="font-mono text-xl font-bold tracking-tight">{APP_NAME}</span>
            <p className="hidden text-xs text-muted-foreground sm:block">
              Schematic debugger for boards on the bench
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            onClick={toggle}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-label="Toggle theme"
          >
            {theme === "dark" ? <Sun className="text-amber-300" /> : <Moon />}
          </Button>
          {showNew && (
            <Button variant="outline" size="sm" onClick={onNewAnalysis}>
              <RotateCcw />
              New analysis
            </Button>
          )}
        </div>
      </div>
    </header>
  )
}

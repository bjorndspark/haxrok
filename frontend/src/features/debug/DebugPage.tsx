import { Header } from "./components/Header"
import { InputView } from "./components/InputView"
import { SessionView } from "./components/SessionView"
import { useDebugStore } from "./store"
import { useDebugActions } from "./useDebug"

export function DebugPage() {
  const view = useDebugStore((s) => s.view)
  const hasRuns = useDebugStore((s) => s.runs.length > 0)
  const reset = useDebugStore((s) => s.reset)
  const actions = useDebugActions()

  return (
    <div className="flex min-h-screen flex-col">
      <Header onNewAnalysis={reset} showNew={hasRuns} />
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {view === "input" ? <InputView actions={actions} /> : <SessionView actions={actions} />}
      </main>
      <footer className="mt-auto border-t bg-card py-4 text-xs text-muted-foreground">
        <div className="mx-auto flex max-w-4xl flex-col items-center justify-between gap-2 px-4 sm:flex-row sm:px-6">
          <span>Schematic causes of bench symptoms, for KiCad projects.</span>
          <span>Every claim is labelled with what it rests on. Check before you rework.</span>
        </div>
      </footer>
    </div>
  )
}

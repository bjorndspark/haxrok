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
    </div>
  )
}

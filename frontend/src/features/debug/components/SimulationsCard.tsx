import { Activity, LoaderCircle, Send } from "lucide-react"
import { useState } from "react"

import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"

import type { Run, Simulation } from "../types"
import { useSimulationChat } from "../useDebug"
import { SimulationPlot } from "./SimulationPlot"

const SUGGESTIONS = ["What does this show?", "Is there enough margin?", "What should I check?"]

function SimulationChat({ runNumber, sim }: { runNumber: number; sim: Simulation }) {
  const chat = useSimulationChat(runNumber, sim)
  const [question, setQuestion] = useState("")
  const busy = chat.pending !== null
  const send = (q: string) => {
    chat.ask(q)
    setQuestion("")
  }
  return (
    <div className="space-y-2 border-t pt-3 text-xs">
      {chat.turns.map((t, i) => (
        <div key={i} className="space-y-1">
          <p className="font-semibold">{t.question}</p>
          <p className="leading-relaxed whitespace-pre-line text-foreground/90">{t.answer}</p>
        </div>
      ))}
      {chat.pending !== null && (
        <div className="space-y-1">
          <p className="font-semibold">{chat.pending}</p>
          <p className="flex items-center gap-1.5 text-muted-foreground">
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            Reading the waveform…
          </p>
        </div>
      )}
      {chat.error && <p className="text-rose-700 dark:text-rose-400">{chat.error}</p>}
      {chat.turns.length === 0 && !busy && (
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => send(s)}
              className="rounded-full border bg-background px-2.5 py-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          send(question)
        }}
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about this simulation"
          maxLength={1000}
          className="min-w-0 flex-1 rounded-md border bg-card px-3 py-1.5 text-sm focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none"
        />
        <Button type="submit" size="sm" disabled={busy || !question.trim()}>
          <Send />
          Ask
        </Button>
      </form>
    </div>
  )
}

/** Every ngspice simulation of a run, each with its own questions. Shown
 *  while the model works (they arrive first) and on the finished run. */
export function SimulationsCard({ run }: { run: Run }) {
  if (run.simulations.length === 0) return null
  return (
    <Card className="space-y-4 p-6 sm:p-7">
      <div>
        <h3 className="flex items-center gap-2 text-base font-bold sm:text-lg">
          <Activity className="h-5 w-5 text-blue-600 dark:text-sky-400" />
          Simulations ({run.simulations.length})
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          ngspice runs on circuits from your netlist, in parallel with the review. Ask about any of
          them.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {run.simulations.map((sim) => (
          <div key={sim.id} className="min-w-0 space-y-3 rounded-lg border bg-background p-4">
            <p className="text-sm font-semibold">{sim.title}</p>
            <SimulationPlot sim={sim} />
            <details className="text-[11px] text-muted-foreground">
              <summary className="cursor-pointer">Netlist</summary>
              <pre className="mt-1.5 overflow-x-auto rounded-md border bg-muted/40 p-2 font-mono">
                {sim.deck}
              </pre>
            </details>
            <SimulationChat runNumber={run.number} sim={sim} />
          </div>
        ))}
      </div>
    </Card>
  )
}

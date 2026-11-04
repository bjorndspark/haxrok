import { useState } from "react"

import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"
import { Prompt } from "@/shared/ui/prompt"
import { Title } from "@/shared/ui/title"

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
          <p className="font-bold">
            <span className="text-muted-foreground">&gt; </span>
            {t.question}
          </p>
          <p className="leading-relaxed whitespace-pre-line">{t.answer}</p>
        </div>
      ))}
      {chat.pending !== null && (
        <div className="space-y-1">
          <p className="font-bold">
            <span className="text-muted-foreground">&gt; </span>
            {chat.pending}
          </p>
          <p className="text-muted-foreground">
            <span className="animate-pulse">●</span> reading the waveform…
          </p>
        </div>
      )}
      {chat.error && <p className="text-bad">{chat.error}</p>}
      {chat.turns.length === 0 && !busy && (
        <div className="flex flex-wrap gap-x-[2ch] gap-y-1">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => send(s)}
              className="text-info underline decoration-dotted underline-offset-2 hover:no-underline"
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="flex"
        onSubmit={(e) => {
          e.preventDefault()
          send(question)
        }}
      >
        <Prompt
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about this simulation"
          maxLength={1000}
          aria-label="Ask about this simulation"
        >
          <Button type="submit" size="sm" disabled={busy || !question.trim()}>
            Ask
          </Button>
        </Prompt>
      </form>
    </div>
  )
}

/** Every ngspice simulation of a run, each with its own questions. Shown
 *  while the model works (they arrive first) and on the finished run. */
export function SimulationsCard({ run }: { run: Run }) {
  if (run.simulations.length === 0) return null
  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <Title as="h3">Simulations ({run.simulations.length})</Title>
      <div className="space-y-4">
        {run.simulations.map((sim) => (
          <div key={sim.id} className="min-w-0 space-y-3 border p-3">
            <p className="text-sm font-bold">{sim.title}</p>
            <SimulationPlot sim={sim} />
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">ngspice netlist</summary>
              <pre className="mt-1.5 overflow-x-auto border-l-2 pl-[1ch]">{sim.deck}</pre>
            </details>
            <SimulationChat runNumber={run.number} sim={sim} />
          </div>
        ))}
      </div>
    </Card>
  )
}

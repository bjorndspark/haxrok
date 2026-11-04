import { Card } from "@/shared/ui/card"
import { Title } from "@/shared/ui/title"

import { useMessages } from "../messages"
import type { Run, Simulation } from "../types"
import { useSimulationChat } from "../useDebug"
import { ChatThread } from "./ChatThread"
import { SimulationPlot } from "./SimulationPlot"

function SimulationChat({ runNumber, sim }: { runNumber: number; sim: Simulation }) {
  const chat = useSimulationChat(runNumber, sim)
  return (
    <div className="border-t pt-3">
      <ChatThread chat={chat} text={useMessages().simulations} />
    </div>
  )
}

/** Every ngspice simulation of a run, each with its own questions. Shown
 *  while the model works (they arrive first) and on the finished run. */
export function SimulationsCard({ run }: { run: Run }) {
  const t = useMessages().simulations
  if (run.simulations.length === 0) return null
  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <Title as="h3">{t.title(run.simulations.length)}</Title>
      <div className="space-y-4">
        {run.simulations.map((sim) => (
          <div key={sim.id} className="min-w-0 space-y-3 border p-3">
            <p className="text-sm font-bold">{sim.title}</p>
            <SimulationPlot sim={sim} />
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">{t.netlist}</summary>
              <pre className="mt-1.5 overflow-x-auto border-l-2 pl-[1ch]">{sim.deck}</pre>
            </details>
            <SimulationChat runNumber={run.number} sim={sim} />
          </div>
        ))}
      </div>
    </Card>
  )
}

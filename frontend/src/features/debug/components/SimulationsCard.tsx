import { Badge } from "@/shared/ui/badge"
import { Button } from "@/shared/ui/button"
import { Card } from "@/shared/ui/card"
import { Title } from "@/shared/ui/title"

import { useMessages } from "../messages"
import { isVendorModel, pairVendorSims, peakRows, type SimPair } from "../present"
import type { Run, SimCircuit, Simulation, SimVerdict } from "../types"
import { formatElapsed, useElapsed, useSimulationChat, useWhatIf, type WhatIf } from "../useDebug"
import { ChatThread } from "./ChatThread"
import { SimulationPlot } from "./SimulationPlot"

const VERDICT_TONE = {
  flagged: "warn",
  passed: "ok",
  timed_out: "neutral",
  failed: "neutral",
  skipped: "neutral",
} as const satisfies Record<SimVerdict, "warn" | "ok" | "neutral">

function VerdictBadge({ verdict, className }: { verdict: SimVerdict; className?: string }) {
  const t = useMessages().simulations
  return (
    <Badge tone={VERDICT_TONE[verdict]} bracket className={className}>
      {t.verdict[verdict]}
    </Badge>
  )
}

function SimulationChat({ runNumber, sim }: { runNumber: number; sim: Simulation }) {
  const chat = useSimulationChat(runNumber, sim)
  return (
    <div className="border-t pt-3">
      <ChatThread chat={chat} text={useMessages().simulations} />
    </div>
  )
}

/** One what-if field: the part's ref (or the quantity) before an
 *  underlined value. */
function ValueField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (text: string) => void
}) {
  return (
    <label className="flex items-center gap-[1ch] text-xs">
      <span className="text-muted-foreground">{label}</span>
      <input
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
        }}
        maxLength={20}
        spellCheck={false}
        className="w-[9ch] border-b border-input bg-transparent py-0.5 font-mono text-sm caret-foreground focus:border-foreground focus:outline-none"
      />
    </label>
  )
}

/** The R and C fields under an RC plot: re-run ngspice with the user's
 *  values (the rail stays), or Reset to drop the what-if. */
function WhatIfFields({ w }: { w: WhatIf }) {
  const t = useMessages().simulations.whatIf
  return (
    <form
      className="space-y-1.5"
      onSubmit={(e) => {
        e.preventDefault()
        w.rerun()
      }}
    >
      <p className="text-xs text-muted-foreground">{t.hint}</p>
      <div className="flex flex-wrap items-center gap-x-[2ch] gap-y-2">
        <ValueField label={w.labels.r} value={w.r} onChange={w.setR} />
        <ValueField label={w.labels.c} value={w.c} onChange={w.setC} />
        <Button type="submit" size="sm" disabled={w.pending}>
          {t.rerun}
        </Button>
        {w.whatIf && (
          <Button type="button" variant="outline" size="sm" onClick={w.reset}>
            {t.reset}
          </Button>
        )}
        {w.whatIf?.verdict && (
          <span className="flex items-baseline gap-[1ch] text-xs">
            <span className="text-muted-foreground">{t.verdict}</span>
            <VerdictBadge verdict={w.whatIf.verdict} />
          </span>
        )}
      </div>
      {w.pending && (
        <p className="text-xs text-muted-foreground">
          <span className="animate-pulse">●</span> {t.rerunning}
        </p>
      )}
      {w.error && <p className="text-xs text-bad">{w.error}</p>}
    </form>
  )
}

/** An RC simulation's plot with its what-if fields; the what-if is drawn
 *  over the original. */
function RcSimulation({
  runNumber,
  sim,
  circuit,
}: {
  runNumber: number
  sim: Simulation
  circuit: SimCircuit
}) {
  const w = useWhatIf(runNumber, sim, circuit)
  return (
    <>
      <SimulationPlot sim={sim} whatIf={w.whatIf} />
      <WhatIfFields w={w} />
    </>
  )
}

/** The netlist that was run, folded away; absent when nothing ran. */
function Netlist({ sim, label }: { sim: Simulation; label?: string }) {
  const t = useMessages().simulations
  if (!sim.deck.trim()) return null
  return (
    <details className="text-xs text-muted-foreground">
      <summary className="cursor-pointer">
        {t.netlist}
        {label && ` · ${label}`}
      </summary>
      <pre className="mt-1.5 overflow-x-auto border-l-2 pl-[1ch]">{sim.deck}</pre>
    </details>
  )
}

/** Plan 003's RC simulations of a run, each with its own questions. Shown
 *  while the model works (they arrive first) and on the finished run. */
export function SimulationsCard({ run }: { run: Run }) {
  const t = useMessages().simulations
  const sims = run.simulations.filter((sim) => !isVendorModel(sim))
  if (sims.length === 0) return null
  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <Title as="h3">{t.title(sims.length)}</Title>
      <div className="space-y-4">
        {sims.map((sim) => (
          <div key={sim.id} className="min-w-0 space-y-3 border p-3">
            <p className="flex flex-wrap items-baseline gap-[1ch] text-sm">
              <span className="font-bold">{sim.title}</span>
              {sim.verdict && <VerdictBadge verdict={sim.verdict} className="ml-auto" />}
            </p>
            {sim.circuit ? (
              <RcSimulation runNumber={run.number} sim={sim} circuit={sim.circuit} />
            ) : (
              <SimulationPlot sim={sim} />
            )}
            <Netlist sim={sim} />
            <SimulationChat runNumber={run.number} sim={sim} />
          </div>
        ))}
      </div>
    </Card>
  )
}

/** "Still running · mm:ss" with a stop button, ticking from the run's start. */
function StillRunning({ run, onStop }: { run: Run; onStop: () => void }) {
  const t = useMessages().simulations
  const elapsed = useElapsed(run.startedAt, null)
  return (
    <div className="flex flex-wrap items-center gap-[1.5ch] text-xs">
      <span className="animate-pulse text-warn">●</span>
      <span>
        {t.running} · <span className="tabular-nums">{formatElapsed(elapsed)}</span>
      </span>
      <Button variant="outline" size="sm" className="ml-auto" onClick={onStop}>
        {t.stop}
      </Button>
    </div>
  )
}

/** The regulators simulated with their vendor models (plan 004). They
 *  arrive after the result; while the stream is still open the card says
 *  so and can stop it without failing the run. */
export function VendorSimulationsCard({ run, onStop }: { run: Run; onStop: () => void }) {
  const t = useMessages().simulations
  const sims = run.simulations.filter(isVendorModel)
  const running = run.streaming && run.finishedAt !== null
  if (sims.length === 0 && !running) return null
  return (
    <Card className="space-y-4 p-4 sm:p-5">
      <Title as="h3">{t.vendorTitle(sims.length)}</Title>
      {running && <StillRunning run={run} onStop={onStop} />}
      <div className="space-y-4">
        {pairVendorSims(sims).map((pair) => (
          <VendorSimulation key={pair.sim.id} runNumber={run.number} pair={pair} />
        ))}
      </div>
    </Card>
  )
}

/** Each ammeter's peak, as built and (when there is one) with the fix. */
function PeakTable({ pair }: { pair: SimPair }) {
  const t = useMessages().simulations.fix
  const rows = peakRows(pair.sim, pair.fixed)
  if (rows.length === 0) return null
  const finding = pair.fixed?.finding ?? ""
  return (
    <table className="text-xs">
      <thead className="text-muted-foreground">
        <tr>
          <th className="pr-[2ch] text-left font-normal">{t.peak}</th>
          <th className="pr-[2ch] text-right font-normal">{t.asBuilt}</th>
          {pair.fixed && <th className="text-right font-normal">{t.withFix(finding)}</th>}
        </tr>
      </thead>
      <tbody className="font-mono tabular-nums">
        {rows.map((r) => (
          <tr key={r.name}>
            <td className="pr-[2ch] font-sans">{r.label}</td>
            <td className="pr-[2ch] text-right text-info">{r.built ?? "—"}</td>
            {pair.fixed && <td className="text-right text-warn">{r.fixed ?? "—"}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** One regulator's vendor-model run; with a wiring fix, the with-fix run's
 *  output is drawn dashed over the as-built one and both verdicts show. */
function VendorSimulation({ runNumber, pair }: { runNumber: number; pair: SimPair }) {
  const t = useMessages().simulations
  const { sim, fixed } = pair
  const finding = fixed?.finding ?? ""
  return (
    <div className="min-w-0 space-y-3 border p-3">
      <p className="flex flex-wrap items-baseline gap-[1ch] text-sm">
        <span className="font-bold text-info">{sim.ref}</span>
        {sim.analysis && <span>· {t.analysis[sim.analysis]}</span>}
        <span className="ml-auto flex flex-wrap items-baseline gap-[1ch]">
          {fixed && <span className="text-xs text-muted-foreground">{t.fix.asBuilt}</span>}
          {sim.verdict && <VerdictBadge verdict={sim.verdict} />}
          {fixed?.verdict && (
            <>
              <span className="text-xs text-muted-foreground">{t.fix.withFix(finding)}</span>
              <VerdictBadge verdict={fixed.verdict} />
            </>
          )}
        </span>
      </p>
      {sim.t.length > 0 ? (
        <>
          <p className="text-sm font-bold">{sim.title}</p>
          <SimulationPlot
            sim={sim}
            whatIf={fixed && fixed.t.length > 0 ? fixed : null}
            legend={t.fix.legend(finding)}
          />
        </>
      ) : (
        <p className="text-sm leading-relaxed">{sim.text}</p>
      )}
      {fixed?.t.length === 0 && <p className="text-sm leading-relaxed">{fixed.text}</p>}
      <PeakTable pair={pair} />
      <Netlist sim={sim} label={fixed ? t.fix.asBuilt : undefined} />
      {fixed && <Netlist sim={fixed} label={t.fix.withFix(finding)} />}
      <SimulationChat runNumber={runNumber} sim={sim} />
    </div>
  )
}

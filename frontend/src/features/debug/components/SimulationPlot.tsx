import { useState } from "react"

import { nearestPoint, simulationLayout } from "../present"
import type { Simulation } from "../types"

/** ngspice's waveform for an RC node: a flagged I²C line rising past the
 *  IC's supply + 0.3 V, or an enable pin rising through its RC delay.
 *  Hover reads off a sample. */
export function SimulationPlot({ sim }: { sim: Simulation }) {
  const layout = simulationLayout(sim)
  const [hover, setHover] = useState<ReturnType<typeof nearestPoint>>(null)
  const { plot } = layout
  return (
    <figure className="space-y-1.5">
      <svg
        viewBox={`0 0 ${String(layout.width)} ${String(layout.height)}`}
        className="w-full max-w-md font-mono text-[10px]"
        role="img"
        aria-label={sim.text}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          const x = ((e.clientX - box.left) / box.width) * layout.width
          setHover(x >= plot.x0 && x <= plot.x1 ? nearestPoint(layout, x) : null)
        }}
        onMouseLeave={() => {
          setHover(null)
        }}
      >
        {layout.yTicks.map((t) => (
          <g key={t.label}>
            <line
              x1={plot.x0}
              x2={plot.x1}
              y1={t.y}
              y2={t.y}
              className="stroke-slate-200 dark:stroke-slate-800"
            />
            <text x={plot.x0 - 6} y={t.y + 3} textAnchor="end" className="fill-muted-foreground">
              {t.label}
            </text>
          </g>
        ))}
        {layout.xTicks.map((t) => (
          <text
            key={t.label}
            x={t.x}
            y={plot.y0 + 13}
            textAnchor="middle"
            className="fill-muted-foreground"
          >
            {t.label}
          </text>
        ))}
        <text x={plot.x1} y={plot.y0 + 25} textAnchor="end" className="fill-muted-foreground">
          {sim.t_unit}
        </text>
        <text x={plot.x0 - 6} y={plot.y1 - 12} textAnchor="end" className="fill-muted-foreground">
          V
        </text>
        {layout.levels.map((l) => (
          <g key={l.label}>
            <line
              x1={plot.x0}
              x2={plot.x1}
              y1={l.y}
              y2={l.y}
              strokeDasharray="4 3"
              className={
                l.danger
                  ? "stroke-rose-500 dark:stroke-rose-400"
                  : "stroke-slate-400 dark:stroke-slate-500"
              }
            />
            <text x={plot.x1 + 4} y={l.y + 3} className="fill-foreground">
              {l.label}
            </text>
          </g>
        ))}
        <path
          d={layout.path}
          fill="none"
          strokeWidth={2}
          strokeLinejoin="round"
          className="stroke-blue-600 dark:stroke-sky-400"
        />
        {hover && (
          <g>
            <line
              x1={hover.x}
              x2={hover.x}
              y1={plot.y1}
              y2={plot.y0}
              className="stroke-slate-400 dark:stroke-slate-500"
            />
            <circle
              cx={hover.x}
              cy={hover.y}
              r={4}
              strokeWidth={2}
              className="fill-blue-600 stroke-background dark:fill-sky-400"
            />
            <text
              x={hover.x > (plot.x0 + plot.x1) / 2 ? hover.x - 6 : hover.x + 6}
              y={plot.y1 - 10}
              textAnchor={hover.x > (plot.x0 + plot.x1) / 2 ? "end" : "start"}
              className="fill-foreground font-semibold"
            >
              {`${hover.v.toFixed(2)} V @ ${String(Number(hover.t.toPrecision(3)))} ${sim.t_unit}`}
            </text>
          </g>
        )}
      </svg>
      <figcaption className="text-[11px] leading-relaxed text-muted-foreground">
        {sim.text}
      </figcaption>
    </figure>
  )
}

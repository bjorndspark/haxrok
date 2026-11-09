import { useState } from "react"

import { useMessages } from "../messages"
import { hoverReadout, simulationLayout } from "../present"
import type { Simulation } from "../types"

/** ngspice's waveform for an RC node: a flagged I²C line rising past the
 *  IC's supply + 0.3 V, or an enable pin rising through its RC delay.
 *  A what-if re-run is drawn dashed over it. Hover reads off a sample. */
export function SimulationPlot({
  sim,
  whatIf = null,
}: {
  sim: Simulation
  whatIf?: Simulation | null
}) {
  const t = useMessages().simulations.whatIf
  const layout = simulationLayout(sim, whatIf)
  const [hover, setHover] = useState<ReturnType<typeof hoverReadout>>(null)
  const { plot } = layout
  return (
    <figure className="space-y-1.5">
      <svg
        viewBox={`0 0 ${String(layout.width)} ${String(layout.height)}`}
        className="w-full max-w-lg font-mono text-xs"
        role="img"
        aria-label={sim.text}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          const x = ((e.clientX - box.left) / box.width) * layout.width
          setHover(x >= plot.x0 && x <= plot.x1 ? hoverReadout(layout, x) : null)
        }}
        onMouseLeave={() => {
          setHover(null)
        }}
      >
        {layout.yTicks.map((t) => (
          <g key={t.label}>
            <line x1={plot.x0} x2={plot.x1} y1={t.y} y2={t.y} className="stroke-border" />
            <text x={plot.x0 - 6} y={t.y + 4} textAnchor="end" className="fill-muted-foreground">
              {t.label}
            </text>
          </g>
        ))}
        {layout.xTicks.map((t) => (
          <text
            key={t.label}
            x={t.x}
            y={plot.y0 + 16}
            textAnchor="middle"
            className="fill-muted-foreground"
          >
            {t.label}
          </text>
        ))}
        <text x={plot.x1} y={plot.y0 + 31} textAnchor="end" className="fill-muted-foreground">
          {layout.unit}
        </text>
        <text x={plot.x0 - 6} y={plot.y1 - 10} textAnchor="end" className="fill-muted-foreground">
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
              className={l.danger ? "stroke-bad" : "stroke-muted-foreground"}
            />
            <text x={plot.x1 + 4} y={l.y + 4} className={l.danger ? "fill-bad" : "fill-value"}>
              {l.label}
            </text>
          </g>
        ))}
        <path
          d={layout.path}
          fill="none"
          strokeWidth={2}
          strokeLinejoin="round"
          className="stroke-info"
        />
        {layout.whatIf && (
          <path
            d={layout.whatIf.path}
            fill="none"
            strokeWidth={2}
            strokeDasharray="6 4"
            strokeLinejoin="round"
            className="stroke-warn"
          />
        )}
        {hover && (
          <g>
            <line
              x1={hover.x}
              x2={hover.x}
              y1={plot.y1}
              y2={plot.y0}
              className="stroke-muted-foreground"
            />
            {hover.dots.map((d) => (
              <circle
                key={String(d.dashed)}
                cx={hover.x}
                cy={d.y}
                r={4}
                strokeWidth={2}
                className={`${d.dashed ? "fill-warn" : "fill-info"} stroke-background`}
              />
            ))}
            <text
              x={hover.x > (plot.x0 + plot.x1) / 2 ? hover.x - 6 : hover.x + 6}
              y={plot.y1 - 10}
              textAnchor={hover.x > (plot.x0 + plot.x1) / 2 ? "end" : "start"}
              className="fill-value font-bold"
            >
              {hover.label}
            </text>
          </g>
        )}
      </svg>
      <figcaption className="space-y-1 text-xs leading-relaxed text-muted-foreground">
        <p>{sim.text}</p>
        {whatIf && (
          <p>
            <span className="mr-[1ch] text-warn">╌ {t.legend}</span>
            {whatIf.text}
          </p>
        )}
      </figcaption>
    </figure>
  )
}

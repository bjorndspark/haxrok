import { cn } from "@/shared/lib/utils"

import { diagramLayout } from "../present"
import type { Diagram } from "../types"

/** The hypothesis's cited IC pins wired to their nets, drawn from the
 *  netlist by the backend. A pin the wiring check flagged is red. */
export function WiringDiagram({ diagram }: { diagram: Diagram }) {
  const layout = diagramLayout(diagram)
  return (
    <figure className="space-y-1.5">
      <svg
        viewBox={`0 0 ${String(layout.width)} ${String(layout.height)}`}
        className="w-full max-w-xl font-mono text-[11px]"
        role="img"
        aria-label={`${diagram.ic} pins and the nets they are on`}
      >
        <rect
          x={layout.box.x}
          y={layout.box.y}
          width={layout.box.width}
          height={layout.box.height}
          rx={4}
          className="fill-background stroke-slate-400 dark:stroke-slate-500"
        />
        <text
          x={layout.box.x + 8}
          y={layout.box.y + 15}
          className="fill-foreground text-[12px] font-bold"
        >
          {diagram.ic}
        </text>
        <text x={layout.box.x + 8} y={layout.box.y + 28} className="fill-muted-foreground">
          {diagram.value}
        </text>
        {layout.pins.map((p) => (
          <g
            key={p.pin}
            className={cn(
              p.flagged
                ? "fill-rose-700 stroke-rose-600 dark:fill-rose-300 dark:stroke-rose-400"
                : "fill-foreground stroke-slate-500 dark:stroke-slate-400",
            )}
          >
            <text
              x={layout.box.x + layout.box.width - 6}
              y={p.y + 4}
              textAnchor="end"
              stroke="none"
            >
              {p.label}
            </text>
            <line
              x1={layout.box.x + layout.box.width}
              y1={p.y}
              x2={layout.busX}
              y2={p.y}
              strokeWidth={p.flagged ? 2 : 1.5}
            />
            <circle cx={layout.busX} cy={p.y} r={2.5} stroke="none" />
          </g>
        ))}
        {layout.nets.map((n) => (
          <g key={n.name}>
            <line
              x1={layout.busX}
              y1={n.top}
              x2={layout.busX}
              y2={n.bottom}
              strokeWidth={2}
              className={
                n.flagged
                  ? "stroke-rose-600 dark:stroke-rose-400"
                  : "stroke-slate-500 dark:stroke-slate-400"
              }
            />
            <text x={layout.busX + 10} y={n.top + 4} className="fill-foreground font-bold">
              {n.label}
            </text>
            <text x={layout.busX + 10} y={n.top + 18} className="fill-muted-foreground">
              {n.members}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="text-[11px] text-muted-foreground">
        Drawn from your netlist. Red: a pin wired against its datasheet function.
      </figcaption>
    </figure>
  )
}

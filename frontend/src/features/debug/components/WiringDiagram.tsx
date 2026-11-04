import { cn } from "@/shared/lib/utils"

import { useMessages } from "../messages"
import { diagramLayout } from "../present"
import type { Diagram } from "../types"

/** The hypothesis's cited IC pins wired to their nets, drawn from the
 *  netlist by the backend: the IC in cyan, its value yellow, nets magenta. A
 *  pin the wiring check flagged is red. */
export function WiringDiagram({ diagram }: { diagram: Diagram }) {
  const m = useMessages()
  const layout = diagramLayout(diagram, m)
  return (
    <figure className="space-y-1.5">
      <svg
        viewBox={`0 0 ${String(layout.width)} ${String(layout.height)}`}
        className="w-full max-w-2xl font-mono text-xs"
        role="img"
        aria-label={m.diagram.aria(diagram.ic)}
      >
        <rect
          x={layout.box.x}
          y={layout.box.y}
          width={layout.box.width}
          height={layout.box.height}
          className="fill-background stroke-foreground"
        />
        <text x={layout.box.x + 8} y={layout.box.y + 17} className="fill-info text-sm font-bold">
          {diagram.ic}
        </text>
        <text x={layout.box.x + 8} y={layout.box.y + 33} className="fill-value">
          {diagram.value}
        </text>
        {layout.pins.map((p) => (
          <g
            key={p.pin}
            className={cn(
              p.flagged
                ? "fill-bad stroke-bad font-bold"
                : "fill-foreground stroke-muted-foreground",
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
              className={n.flagged ? "stroke-bad" : "stroke-muted-foreground"}
            />
            <text
              x={layout.busX + 10}
              y={n.top + 4}
              className={cn("font-bold", n.flagged ? "fill-bad" : "fill-net")}
            >
              {n.label}
            </text>
            <text x={layout.busX + 10} y={n.top + 20} className="fill-muted-foreground">
              {n.members}
            </text>
          </g>
        ))}
      </svg>
    </figure>
  )
}

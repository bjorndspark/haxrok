import * as React from "react"

import { cn } from "@/shared/lib/utils"

/** A panel: a square box ruled in the line colour, like a TUI pane. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("border bg-card text-card-foreground", className)} {...props} />
}

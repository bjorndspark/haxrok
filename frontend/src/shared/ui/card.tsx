import * as React from "react"

import { cn } from "@/shared/lib/utils"

/** Surface with the mock's Material elevation. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border bg-card text-card-foreground elevation-1", className)}
      {...props}
    />
  )
}

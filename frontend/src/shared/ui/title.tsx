import * as React from "react"

import { cn } from "@/shared/lib/utils"

/** A section heading as a reverse-video cyan bar, like htop's table header. */
export function Title({
  as: Tag = "h2",
  className,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement> & { as?: "h1" | "h2" | "h3" }) {
  return (
    <Tag
      className={cn(
        "inline-flex items-center gap-[1ch] bg-info px-[1ch] text-sm font-bold tracking-wider text-on-state uppercase",
        className,
      )}
      {...props}
    />
  )
}

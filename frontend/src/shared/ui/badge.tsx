import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"

import { cn } from "@/shared/lib/utils"

/** A state label in a state colour. `bracket` draws it as `[label]`, kept for
 *  evidence labels; `alert` and `solid` are reverse video, kept for the things
 *  that need attention (a fault, the top rank). */
const badgeVariants = cva("inline-flex items-center font-mono text-xs whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "text-muted-foreground",
      info: "text-info",
      ok: "text-ok",
      warn: "text-warn",
      bad: "font-bold text-bad",
      alert: "bg-bad px-[1ch] font-bold text-on-state",
      solid: "bg-foreground px-[1ch] font-bold text-background",
    },
    bracket: {
      true: "before:content-['['] after:content-[']']",
      false: "",
    },
  },
  defaultVariants: { tone: "neutral", bracket: false },
})

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, bracket, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone, bracket }), className)} {...props} />
}

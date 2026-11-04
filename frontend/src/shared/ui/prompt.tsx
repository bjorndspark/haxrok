import * as React from "react"

import { cn } from "@/shared/lib/utils"

/** A one-line input drawn as a shell prompt: a `>` caret on an underline that
 *  darkens on focus. Put the submit button in `children`. */
export const Prompt = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement> & { children?: React.ReactNode }
>(({ className, children, ...props }, ref) => (
  <div className="flex min-w-0 flex-1 items-center gap-[1ch] border-b border-input focus-within:border-foreground">
    <span className="text-muted-foreground" aria-hidden>
      &gt;
    </span>
    <input
      ref={ref}
      className={cn(
        "min-w-0 flex-1 bg-transparent py-1.5 font-mono text-sm caret-foreground placeholder:text-muted-foreground focus:outline-none",
        className,
      )}
      {...props}
    />
    {children}
  </div>
))
Prompt.displayName = "Prompt"

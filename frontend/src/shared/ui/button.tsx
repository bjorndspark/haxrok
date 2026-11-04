import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"

import { cn } from "@/shared/lib/utils"

/** `default` is a reverse-video key, `outline` a ruled one; both square. */
const buttonVariants = cva(
  "inline-flex cursor-pointer items-center justify-center gap-[1ch] font-mono whitespace-nowrap focus-visible:outline-1 focus-visible:outline-offset-2 focus-visible:outline-foreground disabled:pointer-events-none disabled:opacity-40 [&_svg]:pointer-events-none [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-foreground text-background hover:outline-1 hover:outline-offset-2 hover:outline-foreground",
        outline: "border border-foreground bg-transparent hover:bg-accent",
      },
      size: {
        default: "h-8 px-[1.5ch] text-sm",
        sm: "h-7 px-[1ch] text-xs",
        lg: "h-9 px-[2ch] text-sm font-bold",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    )
  },
)
Button.displayName = "Button"

export { Button, buttonVariants }

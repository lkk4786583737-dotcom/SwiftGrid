"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold smooth focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        brand:
          "grad-brand text-white shadow-btn hover:-translate-y-0.5 hover:shadow-apple-hover",
        outline:
          "border-2 border-brand text-brand bg-white hover:bg-brand hover:text-white",
        ghost: "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
        soft: "bg-brand-light/10 text-brand hover:bg-brand-light/20",
        amber:
          "bg-amber-500 text-white hover:bg-amber-600 shadow-amber",
        amberSoft:
          "bg-white border border-amber-200 text-amber-700 hover:bg-amber-100",
        danger:
          "bg-white border border-red-200 text-red-500 hover:bg-red-50",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-8 px-3 text-xs",
        lg: "h-12 px-6",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "brand", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };

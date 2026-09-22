"use client";

import { cn } from "@/lib/utils";

export function ComposeBadge({
  children,
  variant = "green",
  dot = false,
  className,
}: {
  children: React.ReactNode;
  variant?: "green" | "amber" | "neutral";
  dot?: boolean;
  className?: string;
}) {
  const styles = {
    green: "bg-brand/10 text-brand",
    amber: "bg-amber-50 text-amber-700",
    neutral: "bg-slate-100 text-slate-500",
  };
  return (
    <span
      className={cn(
        "inline-flex min-h-[26px] items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-bold whitespace-nowrap",
        styles[variant],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

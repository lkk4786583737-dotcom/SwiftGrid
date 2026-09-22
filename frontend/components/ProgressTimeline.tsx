"use client";

import { Check, Loader2, TriangleAlert } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export type StepStatus = "done" | "active" | "pending" | "warn";
export type Step = { label: string; sub?: string; status: StepStatus };

export function ProgressTimeline({
  title = "AI 正在推导你的处理逻辑",
  hint = "关键节点实时反馈，可随时在左侧按钮中断",
  steps,
}: {
  title?: string;
  hint?: string;
  steps: Step[];
}) {
  return (
    <div className="flex flex-col">
      <div className="mb-6 flex items-center gap-2">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-light opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
        </span>
        <div className="text-sm font-semibold text-brand-dark">{title}</div>
        <span className="text-xs text-slate-400">· {hint}</span>
      </div>

      <div className="relative pl-2">
        {steps.map((s, i) => {
          const last = i === steps.length - 1;
          return (
            <motion.div
              key={i}
              className="flex gap-4"
              initial={{ opacity: 0, y: 14, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ delay: i * 0.13, ease: [0.16, 1, 0.3, 1] }}
            >
              <div className="flex flex-col items-center">
                <Node status={s.status} />
                {!last && (
                  <div
                    className={cn(
                      "my-1 w-0.5 flex-1 rounded-full",
                      s.status === "done"
                        ? "bg-brand/30"
                        : s.status === "warn"
                          ? "bg-amber-300"
                          : "flowline",
                    )}
                  />
                )}
              </div>
              <div className={cn(last ? "" : "pb-6")}>
                <div
                  className={cn(
                    "text-sm font-medium",
                    s.status === "active"
                      ? "font-semibold text-brand"
                      : s.status === "warn"
                        ? "font-semibold text-amber-700"
                        : "text-brand-dark",
                  )}
                >
                  {s.label}
                </div>
                {s.sub && (
                  <div className="mt-0.5 text-xs text-slate-400">{s.sub}</div>
                )}
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

function Node({ status }: { status: StepStatus }) {
  if (status === "done") {
    return (
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand text-xs text-white">
        <Check className="h-3.5 w-3.5" />
      </div>
    );
  }
  if (status === "warn") {
    return (
      <div className="flex h-7 w-7 shrink-0 animate-breathe-amber items-center justify-center rounded-full bg-amber-400 text-xs text-amber-950">
        <TriangleAlert className="h-3.5 w-3.5" />
      </div>
    );
  }
  if (status === "active") {
    return (
      <div className="flex h-7 w-7 shrink-0 animate-breathe items-center justify-center rounded-full border-2 border-brand bg-white text-xs text-brand">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      </div>
    );
  }
  return (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 border-slate-200 bg-white text-xs text-slate-300">
      <span className="h-1.5 w-1.5 rounded-full bg-slate-300" />
    </div>
  );
}

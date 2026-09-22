"use client";

import { Bolt, CircleCheck, Hand, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export type ActionState = "idle" | "running" | "waiting" | "done";

export function ActionButton({
  state,
  onClick,
  idleLabel = "开始提取转换逻辑",
  runningLabel = "AI 推导中…",
  doneLabel = "建模完成 · 保存逻辑",
  disabled,
}: {
  state: ActionState;
  onClick: () => void;
  idleLabel?: string;
  runningLabel?: string;
  doneLabel?: string;
  disabled?: boolean;
}) {
  const base =
    "flex h-12 w-full items-center justify-center gap-2 rounded-xl font-semibold smooth";

  if (state === "waiting") {
    return (
      <button
        className={cn(
          base,
          "animate-breathe-amber cursor-default bg-amber-400 text-amber-950 shadow-amber",
        )}
        disabled
      >
        <Hand className="h-4 w-4" /> 等待你的确认…
      </button>
    );
  }

  if (state === "running") {
    return (
      <button
        onClick={onClick}
        className={cn(base, "sweep grad-brand text-white shadow-btn")}
      >
        <Loader2 className="h-4 w-4 animate-spin" /> {runningLabel}
        <span className="font-medium opacity-80">点击中断</span>
      </button>
    );
  }

  if (state === "done") {
    return (
      <button
        onClick={onClick}
        disabled={disabled}
        className={cn(
          base,
          "shadow-btn",
          disabled
            ? "cursor-default bg-brand/85 text-white"
            : "grad-brand text-white hover:-translate-y-0.5 hover:shadow-apple-hover",
        )}
      >
        <CircleCheck className="h-4 w-4" /> {doneLabel}
      </button>
    );
  }

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        base,
        "grad-brand text-white shadow-btn hover:-translate-y-0.5 hover:shadow-apple-hover disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0",
      )}
    >
      <Bolt className="h-4 w-4" /> {idleLabel}
    </button>
  );
}

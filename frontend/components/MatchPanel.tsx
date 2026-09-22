"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { BookMarked, Bolt, ListChecks, WandSparkles } from "lucide-react";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { MatchResp } from "@/lib/types";
import { pct } from "@/lib/utils";
import { cn } from "@/lib/utils";

export function MatchPanel({
  match,
  running,
  onRun,
}: {
  match: MatchResp;
  running: boolean;
  onRun: (logicId: string) => void;
}) {
  // 唯一强匹配：自动，免确认（功能 §6）
  if (match.auto && match.best) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-2xl bg-white p-6 shadow-apple"
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand text-white">
            <WandSparkles className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-bold text-brand-dark">
              已自动匹配到唯一逻辑，直接处理
            </div>
            <div className="mt-0.5 text-xs text-slate-400">
              结构高度吻合（{pct(match.best.score)}%），无需确认，已进入执行
            </div>
          </div>
          <span className="ml-auto rounded-full bg-brand-light/10 px-3 py-1 text-xs font-semibold text-brand">
            {pct(match.best.score)}% 匹配
          </span>
        </div>
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 p-4">
          <BookMarked className="h-4 w-4 text-brand" />
          <span className="text-sm font-medium text-slate-700">
            {match.best.name}
          </span>
        </div>
        <button
          onClick={() => onRun(match.best!.id)}
          disabled={running}
          className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-xl grad-brand font-semibold text-white shadow-btn smooth hover:-translate-y-0.5 disabled:opacity-60"
        >
          <Bolt className="h-4 w-4" />
          {running ? "正在用此逻辑处理…" : "立即处理"}
        </button>
      </motion.div>
    );
  }

  // 多候选：列相似度条，用户选
  return (
    <MultiSelect match={match} running={running} onRun={onRun} />
  );
}

function MultiSelect({
  match,
  running,
  onRun,
}: {
  match: MatchResp;
  running: boolean;
  onRun: (logicId: string) => void;
}) {
  const [selected, setSelected] = React.useState(
    match.candidates[0]?.id ?? "",
  );

  if (match.candidates.length === 0) {
    return (
      <div className="rounded-2xl bg-white p-6 text-center text-sm text-slate-400 shadow-apple">
        没有匹配到可用逻辑。请先到「逻辑建模」创建一个处理逻辑。
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-white p-6 shadow-apple">
      <div className="mb-1 flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-amber-100 text-amber-500">
          <ListChecks className="h-4 w-4" />
        </div>
        <div>
          <div className="text-sm font-bold text-brand-dark">
            匹配到多个相近逻辑，请选一个
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            结构相似度接近，SwiftGrid 不替你猜，由你来定
          </div>
        </div>
      </div>

      <RadioGroup
        value={selected}
        onValueChange={setSelected}
        className="mt-4 gap-3"
      >
        {match.candidates.map((c, i) => {
          const on = selected === c.id;
          return (
            <motion.label
              key={c.id}
              htmlFor={c.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.08 }}
              className={cn(
                "block cursor-pointer rounded-xl p-4 smooth",
                on
                  ? "border-2 border-brand bg-brand-light/5"
                  : "border border-slate-200 hover:border-brand/40",
              )}
            >
              <div className="flex items-center gap-3">
                <RadioGroupItem value={c.id} id={c.id} />
                <span
                  className={cn(
                    "text-sm",
                    on ? "font-semibold text-brand-dark" : "font-medium text-slate-700",
                  )}
                >
                  {c.name}
                </span>
                <span
                  className={cn(
                    "ml-auto text-sm font-bold",
                    on ? "text-brand" : "text-slate-400",
                  )}
                >
                  {pct(c.score)}%
                </span>
              </div>
              <div className="ml-7 mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                <motion.div
                  className={cn("h-full rounded-full", on ? "grad-brand" : "bg-slate-300")}
                  initial={{ width: 0 }}
                  animate={{ width: `${pct(c.score)}%` }}
                  transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
                />
              </div>
            </motion.label>
          );
        })}
      </RadioGroup>

      <button
        onClick={() => onRun(selected)}
        disabled={running || !selected}
        className="mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-xl grad-brand font-semibold text-white shadow-btn smooth hover:-translate-y-0.5 disabled:opacity-60"
      >
        <Bolt className="h-4 w-4" />
        {running ? "处理中…" : "用选中逻辑处理新表"}
      </button>
    </div>
  );
}

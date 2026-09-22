"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Lightbulb, Loader2, RotateCw, Undo2 } from "lucide-react";
import { Textarea } from "@/components/ui/input";
import type { ClarifyEvent } from "@/lib/types";

export function HitlCard({
  data,
  submitting,
  onRetry,
  onAbort,
}: {
  data: ClarifyEvent;
  submitting: boolean;
  onRetry: (clarification: string) => void;
  /** 改进项 1：放弃本次学习的出口 */
  onAbort: () => void;
}) {
  const [text, setText] = React.useState(data.suggested_clarification ?? "");
  const [editing, setEditing] = React.useState(false);

  React.useEffect(() => {
    setText(data.suggested_clarification ?? "");
  }, [data]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 18, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 320, damping: 18 }}
      className="mt-2 rounded-2xl border border-amber-200 bg-amber-50 p-5"
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 text-lg text-amber-500">
          <Lightbulb className="h-5 w-5" />
        </div>
        <div className="flex-1">
          {/* 改进项 7：安抚式标题 + 进度感（而非倒计时压迫感） */}
          <h3 className="mb-1 flex flex-wrap items-center gap-2 text-sm font-bold text-amber-900">
            差一点点就学会了！AI 想和你确认一件事
            <span className="flex items-center gap-1">
              {Array.from({ length: data.max }).map((_, i) => (
                <span
                  key={i}
                  className={
                    i < data.round
                      ? "h-1.5 w-4 rounded-full bg-amber-500"
                      : "h-1.5 w-4 rounded-full bg-amber-200"
                  }
                />
              ))}
              <span className="ml-1 text-[10px] font-semibold text-amber-700">
                第 {data.round} 次确认
              </span>
            </span>
          </h3>
          <p className="mb-3 text-xs leading-relaxed text-amber-800/90">
            {data.diagnosis}
          </p>

          <label className="text-[11px] font-semibold text-amber-700">
            把这句话告诉 AI 就行（已替你写好，可修改）：
          </label>
          <Textarea
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setEditing(true);
            }}
            className="mt-1 h-16 resize-none border-amber-200 bg-white focus:ring-amber-300"
          />

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => onRetry(text)}
              disabled={submitting || !text.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-2 text-xs font-semibold text-white shadow-sm smooth hover:bg-amber-600 disabled:opacity-50"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> 重新推导中…
                </>
              ) : (
                <>
                  <RotateCw className="h-3.5 w-3.5" /> 按这条确认并继续
                </>
              )}
            </button>
            {!editing && (
              <button
                onClick={() => setEditing(true)}
                className="rounded-lg border border-amber-200 bg-white px-4 py-2 text-xs font-semibold text-amber-700 smooth hover:bg-amber-100"
              >
                我自己再补充
              </button>
            )}
            <button
              onClick={onAbort}
              disabled={submitting}
              className="ml-auto flex items-center gap-1 px-2 py-2 text-[11px] font-medium text-amber-700/60 smooth hover:text-amber-800 disabled:opacity-50"
            >
              <Undo2 className="h-3 w-3" /> 放弃本次学习，重新上传
            </button>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

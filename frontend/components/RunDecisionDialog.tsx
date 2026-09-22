"use client";

import * as React from "react";
import { AlertTriangle, Check, Loader2, ShieldCheck } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { RunDecisionEvent } from "@/lib/types";

const LABELS: Record<string, string> = {
  source: "位置",
  column: "字段",
  new_values: "新增值",
  unmatched_values: "未匹配编号",
  unmatched_count: "未匹配数量",
  affected_rows: "影响行数",
  numeric_impact: "数值影响",
  missing_columns: "缺少字段",
  possible_alternatives: "可用替代字段",
  extra_columns: "新增字段",
  mapping_source: "映射表",
  mapping_key: "映射键",
  error: "执行信息",
  failures: "校验结果",
};

function valueText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join("、");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${valueText(item)}`)
      .join("；");
  }
  return String(value ?? "-");
}

export function RunDecisionDialog({
  data,
  submitting,
  onChoose,
}: {
  data: RunDecisionEvent;
  submitting: boolean;
  onChoose: (action: string) => void;
}) {
  const issue = data.active_issue;
  const evidence = Object.entries(issue.evidence ?? {}).filter(
    ([key]) => key !== "result_preview" && key !== "original_logic_unchanged",
  );
  return (
    <Dialog open onOpenChange={() => {}}>
      <DialogContent className="max-h-[82vh] max-w-xl overflow-y-auto rounded-lg [&>button]:hidden">
        <DialogHeader>
          <div className="mb-1 flex h-9 w-9 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
            {data.phase === "candidate_confirmation" ? (
              <ShieldCheck className="h-5 w-5" />
            ) : (
              <AlertTriangle className="h-5 w-5" />
            )}
          </div>
          <DialogTitle className="text-base">{data.title}</DialogTitle>
          <DialogDescription className="leading-6">{data.message}</DialogDescription>
        </DialogHeader>

        {evidence.length > 0 && (
          <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-slate-50/60 px-4">
            {evidence.map(([key, value]) => (
              <div key={key} className="grid grid-cols-[7rem_1fr] gap-3 py-2.5 text-xs">
                <span className="font-medium text-slate-500">{LABELS[key] ?? key}</span>
                <span className="break-words text-slate-700">{valueText(value)}</span>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {issue.options.map((option, index) => (
            <button
              key={option.id}
              type="button"
              disabled={submitting}
              onClick={() => onChoose(option.id)}
              className={
                index === 0
                  ? "inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-white smooth hover:bg-brand-dark disabled:opacity-50"
                  : "inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-medium text-slate-600 smooth hover:bg-slate-50 disabled:opacity-50"
              }
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              {option.label}
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

"use client";

import { FileSpreadsheet } from "lucide-react";
import type { DatasetSummary } from "./presentation-types";
import { cn } from "@/lib/utils";

function ScopeBadge({
  label,
  variant,
}: {
  label: string;
  variant: DatasetSummary["scopeVariant"];
}) {
  const styles = {
    sample: "bg-brand/10 text-brand",
    full: "bg-brand/10 text-brand",
    plan: "bg-amber-50 text-amber-700",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-bold",
        styles[variant],
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

function MetricCell({
  value,
  label,
}: {
  value: string | number;
  label: string;
}) {
  const display =
    typeof value === "number" ? value.toLocaleString("zh-CN") : value;
  return (
    <div className="border-r border-slate-100 px-4 py-2.5 last:border-r-0">
      <div className="text-[15px] font-bold text-brand-dark">{display}</div>
      <div className="mt-0.5 text-[10px] text-slate-400">{label}</div>
    </div>
  );
}

export function DatasetSummaryBar({
  dataset,
  mode = "default",
}: {
  dataset: DatasetSummary;
  /** clarify 模式用方案指标替代文件/行数 */
  mode?: "default" | "plan" | "result";
}) {
  return (
    <div className="grid min-h-[68px] grid-cols-[minmax(220px,1.6fr)_repeat(4,minmax(90px,0.55fr))_minmax(180px,1fr)] border-b border-slate-100 bg-slate-50/60">
      <div className="flex items-center gap-2.5 border-r border-slate-100 px-4 py-2.5">
        <div className="flex h-[38px] w-[34px] shrink-0 items-center justify-center rounded-md border border-brand/20 bg-brand/10 text-[10px] font-extrabold text-brand">
          {mode === "result" ? (
            "结果"
          ) : mode === "plan" ? (
            "方案"
          ) : (
            <FileSpreadsheet className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-bold text-brand-dark">
            {dataset.title}
          </div>
          <div className="truncate text-[11px] text-slate-400">
            {dataset.subtitle}
          </div>
        </div>
      </div>

      {mode === "plan" ? (
        <>
          <MetricCell value={dataset.fileCount} label="目标工作表" />
          <MetricCell value={dataset.sheetCount} label="核心指标" />
          <MetricCell value={dataset.plannedRows} label="已确认口径" />
          <MetricCell value={dataset.fieldCount} label="待确认问题" />
        </>
      ) : mode === "result" ? (
        <>
          <MetricCell value={dataset.fileCount} label="源文件" />
          <MetricCell value={dataset.sheetCount} label="源工作表" />
          <MetricCell value={dataset.plannedRows} label="计划处理行" />
          <MetricCell
            value={dataset.fieldCount}
            label="实际读取行"
          />
        </>
      ) : (
        <>
          <MetricCell value={dataset.fileCount} label="文件" />
          <MetricCell value={dataset.sheetCount} label="工作表" />
          <MetricCell value={dataset.plannedRows} label="计划处理行" />
          <MetricCell value={dataset.fieldCount} label="字段" />
        </>
      )}

      <div className="flex items-center px-4 py-2.5">
        <ScopeBadge label={dataset.scopeLabel} variant={dataset.scopeVariant} />
      </div>
    </div>
  );
}

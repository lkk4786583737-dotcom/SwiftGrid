"use client";

import { CircleCheck } from "lucide-react";
import type { Sheet } from "@/lib/types";
import { DataTable } from "./DataTable";

export function PreviewPanel({ sheets }: { sheets: Sheet[] }) {
  if (!sheets || sheets.length === 0) return null;
  const sheet = sheets[0];
  return (
    <div className="overflow-hidden rounded-xl border border-brand-light/30 bg-brand-light/5">
      <div className="flex items-center gap-2 px-3 py-2 text-xs font-medium text-brand-dark">
        <CircleCheck className="h-4 w-4 text-brand" />
        已识别 {sheet.name} · {sheet.total_rows} 行 × {sheet.columns.length} 列
        <span className="ml-auto truncate pl-2 font-mono text-[11px] text-slate-400">
          {sheet.columns.join("·")}
        </span>
      </div>
    </div>
  );
}

/** 完整可滚动预览（结果区用） */
export function FullPreview({ sheet }: { sheet: Sheet }) {
  return <DataTable sheet={sheet} />;
}

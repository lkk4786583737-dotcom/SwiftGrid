"use client";

import type { Sheet } from "@/lib/types";
import { cn } from "@/lib/utils";

export const RESULT_TABLE_VIEWPORT_CLASS =
  "h-[420px] min-h-0 overflow-auto overscroll-contain rounded-xl border border-slate-200 text-xs";
export const COMPARISON_TABLE_VIEWPORT_CLASS =
  "h-[360px] min-h-0 overflow-auto overscroll-contain";

function fmt(v: string | number | boolean | null): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString("zh-CN") : String(v);
  }
  return String(v);
}

export function DataTable({
  sheet,
  highlightFrom = 0,
}: {
  sheet: Sheet;
  /** 从第几列起用品牌色高亮（透视结果的数值列） */
  highlightFrom?: number;
}) {
  const cols = sheet.columns;
  return (
    <div className={RESULT_TABLE_VIEWPORT_CLASS}>
      <table className="w-max min-w-full border-collapse">
        <thead className="sticky top-0 z-10">
          <tr className="bg-slate-50 text-left font-semibold text-slate-500">
            {cols.map((c, i) => (
              <th
                key={i}
                className={cn(
                  "whitespace-nowrap border-b border-r border-slate-200 p-2.5 last:border-r-0",
                  highlightFrom > 0 &&
                    i >= highlightFrom &&
                    "bg-brand-light/5 text-brand",
                )}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sheet.preview.map((row, r) => (
            <tr
              key={r}
              className="border-b border-slate-100 text-slate-700 last:border-b-0"
            >
              {cols.map((_, c) => (
                <td
                  key={c}
                  className={cn(
                    "whitespace-nowrap border-r border-slate-100 p-2.5 last:border-r-0",
                    highlightFrom > 0 && c >= highlightFrom && "bg-brand-light/5",
                  )}
                >
                  {fmt(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {sheet.total_rows > sheet.preview.length && (
        <div className="sticky bottom-0 border-t border-slate-100 bg-slate-50/95 p-2 text-center text-[11px] text-slate-400 backdrop-blur-sm">
          仅预览前 {sheet.preview.length} 行 · 共 {sheet.total_rows} 行
        </div>
      )}
    </div>
  );
}

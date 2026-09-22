"use client";

import * as React from "react";
import { FileSpreadsheet } from "lucide-react";
import type { ParseResp, Sheet } from "@/lib/types";
import { cn } from "@/lib/utils";

export const PREVIEW_HEIGHT_SOURCE = 240;
export const PREVIEW_HEIGHT_TARGET = 200;
export const PREVIEW_HEIGHT_DIALOG = "min(58vh, 560px)";

function fmt(v: string | number | boolean | null): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString("zh-CN") : String(v);
  }
  return String(v);
}

export function ParsedTablePreview({
  resp,
  previewHeight = PREVIEW_HEIGHT_SOURCE,
  onClear,
}: {
  resp: ParseResp;
  /** 预览区固定高度（px），内容在框内横纵滚动 */
  previewHeight?: number | string;
  onClear?: () => void;
}) {
  const [sheetIdx, setSheetIdx] = React.useState(0);
  const sheet = resp.sheets[sheetIdx] ?? resp.sheets[0];

  React.useEffect(() => {
    setSheetIdx(0);
  }, [resp.handle]);

  if (!sheet) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-400">
        已识别表格，但没有可预览的数据。
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-brand-light/30 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 bg-brand-light/5 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <FileSpreadsheet className="h-4 w-4 shrink-0 text-brand" />
          <div className="min-w-0">
            <div className="truncate text-xs font-semibold text-brand-dark">
              {sheet.name}
            </div>
            <div className="mt-0.5 text-[11px] text-slate-400">
              {sheet.total_rows.toLocaleString("zh-CN")} 行 · {sheet.columns.length} 列
              {resp.sheets.length > 1 ? ` · 共 ${resp.sheets.length} 个工作表` : ""}
            </div>
          </div>
        </div>
        {onClear && (
          <button
            onClick={onClear}
            className="shrink-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-500 smooth hover:border-brand/40 hover:text-brand"
          >
            换文件
          </button>
        )}
      </div>

      {resp.sheets.length > 1 && (
        <div className="flex gap-1 overflow-x-auto border-b border-slate-100 px-3 py-2">
          {resp.sheets.map((s, i) => (
            <button
              key={`${s.name}-${i}`}
              onClick={() => setSheetIdx(i)}
              className={cn(
                "shrink-0 rounded-md px-2 py-1 text-[10px] font-medium smooth",
                i === sheetIdx
                  ? "bg-brand text-white"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200",
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      <PreviewScrollTable sheet={sheet} height={previewHeight} />
    </div>
  );
}

function PreviewScrollTable({ sheet, height }: { sheet: Sheet; height: number | string }) {
  return (
    <div
      className="overflow-auto overscroll-contain"
      style={{ height }}
      title="可在预览框内拖动/滚动查看完整表格"
    >
      <table className="w-max min-w-full border-collapse text-[11px]">
        <thead className="sticky top-0 z-10 bg-slate-50 shadow-[0_1px_0_#e2e8f0]">
          <tr className="text-left font-semibold text-slate-500">
            {sheet.columns.map((c, i) => (
              <th
                key={`${c}-${i}`}
                className="whitespace-nowrap border-b border-r border-slate-200 px-2.5 py-2 last:border-r-0"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sheet.preview.map((row, r) => (
            <tr key={r} className="border-b border-slate-100 text-slate-700 last:border-b-0">
              {sheet.columns.map((_, c) => (
                <td
                  key={c}
                  className="whitespace-nowrap border-r border-slate-100 px-2.5 py-2 last:border-r-0"
                >
                  {fmt(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {sheet.total_rows > sheet.preview.length && (
        <div className="sticky bottom-0 border-t border-slate-100 bg-slate-50/95 px-3 py-2 text-center text-[10px] text-slate-400 backdrop-blur-sm">
          仅预览前 {sheet.preview.length} 行 · 共{" "}
          {sheet.total_rows.toLocaleString("zh-CN")} 行 · 可拖动查看
        </div>
      )}
    </div>
  );
}

"use client";

import * as React from "react";
import { AlertTriangle, Loader2, Move } from "lucide-react";
import type { ComposeTablePreview } from "@/lib/types";
import { cn } from "@/lib/utils";

function displayCell(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number") return value.toLocaleString("zh-CN");
  return String(value);
}

export function TablePreview({
  preview,
  compact = false,
  loading = false,
  className,
}: {
  preview?: ComposeTablePreview | null;
  compact?: boolean;
  loading?: boolean;
  className?: string;
}) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const dragRef = React.useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const sheet = preview?.sheets?.[0];

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !scrollRef.current) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, textarea, select")) return;
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      left: scrollRef.current.scrollLeft,
      top: scrollRef.current.scrollTop,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!dragRef.current || !scrollRef.current) return;
    scrollRef.current.scrollLeft = dragRef.current.left - (event.clientX - dragRef.current.x);
    scrollRef.current.scrollTop = dragRef.current.top - (event.clientY - dragRef.current.y);
  }

  function stopDragging(event: React.PointerEvent<HTMLDivElement>) {
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  if (loading) {
    return (
      <div className={cn("flex min-h-[132px] items-center justify-center border border-slate-200 bg-slate-50 text-xs text-slate-500", className)}>
        <Loader2 className="mr-2 h-4 w-4 animate-spin text-brand" />
        正在用真实样本验证并生成预览
      </div>
    );
  }
  if (preview?.error) {
    return (
      <div className={cn("flex min-h-[96px] items-start gap-2 border border-amber-200 bg-amber-50 p-3 text-[11px] leading-relaxed text-amber-900", className)}>
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>此方案暂时无法生成预览：{preview.error}</span>
      </div>
    );
  }
  if (!sheet) {
    return (
      <div className={cn("flex min-h-[96px] items-center justify-center border border-slate-200 bg-slate-50 text-[11px] text-slate-400", className)}>
        暂无可展示的目标表预览
      </div>
    );
  }

  const compactRowLimit = sheet.columns.length <= 6 ? 10 : sheet.columns.length <= 10 ? 8 : 6;
  const rows = compact ? sheet.rows.slice(0, compactRowLimit) : sheet.rows;
  return (
    <div className={cn("isolate min-w-0 max-w-full overflow-hidden border border-slate-200 bg-white", className)}>
      <div className="flex h-8 items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-2.5 text-[10px] text-slate-500">
        <strong className="truncate text-slate-700">{sheet.name}</strong>
        <span className="flex shrink-0 items-center gap-1">
          <Move className="h-3 w-3" aria-hidden />
          可上下左右拖动
        </span>
      </div>
      <div
        ref={scrollRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={stopDragging}
        onPointerCancel={stopDragging}
        className={cn(
          "relative w-full select-none overflow-auto overscroll-contain",
          compact ? "max-h-[176px]" : "h-[min(58vh,560px)] min-h-[320px]",
          dragging ? "cursor-grabbing" : "cursor-grab",
        )}
      >
        <table className="border-separate border-spacing-0 text-[10px]" style={{ minWidth: Math.max(620, sheet.columns.length * 128) }}>
          <thead>
            <tr>
              {sheet.columns.map((column, index) => (
                <th
                  key={`${column}-${index}`}
                  className={cn(
                    "sticky top-0 z-20 h-8 whitespace-nowrap border-b border-r border-slate-200 bg-slate-100 px-2 text-left font-bold text-slate-600",
                    index === 0 && "left-0 z-30 bg-slate-100",
                  )}
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="hover:bg-brand/[0.025]">
                {sheet.columns.map((column, columnIndex) => (
                  <td
                    key={`${column}-${columnIndex}`}
                    title={displayCell(row[columnIndex])}
                    className={cn(
                      "h-8 max-w-[240px] whitespace-nowrap border-b border-r border-slate-100 bg-white px-2 text-slate-600",
                      columnIndex === 0 && "sticky left-0 z-10 font-semibold text-brand-dark",
                    )}
                  >
                    <span className="block max-w-[220px] overflow-hidden text-ellipsis">
                      {displayCell(row[columnIndex]) || <span className="text-slate-300">空值</span>}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex min-h-8 items-center justify-between gap-3 border-t border-slate-100 px-2.5 text-[10px] text-slate-400">
        <span>代表性样本计算结果，不是AI编造数据</span>
        <span className="shrink-0">
          当前 {rows.length} 行 · {sheet.columns.length} 列
          {preview.estimated_total_rows ? ` · 全量预计约 ${preview.estimated_total_rows.toLocaleString("zh-CN")} 行` : ""}
        </span>
      </div>
    </div>
  );
}

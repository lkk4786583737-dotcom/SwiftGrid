"use client";

import { Button } from "@/components/ui/button";
import type { ResultSheetTab } from "./presentation-types";
import { cn } from "@/lib/utils";

function Pill({
  text,
  variant,
}: {
  text: string;
  variant: "default" | "brand" | "amber";
}) {
  const styles = {
    default: "bg-slate-100 text-slate-600",
    brand: "bg-brand/10 text-brand",
    amber: "bg-amber-50 text-amber-700",
  };
  return (
    <span
      className={cn(
        "inline-flex min-h-[22px] items-center rounded-md px-1.5 text-[9px] font-bold",
        styles[variant],
      )}
    >
      {text}
    </span>
  );
}

export function ResultTable({
  tab,
  page,
  onPageChange,
  onClassificationAction,
}: {
  tab: ResultSheetTab;
  page: number;
  onPageChange?: (page: number) => void;
  onClassificationAction?: (rowId: string) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(tab.totalRows / tab.pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const start = (safePage - 1) * tab.pageSize;
  const rows = tab.serverPaginated ? tab.rows : tab.rows.slice(start, start + tab.pageSize);

  const firstPage = Math.max(1, Math.min(safePage - 2, totalPages - 4));
  const pageButtons = Array.from(
    { length: Math.min(totalPages, 5) },
    (_, i) => firstPage + i,
  );

  return (
    <div>
      <div className="flex min-h-[43px] items-center justify-between gap-3 text-[10px] text-slate-500">
        <span className="truncate">{tab.caption}</span>
        {tab.captionRight && (
          <span className="shrink-0">{tab.captionRight}</span>
        )}
      </div>

      <div className="max-h-[520px] overflow-auto rounded-lg border border-slate-200">
        <table
          className="table-fixed border-separate border-spacing-0 text-[10px]"
          style={{ minWidth: Math.max(870, tab.columns.reduce((total, column) => total + Number.parseInt(column.width ?? "112", 10), 0)) }}
        >
          <colgroup>
            {tab.columns.map((col) => (
              <col key={col.key} style={{ width: col.width }} />
            ))}
          </colgroup>
          <thead>
            <tr className="bg-slate-50 text-left font-bold text-slate-500">
              {tab.columns.map((col, columnIndex) => (
                <th
                  key={col.key}
                  style={columnIndex === 0 ? { left: 0 } : columnIndex === 1 ? { left: tab.columns[0]?.width ?? "112px" } : undefined}
                  className={cn(
                    "sticky top-0 z-20 h-[38px] border-b border-r border-slate-200 bg-slate-50 px-2 last:border-r-0",
                    columnIndex < 2 && "z-30",
                    col.align === "right" && "text-right",
                  )}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr
                key={`${row.id}-${rowIndex}`}
                className="border-b border-slate-100 text-slate-600 last:border-b-0"
              >
                {tab.columns.map((col, columnIndex) => {
                  const value = row.cells[col.key];
                  const isName = col.key.startsWith("name") || col.key.startsWith("type");
                  const pill =
                    row.pill?.column === col.key
                      ? row.pill
                      : undefined;
                  const action =
                    row.action?.column === col.key ? row.action : undefined;

                  return (
                    <td
                      key={col.key}
                      style={columnIndex === 0 ? { left: 0 } : columnIndex === 1 ? { left: tab.columns[0]?.width ?? "112px" } : undefined}
                      className={cn(
                        "h-10 overflow-hidden text-ellipsis whitespace-nowrap border-b border-r border-slate-100 bg-white px-2 last:border-r-0",
                        columnIndex < 2 && "sticky z-10",
                        col.align === "right" && "text-right tabular-nums",
                        isName && !pill && "font-semibold text-brand-dark",
                      )}
                      title={String(value ?? "")}
                    >
                      {pill ? (
                        <Pill text={String(value)} variant={pill.variant} />
                      ) : action ? (
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-[26px] px-2 text-[9px]"
                          onClick={() => onClassificationAction?.(row.id)}
                        >
                          {action.label}
                        </Button>
                      ) : (
                        value
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={tab.columns.length}
                  className="h-24 text-center text-xs text-slate-400"
                >
                  当前工作表没有结果行
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex min-h-[46px] items-center justify-between gap-3 text-[10px] text-slate-500">
        <span className="truncate">
          {tab.live
            ? `当前页显示 ${tab.rows.length} 行，共 ${tab.totalRows.toLocaleString("zh-CN")} 行`
            : tab.id === "customer"
              ? "界面示意：实际客户总数需由全量执行结果确定"
            : tab.id === "pending"
              ? "可以逐条确认并重新执行分类汇总"
              : "当前上传数据内部占比，不代表外部市场份额"}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <div className="flex items-center gap-1">
            <button
              type="button"
              aria-label="上一页"
              disabled={safePage <= 1}
              onClick={() => onPageChange?.(safePage - 1)}
              className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500 disabled:opacity-40"
            >
              ‹
            </button>
            {pageButtons.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onPageChange?.(p)}
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-md border text-[10px]",
                  p === safePage
                    ? "border-brand bg-brand/10 font-extrabold text-brand"
                    : "border-slate-200 bg-white text-slate-500",
                )}
              >
                {p}
              </button>
            ))}
            <button
              type="button"
              aria-label="下一页"
              disabled={safePage >= totalPages}
              onClick={() => onPageChange?.(safePage + 1)}
              className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500 disabled:opacity-40"
            >
              ›
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

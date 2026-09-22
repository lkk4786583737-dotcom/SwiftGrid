"use client";

import { ArrowRightLeft, CircleCheck } from "lucide-react";
import { COMPARISON_TABLE_VIEWPORT_CLASS } from "./DataTable";
import type { Sheet } from "@/lib/types";
import { cn } from "@/lib/utils";

type Cell = string | number | boolean | null;
type Comparison = {
  keyColumn: string;
  beforeByKey: Map<string, Cell[]>;
} | null;

function fmt(v: Cell): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString("zh-CN") : String(v);
  }
  return String(v);
}

export function ProcessReport({
  before,
  after,
  healed,
}: {
  before?: Sheet;
  after: Sheet;
  healed?: boolean;
}) {
  const beforeCols = before?.columns ?? [];
  const afterCols = after.columns;
  const added = afterCols.filter((c) => !beforeCols.includes(c));
  const removed = beforeCols.filter((c) => !afterCols.includes(c));
  const comparison = buildComparison(before, after);

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-4 grid gap-2 text-xs sm:grid-cols-3">
        {before && (
          <SummaryLine icon="check" title="处理前">
            {before.name} · {before.total_rows.toLocaleString("zh-CN")} 行 · {beforeCols.length} 列
          </SummaryLine>
        )}
        <SummaryLine icon="check" title="处理后">
          {after.name} · {after.total_rows.toLocaleString("zh-CN")} 行 · {afterCols.length} 列
        </SummaryLine>
        <SummaryLine icon="diff" title="结构变化">
          {added.length || removed.length
            ? `${added.length} 个新增列，${removed.length} 个移除列`
            : "列结构未变化"}
        </SummaryLine>
      </div>

      {healed && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          新表结构与学习样例存在差异，系统已自动调整逻辑并回测通过。
        </div>
      )}

      {before && (
        <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          {comparison
            ? `已按「${comparison.keyColumn}」对齐处理前后记录，再标记数据变化。`
            : "未识别到稳定主键，仅标记新增列，避免按行号比较造成误判。"}
        </div>
      )}

      <div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-2">
        <DiffTable title="处理前预览" sheet={before} tone="slate" />
        <DiffTable
          title="处理后预览"
          sheet={after}
          before={before}
          comparison={comparison}
          tone="brand"
        />
      </div>
    </div>
  );
}

function SummaryLine({
  icon,
  title,
  children,
}: {
  icon: "check" | "diff";
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white p-3">
      <span className={icon === "check" ? "mt-0.5 text-brand" : "mt-0.5 text-amber-500"}>
        {icon === "check" ? (
          <CircleCheck className="h-3.5 w-3.5" />
        ) : (
          <ArrowRightLeft className="h-3.5 w-3.5" />
        )}
      </span>
      <div className="min-w-0">
        <div className="font-semibold text-slate-700">{title}</div>
        <div className="mt-0.5 truncate text-slate-500">{children}</div>
      </div>
    </div>
  );
}

function DiffTable({
  title,
  sheet,
  before,
  comparison,
  tone,
}: {
  title: string;
  sheet?: Sheet;
  before?: Sheet;
  comparison?: Comparison;
  tone: "slate" | "brand";
}) {
  if (!sheet) {
    return (
      <div className="flex h-[360px] items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/60 text-xs text-slate-400">
        没有处理前预览
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-xl border bg-white",
        tone === "brand" ? "border-brand-light/40" : "border-slate-200",
      )}
    >
      <div
        className={cn(
          "flex items-center justify-between border-b px-3 py-2 text-xs font-semibold",
          tone === "brand"
            ? "border-brand-light/30 bg-brand-light/10 text-brand-dark"
            : "border-slate-200 bg-slate-50 text-slate-500",
        )}
      >
        <span>{title}</span>
        {tone === "brand" && (
          <span className="font-normal text-slate-400">高亮表示新增或变化</span>
        )}
      </div>

      <div className={COMPARISON_TABLE_VIEWPORT_CLASS}>
        <table className="w-max min-w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10">
            <tr className="bg-slate-50 text-left font-semibold text-slate-500">
              {sheet.columns.map((c, i) => (
                <th
                  key={`${c}-${i}`}
                  className={cn(
                    "whitespace-nowrap border-b border-r border-slate-200 p-2 last:border-r-0",
                    tone === "brand" && isAddedColumn(c, before) && "bg-emerald-50 text-brand",
                  )}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.preview.map((row, r) => (
              <tr key={r} className="border-b border-slate-100 text-slate-700 last:border-b-0">
                {sheet.columns.map((c, colIdx) => {
                  const changed =
                    tone === "brand" &&
                    isChangedCell(before, sheet, comparison ?? null, c, row, row[colIdx]);
                  return (
                    <td
                      key={`${r}-${c}`}
                      className={cn(
                        "whitespace-nowrap border-r border-slate-100 p-2 last:border-r-0",
                        changed && "bg-emerald-50 font-semibold text-brand-dark ring-1 ring-inset ring-emerald-100",
                      )}
                    >
                      {fmt(row[colIdx])}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sheet.total_rows > sheet.preview.length && (
        <div className="border-t border-slate-100 bg-slate-50/60 p-2 text-center text-[11px] text-slate-400">
          仅预览前 {sheet.preview.length} 行 · 共 {sheet.total_rows.toLocaleString("zh-CN")} 行
        </div>
      )}
    </div>
  );
}

function isAddedColumn(col: string, before?: Sheet): boolean {
  return !!before && !before.columns.includes(col);
}

function isChangedCell(
  before: Sheet | undefined,
  after: Sheet,
  comparison: Comparison,
  col: string,
  afterRow: Cell[],
  afterValue: Cell,
): boolean {
  if (!before) return false;
  const beforeColIdx = before.columns.indexOf(col);
  if (beforeColIdx < 0) return true;
  if (!comparison) return false;
  const keyIdx = after.columns.indexOf(comparison.keyColumn);
  const rowKey = keyIdx >= 0 ? normalizeKey(afterRow[keyIdx]) : null;
  const beforeRow = rowKey ? comparison.beforeByKey.get(rowKey) : undefined;
  if (!beforeRow) return true;
  return fmt(beforeRow[beforeColIdx]) !== fmt(afterValue);
}

function buildComparison(before: Sheet | undefined, after: Sheet): Comparison {
  if (!before) return null;
  const candidates = before.columns.filter((column) => after.columns.includes(column));
  const ranked = candidates
    .map((column) => {
      const beforeIdx = before.columns.indexOf(column);
      const afterIdx = after.columns.indexOf(column);
      const beforeKeys = before.preview.map((row) => normalizeKey(row[beforeIdx])).filter(Boolean) as string[];
      const afterKeys = after.preview.map((row) => normalizeKey(row[afterIdx])).filter(Boolean) as string[];
      if (beforeKeys.length < 2 || afterKeys.length < 2) return null;
      if (new Set(beforeKeys).size !== beforeKeys.length) return null;
      if (new Set(afterKeys).size !== afterKeys.length) return null;
      const beforeSet = new Set(beforeKeys);
      const overlap = afterKeys.filter((key) => beforeSet.has(key)).length;
      const overlapRatio = overlap / Math.min(beforeKeys.length, afterKeys.length);
      if (overlapRatio < 0.5) return null;
      const namePriority = /(^id$|id$|编号|编码|单号|账号|唯一)/i.test(column) ? 1 : 0;
      return { column, score: namePriority + overlapRatio };
    })
    .filter((item): item is { column: string; score: number } => !!item)
    .sort((a, b) => b.score - a.score);

  const keyColumn = ranked[0]?.column;
  if (!keyColumn) return null;
  const keyIdx = before.columns.indexOf(keyColumn);
  const beforeByKey = new Map<string, Cell[]>();
  before.preview.forEach((row) => {
    const key = normalizeKey(row[keyIdx]);
    if (key) beforeByKey.set(key, row);
  });
  return { keyColumn, beforeByKey };
}

function normalizeKey(value: Cell): string | null {
  if (value === null || value === undefined) return null;
  const normalized = String(value).trim();
  return normalized ? normalized : null;
}

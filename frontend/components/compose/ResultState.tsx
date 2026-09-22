"use client";

import * as React from "react";
import {
  ArrowLeft,
  Check,
  Download,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { DatasetSummaryBar } from "./DatasetSummaryBar";
import { QualityBar, QualitySummary } from "./QualitySummary";
import { ResultTable } from "./ResultTable";
import type {
  DatasetSummary,
  QualitySummaryData,
  ResultSheetTab,
} from "./presentation-types";
import { cn } from "@/lib/utils";

export function ResultState({
  dataset,
  quality,
  resultSummary,
  tabs,
  activeTabId,
  page,
  completed,
  executing,
  executingProgress,
  onTabChange,
  onPageChange,
  onDownloadExcel,
  onAdjustPlan,
  onCompleteTable,
  onBackHome,
  onClassificationAction,
}: {
  dataset: DatasetSummary;
  quality: QualitySummaryData;
  resultSummary?: { source_sales?: string; source_returns?: string; source_net?: string; source_metrics?: Record<string, string> };
  tabs: ResultSheetTab[];
  activeTabId: string;
  page: number;
  completed: boolean;
  executing?: boolean;
  executingProgress?: { label: string; processed: number; total: number };
  onTabChange?: (tabId: string) => void;
  onPageChange?: (page: number) => void;
  onDownloadExcel?: () => void;
  onAdjustPlan?: () => void;
  onCompleteTable?: () => void;
  onBackHome?: () => void;
  onClassificationAction?: (rowId: string) => void;
}) {
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0];
  const canComplete = quality.canComplete && !executing;
  const completeLabel = completed
    ? "已存入逻辑库"
    : "完成制表并存入逻辑库";
  const summaryItems = [
    ...(resultSummary?.source_sales ? [{ label: "销售金额", value: resultSummary.source_sales }] : []),
    ...(resultSummary?.source_returns ? [{ label: "退货金额", value: resultSummary.source_returns }] : []),
    ...(resultSummary?.source_net ? [{ label: "净销售额", value: resultSummary.source_net }] : []),
    ...Object.entries(resultSummary?.source_metrics ?? {}).map(([label, value]) => ({ label, value })),
  ].slice(0, 5);

  function formatSummary(value: string) {
    const numeric = Number(value);
    return Number.isFinite(numeric)
      ? numeric.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : value;
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-100 bg-white shadow-apple">
      <DatasetSummaryBar dataset={dataset} mode="result" />

      {executing && executingProgress ? (
        <div className="border-b border-brand/20 bg-brand/5 px-4 py-4">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="flex items-center gap-2 font-semibold text-brand">
              <Loader2 className="h-4 w-4 animate-spin" />
              {executingProgress.label}
            </span>
            <span className="text-slate-500">
              {executingProgress.processed.toLocaleString("zh-CN")} /{" "}
              {executingProgress.total.toLocaleString("zh-CN")} 行
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-brand/10">
            <div
              className="h-full rounded-full bg-brand transition-all"
              style={{
                width: `${Math.round(
                  executingProgress.total > 0
                    ? (executingProgress.processed / executingProgress.total) * 100
                    : 0,
                )}%`,
              }}
            />
          </div>
        </div>
      ) : (
        <QualityBar quality={quality} />
      )}

      {!executing && summaryItems.length > 0 && (
        <div className="flex min-h-[58px] items-center gap-0 overflow-x-auto border-b border-slate-100 bg-white px-4">
          {summaryItems.map((item, index) => (
            <div key={item.label} className={cn("min-w-[155px] pr-5", index > 0 && "border-l border-slate-100 pl-5")}>
              <span className="block text-[9px] text-slate-400">{item.label}</span>
              <strong className="mt-0.5 block whitespace-nowrap text-xs tabular-nums text-brand-dark">{formatSummary(item.value)}</strong>
            </div>
          ))}
        </div>
      )}

      <div className="grid min-h-[505px] w-full grid-cols-1 xl:grid-cols-[minmax(0,1fr)_286px]">
        <div className="min-w-0 border-b border-slate-100 px-4 pb-4 xl:border-b-0 xl:border-r">
          <div className="flex min-h-[58px] items-center justify-between gap-3 border-b border-slate-100">
            <div className="flex h-[58px] items-stretch gap-6">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onTabChange?.(tab.id)}
                  className={cn(
                    "relative bg-transparent text-xs font-bold transition-colors",
                    activeTabId === tab.id
                      ? "text-brand"
                      : "text-slate-500 hover:text-slate-700",
                  )}
                >
                  {tab.label}
                  <span className="ml-1 rounded bg-slate-100 px-1 py-0.5 text-[9px] font-semibold text-slate-400">
                    {tab.countLabel}
                  </span>
                  {activeTabId === tab.id && (
                    <span className="absolute inset-x-0 bottom-0 h-0.5 bg-brand" />
                  )}
                </button>
              ))}
            </div>
            <div className="flex gap-1.5">
              <Button
                variant="outline"
                size="sm"
                className="h-[34px]"
                disabled={executing}
                onClick={onDownloadExcel}
              >
                <Download className="h-3.5 w-3.5" />
                下载 Excel
              </Button>
            </div>
          </div>

          {activeTab ? (
            <ResultTable
              tab={activeTab}
              page={page}
              onPageChange={onPageChange}
              onClassificationAction={onClassificationAction}
            />
          ) : executing ? (
            <div className="flex min-h-[420px] flex-col items-center justify-center px-6 text-center">
              <Loader2 className="mb-3 h-7 w-7 animate-spin text-brand" />
              <strong className="text-sm text-brand-dark">
                {executingProgress?.label ?? "正在生成全量结果"}
              </strong>
              <span className="mt-1.5 max-w-md text-xs leading-relaxed text-slate-500">
                已读取 {(executingProgress?.processed ?? 0).toLocaleString("zh-CN")} / {(executingProgress?.total ?? 0).toLocaleString("zh-CN")} 行。结果完成前不会展示抽样数据。
              </span>
            </div>
          ) : (
            <div className="flex min-h-[420px] items-center justify-center text-xs text-slate-400">
              暂无可展示的结果表
            </div>
          )}
        </div>

        <QualitySummary quality={quality} />
      </div>

      <div className="flex min-h-[70px] items-center justify-between gap-4 border-t border-slate-100 bg-white px-4 py-3">
        <div>
          <strong className="text-xs text-brand-dark">确认目标表后完成制表</strong>
          <span className="mt-0.5 block text-[10px] text-slate-500">
            只有此操作会把已验证的转换逻辑自动保存到「我的逻辑库」。
          </span>
          {!quality.canComplete && quality.blockReason && (
            <span className="mt-1 block text-[10px] text-red-500">
              {quality.blockReason}
            </span>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {!completed && <Button variant="ghost" disabled={executing} onClick={onAdjustPlan}>
            <ArrowLeft className="h-4 w-4" />
            调整目标表
          </Button>}
          {completed && (
            <Button variant="brand" onClick={onBackHome}>
              <ArrowLeft className="h-4 w-4" />
              继续制作新表
            </Button>
          )}
          <Button
            variant="brand"
            disabled={!canComplete || completed}
            onClick={onCompleteTable}
            className={cn(completed && "hidden")}
          >
            <Check className="h-4 w-4" />
            {completeLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { CircleCheck, Download, TableProperties } from "lucide-react";
import { AnalysisPanel } from "./AnalysisPanel";
import { DataTable } from "./DataTable";
import { ProcessReport } from "./ProcessReport";
import { ChartPanel } from "./ChartPanel";
import { AdvancedOptions } from "./AdvancedOptions";
import { Badge } from "@/components/ui/badge";
import { useToast } from "./Toast";
import * as api from "@/lib/api";
import { exportChart, type ChartFormat } from "@/lib/chartExport";
import { humanizeError } from "@/lib/errors";
import type { AnalysisChart, Sheet } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "preview" | "report" | "analysis" | "chart";

const RESULT_TABS: Array<{
  id: Tab;
  label: string;
  badge?: { label: string; className: string };
}> = [
  { id: "preview", label: "数据预览" },
  { id: "report", label: "处理报告" },
  {
    id: "analysis",
    label: "数据分析",
    badge: { label: "Beta", className: "bg-amber-100 text-amber-700" },
  },
  {
    id: "chart",
    label: "图表",
    badge: { label: "New", className: "bg-brand-light/20 text-brand" },
  },
];

export function ResultTabs({
  resultPreview,
  beforePreview,
  resultHandle,
  code,
  healed,
  banner,
}: {
  resultPreview: Sheet[];
  beforePreview?: Sheet[];
  resultHandle: string;
  code?: string;
  healed?: boolean;
  banner?: string;
}) {
  const toast = useToast();
  const [tab, setTab] = React.useState<Tab>("preview");
  const [busy, setBusy] = React.useState(false);
  const [sheetIdx, setSheetIdx] = React.useState(0);
  const [activeChart, setActiveChart] = React.useState<AnalysisChart | null>(null);
  const chartRef = React.useRef<HTMLDivElement>(null);

  const sheets = resultPreview ?? [];
  const sheet = sheets[sheetIdx] ?? sheets[0];
  const empty = !sheet || !sheet.columns?.length || !sheet.preview?.length;

  async function downloadExcel() {
    setBusy(true);
    try {
      await api.exportXlsx(resultHandle);
      toast.success("下载已开始", "Excel 结果正在保存到你的下载目录。");
    } catch (error) {
      toast.error(humanizeError(error), downloadExcel);
    } finally {
      setBusy(false);
    }
  }

  async function downloadChart(fmt: ChartFormat) {
    try {
      await exportChart(chartRef.current, fmt);
      toast.success("图表已导出", `${fmt.toUpperCase()} 文件正在下载。`);
    } catch (error) {
      toast.error(humanizeError(error), () => downloadChart(fmt));
    }
  }

  if (sheets.length === 0 || empty) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/50 py-16 text-center">
        <TableProperties className="mb-3 h-8 w-8 text-slate-300" />
        <p className="text-sm font-medium text-slate-600">结果表是空的</p>
        <p className="mt-1 max-w-xs text-xs text-slate-400">
          AI 跑完了但没有产出可预览的数据。可以点“重新开始”换一份源表试试，或检查目标样例是否正确。
        </p>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ ease: [0.16, 1, 0.3, 1], duration: 0.5 }}
      className="flex flex-1 flex-col"
    >
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3 border-b border-slate-100">
        <div className="flex min-w-0 gap-5 overflow-x-auto">
          {RESULT_TABS.map((item) => (
            <TabBtn
              key={item.id}
              on={tab === item.id}
              onClick={() => setTab(item.id)}
            >
              {item.label}
              {item.badge && (
                <Badge className={cn("ml-1", item.badge.className)}>
                  {item.badge.label}
                </Badge>
              )}
            </TabBtn>
          ))}
        </div>

        <div className="mb-2 flex shrink-0 items-center gap-2">
          {tab === "chart" && (
            <div className="flex items-center gap-1 border-r border-slate-200 pr-2">
              <span className="mr-1 text-[11px] text-slate-400">下载图</span>
              {(["png", "svg", "jpg"] as ChartFormat[]).map((format) => (
                <button
                  key={format}
                  onClick={() => downloadChart(format)}
                  className="rounded-md border border-brand/40 px-2 py-1 text-[11px] font-semibold uppercase text-brand smooth hover:bg-brand hover:text-white"
                >
                  {format}
                </button>
              ))}
            </div>
          )}
          <button
            onClick={downloadExcel}
            disabled={busy}
            className="flex items-center gap-1 rounded-lg border border-brand/40 px-3 py-1.5 text-xs font-medium text-brand smooth hover:bg-brand hover:text-white disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" /> {busy ? "导出中..." : "下载 Excel"}
          </button>
        </div>
      </div>

      {sheets.length > 1 && tab !== "chart" && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              onClick={() => setSheetIdx(i)}
              className={cn(
                "rounded-lg px-2.5 py-1 text-[11px] font-medium smooth",
                sheetIdx === i
                  ? "bg-brand text-white"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200",
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      {tab === "preview" && (
        <div className="flex flex-1 flex-col">
          {banner && (
            <div className="mb-4 flex items-center gap-3 rounded-lg border border-brand-light/30 bg-brand-light/10 p-3">
              <div className="flex h-6 w-6 items-center justify-center rounded-full bg-brand text-white">
                <CircleCheck className="h-3.5 w-3.5" />
              </div>
              <span className="text-sm font-medium text-brand-dark">{banner}</span>
            </div>
          )}
          <DataTable sheet={sheet} highlightFrom={Math.min(2, sheet.columns.length)} />
          {code && <AdvancedOptions code={code} />}
        </div>
      )}

      {tab === "report" && (
        <ProcessReport
          before={beforePreview?.[sheetIdx] ?? beforePreview?.[0]}
          after={sheet}
          healed={healed}
        />
      )}

      {tab === "analysis" && (
        <AnalysisPanel
          resultHandle={resultHandle}
          sheet={sheet}
          onUseChart={(chart) => {
            setActiveChart(chart);
            setTab("chart");
          }}
        />
      )}

      {tab === "chart" && (
        <div ref={chartRef} className="flex flex-1 flex-col">
          <ChartPanel
            resultHandle={resultHandle}
            sheets={sheets}
            sheetIndex={sheetIdx}
            initialChart={activeChart}
          />
        </div>
      )}
    </motion.div>
  );
}

function TabBtn({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "border-b-2 px-1 pb-3 text-sm smooth",
        on
          ? "border-brand font-semibold text-brand"
          : "border-transparent font-medium text-slate-400 hover:text-slate-600",
      )}
    >
      {children}
    </button>
  );
}

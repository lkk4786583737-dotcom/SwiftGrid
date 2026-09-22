"use client";

import * as React from "react";
import {
  BarChart3,
  CircleAlert,
  Lightbulb,
  LineChart,
  PieChart,
  Search,
  Sigma,
} from "lucide-react";
import * as api from "@/lib/api";
import type { AnalysisChart, AnalysisSummaryResp, Sheet } from "@/lib/types";
import { cn } from "@/lib/utils";

const ROLE_LABEL: Record<string, string> = {
  time: "时间",
  numeric: "数值",
  category: "分类",
  text: "文本",
};

const CHART_ICON: Record<string, React.ElementType> = {
  line: LineChart,
  bar: BarChart3,
  pie: PieChart,
  scatter: Sigma,
  hist: BarChart3,
};

export function AnalysisPanel({
  resultHandle,
  sheet,
  onUseChart,
}: {
  resultHandle: string;
  sheet: Sheet;
  onUseChart: (chart: AnalysisChart) => void;
}) {
  const [data, setData] = React.useState<AnalysisSummaryResp | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .analysisSummary({ result_handle: resultHandle, sheet: sheet.name })
      .then((r) => {
        if (alive) setData(r);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "数据分析生成失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [resultHandle, sheet.name]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-slate-400">
        正在生成轻量数据分析...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <CircleAlert className="mb-3 h-8 w-8 text-amber-500" />
        <p className="text-sm font-medium text-slate-600">暂时无法生成数据分析</p>
        <p className="mt-1 text-xs text-slate-400">{error}</p>
      </div>
    );
  }

  const profile = data.profile;
  const topColumns = profile.columns.slice(0, 8);

  return (
    <div className="grid flex-1 grid-cols-1 gap-4 xl:grid-cols-[280px_1fr]">
      <aside className="space-y-4">
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Search className="h-4 w-4 text-brand" /> 表格画像
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <Metric label="行数" value={profile.total_rows.toLocaleString("zh-CN")} />
            <Metric label="列数" value={String(profile.total_columns)} />
            <Metric label="数值列" value={String(profile.numeric_columns.length)} />
            <Metric label="分类列" value={String(profile.category_columns.length)} />
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 text-sm font-semibold text-slate-700">字段识别</div>
          <div className="space-y-2">
            {topColumns.map((c) => (
              <div key={c.name} className="flex items-center justify-between gap-2 text-xs">
                <span className="min-w-0 truncate text-slate-600">{c.name}</span>
                <span
                  className={cn(
                    "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold",
                    c.role === "numeric" && "bg-brand-light/15 text-brand",
                    c.role === "time" && "bg-blue-50 text-blue-600",
                    c.role === "category" && "bg-amber-50 text-amber-700",
                    c.role === "text" && "bg-slate-100 text-slate-500",
                  )}
                >
                  {ROLE_LABEL[c.role]}
                </span>
              </div>
            ))}
          </div>
        </section>
      </aside>

      <main className="space-y-4">
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Lightbulb className="h-4 w-4 text-brand" /> 基础洞察
          </div>
          <div className="space-y-2">
            {data.insights.map((item, i) => (
              <p key={i} className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
                {item}
              </p>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 text-sm font-semibold text-slate-700">推荐分析问题</div>
          <div className="grid gap-2 md:grid-cols-2">
            {data.recommended_questions.map((q, i) => (
              <div key={i} className="rounded-lg border border-slate-100 bg-slate-50/60 p-3 text-xs leading-5 text-slate-600">
                {q}
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 text-sm font-semibold text-slate-700">推荐图表</div>
          <div className="grid gap-3 md:grid-cols-2">
            {data.recommended_charts.map((chart, i) => {
              const Icon = CHART_ICON[chart.chart_type] ?? BarChart3;
              return (
                <button
                  key={`${chart.title}-${i}`}
                  onClick={() => onUseChart(chart)}
                  className="group rounded-xl border border-slate-200 bg-white p-4 text-left smooth hover:border-brand/50 hover:shadow-sm"
                >
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <Icon className="h-4 w-4 shrink-0 text-brand" />
                      <span className="truncate text-sm font-semibold text-slate-700">
                        {chart.title}
                      </span>
                    </div>
                    <span className="shrink-0 rounded-full bg-brand-light/15 px-2 py-0.5 text-[10px] font-semibold text-brand">
                      应用
                    </span>
                  </div>
                  <p className="text-xs leading-5 text-slate-500">{chart.reason}</p>
                  <p className="mt-2 text-[11px] text-slate-400">
                    {chart.x}{chart.y ? ` / ${chart.y}` : ""} · {chart.agg}
                  </p>
                </button>
              );
            })}
          </div>
        </section>
      </main>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className="mt-1 text-sm font-semibold text-slate-700">{value}</div>
    </div>
  );
}

"use client";

import * as React from "react";
import {
  BarChart3 as IconBar,
  LineChart as IconLine,
  PieChart as IconPie,
  ScatterChart as IconScatter,
  Lightbulb,
  SlidersHorizontal,
} from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import * as api from "@/lib/api";
import type {
  AnalysisChart,
  ChartDataResp,
  ChartPoint,
  ChartSuggestion,
  ChartsResp,
  Sheet,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const CHART_TYPES = ["line", "bar", "pie", "scatter", "hist"] as const;
type ChartType = (typeof CHART_TYPES)[number];

const CHART_NAMES: Record<ChartType, string> = {
  line: "折线图",
  bar: "柱状图",
  pie: "饼图",
  scatter: "散点图",
  hist: "直方图",
};

const AGG_NAMES: Record<string, string> = {
  sum: "求和",
  avg: "平均",
  count: "计数",
  max: "最大值",
  min: "最小值",
};

const ICON: Record<ChartType, React.ElementType> = {
  line: IconLine,
  bar: IconBar,
  pie: IconPie,
  scatter: IconScatter,
  hist: IconBar,
};

function fromAnalysisChart(c: AnalysisChart): ChartSuggestion & { agg: string } {
  return {
    type: c.chart_type,
    x: c.x,
    y: c.y,
    label: c.title,
    agg: c.agg,
  };
}

export function ChartPanel({
  resultHandle,
  sheets,
  sheetIndex = 0,
  initialChart,
}: {
  resultHandle: string;
  sheets: Sheet[];
  sheetIndex?: number;
  initialChart?: AnalysisChart | null;
}) {
  const sheet = sheets[sheetIndex] ?? sheets[0];
  const [localSheetIdx, setLocalSheetIdx] = React.useState(sheetIndex);
  const activeSheet = sheets[localSheetIdx] ?? sheet;

  const [data, setData] = React.useState<ChartsResp | null>(null);
  const [chartData, setChartData] = React.useState<ChartDataResp | null>(null);
  const [loadingSuggest, setLoadingSuggest] = React.useState(true);
  const [loadingPoints, setLoadingPoints] = React.useState(false);
  const [recIdx, setRecIdx] = React.useState(0);
  const [chartType, setChartType] = React.useState<ChartType>("bar");
  const [xCol, setXCol] = React.useState("");
  const [yCol, setYCol] = React.useState<string | null>(null);
  const [agg, setAgg] = React.useState("sum");
  const [customized, setCustomized] = React.useState(false);
  const [renderErr, setRenderErr] = React.useState<string | null>(null);

  React.useEffect(() => setLocalSheetIdx(sheetIndex), [sheetIndex]);

  React.useEffect(() => {
    let alive = true;
    setLoadingSuggest(true);
    setChartData(null);
    api
      .suggestCharts(resultHandle, activeSheet.name)
      .then((r) => {
        if (!alive) return;
        setData(r);
        setRecIdx(0);
        setCustomized(false);
        const first = initialChart ? fromAnalysisChart(initialChart) : r.suggestions[0];
        if (first) {
          setChartType(first.type);
          setXCol(first.x);
          setYCol(first.y);
          setAgg(initialChart ? initialChart.agg : first.type === "hist" ? "count" : "sum");
        } else if (r.roles.numeric[0]) {
          setChartType("hist");
          setXCol(r.roles.numeric[0]);
          setYCol(null);
          setAgg("count");
        }
      })
      .catch(() => setData(null))
      .finally(() => {
        if (alive) setLoadingSuggest(false);
      });
    return () => {
      alive = false;
    };
  }, [resultHandle, activeSheet.name, initialChart]);

  React.useEffect(() => {
    if (!xCol || (chartType !== "hist" && !yCol)) return;
    let alive = true;
    setLoadingPoints(true);
    setRenderErr(null);
    api
      .chartData({
        result_handle: resultHandle,
        sheet: activeSheet.name,
        chart_type: chartType,
        x: xCol,
        y: yCol,
        agg,
      })
      .then((r) => {
        if (!alive) return;
        setChartData(r);
        if (!r.points.length) setRenderErr("这组图表配置没有可绘制的数据");
      })
      .catch((e) => {
        if (!alive) return;
        setChartData(null);
        setRenderErr(e instanceof Error ? e.message : "图表数据生成失败");
      })
      .finally(() => {
        if (alive) setLoadingPoints(false);
      });
    return () => {
      alive = false;
    };
  }, [resultHandle, activeSheet.name, chartType, xCol, yCol, agg]);

  const suggestions = data?.suggestions ?? [];
  const roles = data?.roles ?? { time: [], category: [], numeric: [] };
  const cols = activeSheet.columns;
  const points = chartData?.points ?? [];

  function applyRecommendation(idx: number) {
    const s = suggestions[idx];
    if (!s) return;
    setRecIdx(idx);
    setChartType(s.type);
    setXCol(s.x);
    setYCol(s.y);
    setAgg(s.type === "hist" ? "count" : "sum");
    setCustomized(false);
    setRenderErr(null);
  }

  function markCustom() {
    setCustomized(true);
    setRenderErr(null);
  }

  const yCandidates = React.useMemo(() => {
    if (chartType === "scatter") return roles.numeric.length ? roles.numeric : cols;
    if (chartType === "hist") return [];
    return roles.numeric.length ? roles.numeric : cols;
  }, [chartType, roles.numeric, cols]);

  const xLabel = chartType === "bar" || chartType === "pie" ? "分类轴" : "X 轴";
  const yLabel = chartType === "bar" || chartType === "pie" ? "数值轴" : "Y 轴";
  const chartTitle = initialChart?.title || makeChartTitle(chartType, xCol, yCol);
  const chartSubtitle = [
    activeSheet.name,
    chartData ? `全量 ${chartData.total_rows.toLocaleString("zh-CN")} 行` : "全量数据",
    chartType === "scatter" ? "原始点位" : `${AGG_NAMES[agg] ?? agg}聚合`,
  ].join(" · ");

  if (loadingSuggest) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-slate-400">
        正在分析可绘制的图表...
      </div>
    );
  }

  if (!roles.numeric.length) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm font-medium text-slate-600">当前表没有数值列</p>
        <p className="mt-1 max-w-xs text-xs text-slate-400">
          图表需要至少一列数字，如金额、数量或计数。这份结果更适合在数据预览里查看。
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      {sheets.length > 1 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              onClick={() => setLocalSheetIdx(i)}
              className={cn(
                "rounded-lg px-2.5 py-1 text-[11px] font-medium smooth",
                localSheetIdx === i
                  ? "bg-brand text-white"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200",
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      <div className="mb-4 rounded-xl border border-brand-light/30 bg-brand-light/5 p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-brand-dark">
          <Lightbulb className="h-4 w-4 text-brand" /> 智能推荐
          {customized && (
            <span className="flex items-center gap-0.5 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
              <SlidersHorizontal className="h-3 w-3" /> 已自定义
            </span>
          )}
        </div>
        {suggestions.length === 0 ? (
          <p className="text-xs text-slate-400">
            暂无自动推荐，请在下方手动选择图型和坐标轴。
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s, i) => {
              const Icon = ICON[s.type];
              const on = !customized && i === recIdx;
              return (
                <button
                  key={i}
                  onClick={() => applyRecommendation(i)}
                  className={cn(
                    "flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium smooth",
                    on
                      ? "border-2 border-brand bg-white font-semibold text-brand shadow-sm"
                      : "border border-slate-200 bg-white text-slate-500 hover:border-brand/50",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" /> {s.label.replace(/^[^\s]+\s/, "")}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-4">
        <SelectField label="图表类型" value={chartType} onValueChange={(v) => {
          markCustom();
          setChartType(v as ChartType);
          if (v === "hist") {
            setYCol(null);
            setAgg("count");
          }
        }}>
          {CHART_TYPES.map((t) => (
            <SelectItem key={t} value={t} className="text-xs">
              {CHART_NAMES[t]}
            </SelectItem>
          ))}
        </SelectField>

        <SelectField label={xLabel} value={xCol} placeholder="选列" onValueChange={(v) => {
          markCustom();
          setXCol(v);
        }}>
          {cols.map((c) => (
            <SelectItem key={c} value={c} className="text-xs">
              {c}
            </SelectItem>
          ))}
        </SelectField>

        {chartType !== "hist" && (
          <SelectField label={yLabel} value={yCol ?? ""} placeholder="选数值列" onValueChange={(v) => {
            markCustom();
            setYCol(v);
          }}>
            {yCandidates.map((c) => (
              <SelectItem key={c} value={c} className="text-xs">
                {c}{roles.numeric.includes(c) ? "" : "（非数值，可能画不出）"}
              </SelectItem>
            ))}
          </SelectField>
        )}

        {chartType !== "scatter" && (
          <SelectField label="聚合方式" value={agg} onValueChange={(v) => {
            markCustom();
            setAgg(v);
          }}>
            {Object.entries(AGG_NAMES).map(([value, label]) => (
              <SelectItem key={value} value={value} className="text-xs">
                {label}
              </SelectItem>
            ))}
          </SelectField>
        )}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
        <span>全量数据制图：{chartData ? `${chartData.total_rows.toLocaleString("zh-CN")} 行` : "准备中"}</span>
        {chartData && <span>聚合：{AGG_NAMES[chartData.aggregation] ?? chartData.aggregation}</span>}
        {chartData?.limited && <span>为保证可读性，仅展示前 {chartData.limit} 个点</span>}
      </div>

      <div className="flex min-h-[360px] flex-1 items-center justify-center rounded-xl border border-slate-200 bg-white p-4">
        {loadingPoints ? (
          <span className="text-xs text-slate-400">正在基于全量表格生成图表...</span>
        ) : renderErr ? (
          <div className="text-center">
            <p className="text-xs font-medium text-amber-700">{renderErr}</p>
            <p className="mt-1 text-[11px] text-slate-400">
              换一组图型或坐标轴试试，常见原因是数值轴选择了文本列。
            </p>
          </div>
        ) : points.length ? (
          <ChartCanvas
            type={chartType}
            points={points}
            title={chartTitle}
            subtitle={chartSubtitle}
            xLabel={chartType === "hist" ? xCol : xCol || "X"}
            yLabel={chartType === "scatter" ? yCol || "Y" : yCol ? `${AGG_NAMES[agg] ?? agg} ${yCol}` : "计数"}
            limited={!!chartData?.limited}
          />
        ) : (
          <span className="text-xs text-slate-400">请选择完整的坐标轴</span>
        )}
      </div>
    </div>
  );
}

function SelectField({
  label,
  value,
  placeholder,
  onValueChange,
  children,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onValueChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-[11px] font-semibold text-slate-600">
        {label}
      </label>
      <Select value={value} onValueChange={onValueChange}>
        <SelectTrigger className="h-9 text-xs">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>{children}</SelectContent>
      </Select>
    </div>
  );
}

function makeChartTitle(type: ChartType, x: string, y: string | null): string {
  if (type === "hist") return `${x}分布`;
  if (type === "pie") return `${y ?? "数值"}按${x}占比`;
  if (type === "scatter") return `${x}与${y ?? "数值"}关系`;
  if (type === "line") return `${y ?? "数值"}趋势`;
  return `按${x}对比${y ?? "数值"}`;
}

function ChartCanvas({
  type,
  points,
  title,
  subtitle,
  xLabel,
  yLabel,
  limited,
}: {
  type: ChartType;
  points: ChartPoint[];
  title: string;
  subtitle: string;
  xLabel: string;
  yLabel: string;
  limited: boolean;
}) {
  if (type === "pie") {
    return <PieChart points={points} title={title} subtitle={subtitle} limited={limited} />;
  }
  return (
    <AxisChart
      type={type}
      points={points}
      title={title}
      subtitle={subtitle}
      xLabel={xLabel}
      yLabel={yLabel}
      limited={limited}
    />
  );
}

const PALETTE = ["#0F8A4D", "#2FBF71", "#0B7A43", "#5BD08C", "#2563EB", "#F59E0B", "#64748B", "#8B5CF6"];

function AxisChart({
  type,
  points,
  title,
  subtitle,
  xLabel,
  yLabel,
  limited,
}: {
  type: ChartType;
  points: ChartPoint[];
  title: string;
  subtitle: string;
  xLabel: string;
  yLabel: string;
  limited: boolean;
}) {
  const width = 760;
  const height = 420;
  const plot = { left: 76, top: 78, right: 28, bottom: 76 };
  const plotW = width - plot.left - plot.right;
  const plotH = height - plot.top - plot.bottom;
  const shown = points.slice(0, type === "bar" ? 18 : 80);
  const values = shown.map((p) => p.value);
  const max = Math.max(...values, 1);
  const min = type === "scatter" ? Math.min(...values, 0) : 0;
  const range = Math.max(max - min, 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => min + range * t);

  const xValues = shown.map((p) => p.x ?? 0);
  const minX = type === "scatter" ? Math.min(...xValues) : 0;
  const maxX = type === "scatter" ? Math.max(...xValues) : Math.max(shown.length - 1, 1);
  const xRange = Math.max(maxX - minX, 1);
  const xFor = (p: ChartPoint, i: number) =>
    type === "scatter"
      ? plot.left + (((p.x ?? 0) - minX) / xRange) * plotW
      : plot.left + (shown.length === 1 ? plotW / 2 : (i / (shown.length - 1)) * plotW);
  const yFor = (v: number) => plot.top + plotH - ((v - min) / range) * plotH;
  const linePath = shown.map((p, i) => `${i === 0 ? "M" : "L"} ${xFor(p, i)} ${yFor(p.value)}`).join(" ");
  const barW = Math.max(10, Math.min(34, plotW / Math.max(shown.length, 1) - 8));
  const labelStep = Math.max(1, Math.ceil(shown.length / 8));

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full max-w-[900px]" role="img" aria-label={title}>
      <rect width={width} height={height} fill="#ffffff" />
      <text x={plot.left} y={30} fill="#0f172a" fontSize="22" fontWeight="700">{title}</text>
      <text x={plot.left} y={54} fill="#64748b" fontSize="12">{subtitle}{limited ? " · 图中已限量展示" : ""}</text>

      {ticks.map((t, i) => {
        const y = yFor(t);
        return (
          <g key={i}>
            <line x1={plot.left} y1={y} x2={width - plot.right} y2={y} stroke="#e2e8f0" strokeDasharray={i === 0 ? "0" : "4 4"} />
            <text x={plot.left - 10} y={y + 4} textAnchor="end" fill="#64748b" fontSize="11">{formatNumber(t)}</text>
          </g>
        );
      })}

      <line x1={plot.left} y1={plot.top} x2={plot.left} y2={plot.top + plotH} stroke="#94a3b8" />
      <line x1={plot.left} y1={plot.top + plotH} x2={plot.left + plotW} y2={plot.top + plotH} stroke="#94a3b8" />

      {type === "bar" || type === "hist" ? shown.map((p, i) => {
        const x = plot.left + (i + 0.5) * (plotW / shown.length) - barW / 2;
        const y = yFor(p.value);
        return (
          <g key={i}>
            <rect x={x} y={y} width={barW} height={plot.top + plotH - y} rx="4" fill="#0F8A4D" />
            {i % labelStep === 0 && (
              <text x={x + barW / 2} y={plot.top + plotH + 18} textAnchor="middle" fill="#64748b" fontSize="10">
                {shortLabel(p.label)}
              </text>
            )}
          </g>
        );
      }) : null}

      {type === "line" && (
        <>
          <path d={linePath} fill="none" stroke="#0F8A4D" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          {shown.map((p, i) => (
            <circle key={i} cx={xFor(p, i)} cy={yFor(p.value)} r="3.5" fill="#0F8A4D" />
          ))}
        </>
      )}

      {type === "scatter" && shown.map((p, i) => (
        <circle key={i} cx={xFor(p, i)} cy={yFor(p.value)} r="5" fill="#0F8A4D" opacity="0.82" />
      ))}

      {type !== "scatter" && type !== "bar" && type !== "hist" ? null : null}
      {(type === "line") && shown.map((p, i) => i % labelStep === 0 ? (
        <text key={i} x={xFor(p, i)} y={plot.top + plotH + 18} textAnchor="middle" fill="#64748b" fontSize="10">
          {shortLabel(p.label)}
        </text>
      ) : null)}

      <text x={plot.left + plotW / 2} y={height - 22} textAnchor="middle" fill="#334155" fontSize="12" fontWeight="600">{xLabel}</text>
      <text x={18} y={plot.top + plotH / 2} textAnchor="middle" fill="#334155" fontSize="12" fontWeight="600" transform={`rotate(-90 18 ${plot.top + plotH / 2})`}>{yLabel}</text>
    </svg>
  );
}

function PieChart({
  points,
  title,
  subtitle,
  limited,
}: {
  points: ChartPoint[];
  title: string;
  subtitle: string;
  limited: boolean;
}) {
  const width = 760;
  const height = 420;
  const shown = points.slice(0, 8);
  const total = shown.reduce((s, p) => s + p.value, 0) || 1;
  let acc = 0;
  const r = 110;
  const cx = 245;
  const cy = 235;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full max-w-[900px]" role="img" aria-label={title}>
      <rect width={width} height={height} fill="#ffffff" />
      <text x="54" y="30" fill="#0f172a" fontSize="22" fontWeight="700">{title}</text>
      <text x="54" y="54" fill="#64748b" fontSize="12">{subtitle}{limited ? " · 图中已限量展示" : ""}</text>
      {shown.map((p, i) => {
        const start = (acc / total) * 2 * Math.PI;
        acc += p.value;
        const end = (acc / total) * 2 * Math.PI;
        const large = end - start > Math.PI ? 1 : 0;
        const x1 = cx + r * Math.sin(start);
        const y1 = cy - r * Math.cos(start);
        const x2 = cx + r * Math.sin(end);
        const y2 = cy - r * Math.cos(end);
        return (
          <path
            key={i}
            d={`M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`}
            fill={PALETTE[i % PALETTE.length]}
            stroke="#ffffff"
            strokeWidth="2"
          />
        );
      })}
      <text x={cx} y={cy - 4} textAnchor="middle" fill="#0f172a" fontSize="18" fontWeight="700">100%</text>
      <text x={cx} y={cy + 17} textAnchor="middle" fill="#64748b" fontSize="11">合计</text>
      {shown.map((p, i) => (
        <g key={i} transform={`translate(430 ${122 + i * 30})`}>
          <rect width="12" height="12" rx="2" fill={PALETTE[i % PALETTE.length]} />
          <text x="20" y="10" fill="#334155" fontSize="12">{shortLabel(p.label, 16)}</text>
          <text x="210" y="10" textAnchor="end" fill="#64748b" fontSize="12">
            {Math.round((p.value / total) * 100)}% · {formatNumber(p.value)}
          </text>
        </g>
      ))}
    </svg>
  );
}

function formatNumber(v: number): string {
  if (Math.abs(v) >= 100000000) return `${(v / 100000000).toFixed(1)}亿`;
  if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(1)}万`;
  if (Number.isInteger(v)) return v.toLocaleString("zh-CN");
  return v.toFixed(2);
}

function shortLabel(s: string, max = 10): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

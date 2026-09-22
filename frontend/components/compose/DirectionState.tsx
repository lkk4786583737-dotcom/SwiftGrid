"use client";

import * as React from "react";
import { ArrowRight, Check, ChevronDown, Loader2, Maximize2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ComposeTablePreview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ComposeBadge } from "./ComposeBadge";
import { DatasetSummaryBar } from "./DatasetSummaryBar";
import { TablePreview } from "./TablePreview";
import type { DatasetSummary, DirectionOption, FieldRoleMapping } from "./presentation-types";

type Readiness = {
  level: 1 | 2 | 3 | 4;
  label: string;
  reasons: string[];
  warnings: string[];
};

export function DirectionState({
  dataset,
  directions,
  selectedDirectionId,
  customPrompt,
  fieldRoles,
  readiness,
  submitting = false,
  onSelectDirection,
  onCustomPromptChange,
  onSubmitDirection,
  onLoadPreview,
}: {
  dataset: DatasetSummary;
  directions: DirectionOption[];
  selectedDirectionId: string | null;
  customPrompt: string;
  fieldRoles: FieldRoleMapping[];
  readiness?: Readiness;
  submitting?: boolean;
  onSelectDirection?: (id: string) => void;
  onCustomPromptChange?: (value: string) => void;
  onSubmitDirection?: () => void;
  onLoadPreview?: (id: string) => Promise<ComposeTablePreview | null>;
}) {
  const canContinue = !!selectedDirectionId || customPrompt.trim().length > 0;
  const selected = directions.find((direction) => direction.id === selectedDirectionId) ?? directions[0];
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const [expandedPreview, setExpandedPreview] = React.useState<ComposeTablePreview | null>(null);
  const [previewLoading, setPreviewLoading] = React.useState(false);

  async function expand(direction: DirectionOption) {
    setExpandedId(direction.id);
    setExpandedPreview(direction.preview ?? null);
    if (!onLoadPreview) return;
    setPreviewLoading(true);
    try {
      setExpandedPreview(await onLoadPreview(direction.id));
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-100 bg-white shadow-apple">
      <DatasetSummaryBar dataset={dataset} />

      <div className="grid min-h-[640px] w-full grid-cols-1 xl:grid-cols-[minmax(0,1.55fr)_minmax(300px,0.55fr)]">
        <div className="min-w-0 border-b border-slate-100 p-5 xl:border-b-0 xl:border-r">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h3 className="text-[15px] font-bold text-brand-dark">选择要制作的目标表</h3>
              <p className="text-xs text-slate-500">每个建议都已用真实代表性数据运行预览，选择后仍可修改。</p>
            </div>
            <ComposeBadge variant="neutral">动态生成 {directions.length} 个可执行方案</ComposeBadge>
          </div>

          <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-3">
            {directions.map((direction) => {
              const isSelected = selectedDirectionId === direction.id;
              return (
                <article
                  key={direction.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelectDirection?.(direction.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectDirection?.(direction.id);
                    }
                  }}
                  className={cn(
                    "relative min-w-0 cursor-pointer overflow-hidden rounded-lg border bg-white transition-all hover:border-brand/40 hover:shadow-sm",
                    isSelected ? "border-2 border-brand" : "border-slate-200",
                  )}
                >
                  <div className="p-3.5">
                    <span className={cn(
                      "absolute right-3 top-3 flex h-[18px] w-[18px] items-center justify-center rounded-full border",
                      isSelected ? "border-brand bg-brand text-white" : "border-slate-300 text-transparent",
                    )}>
                      <Check className="h-3 w-3" />
                    </span>
                    <div className="mb-2 flex min-w-0 flex-wrap items-center gap-1.5 pr-7">
                      <h4 className="text-[13px] font-bold text-brand-dark">{direction.title}</h4>
                      {direction.assessment && (
                        <>
                          <ComposeBadge variant="neutral">{direction.assessment.dataEvidence}</ComposeBadge>
                          <ComposeBadge variant="green">{direction.assessment.executability}</ComposeBadge>
                        </>
                      )}
                    </div>
                    <p className="min-h-[34px] text-[11px] leading-relaxed text-slate-500">{direction.description}</p>
                    <div className="my-2.5 flex flex-wrap gap-1.5 text-[10px]">
                      {direction.grainLabel && <span className="rounded bg-brand/8 px-1.5 py-1 font-semibold text-brand">每行：{direction.grainLabel}</span>}
                      {direction.estimatedRows != null && <span className="rounded bg-slate-100 px-1.5 py-1 text-slate-600">全量预计 {direction.estimatedRows.toLocaleString("zh-CN")} 行</span>}
                    </div>
                    <TablePreview preview={direction.preview} compact />
                    <div className="mt-2.5 flex items-center justify-between gap-2">
                      <span className="truncate text-[10px] text-slate-400">输出：{direction.outputs.join("、")}</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 shrink-0 px-2 text-[10px]"
                        onClick={(event) => {
                          event.stopPropagation();
                          void expand(direction);
                        }}
                      >
                        <Maximize2 className="h-3.5 w-3.5" />
                        展开预览
                      </Button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          <div className="mt-4 border-t border-slate-100 bg-brand/[0.025] px-3 py-3">
            <div className="mb-2 text-[13px] font-bold text-brand-dark">推荐不合适？直接描述你想要的表</div>
            <div className="flex items-end gap-2">
              <textarea
                aria-label="自然语言目标表描述"
                value={customPrompt}
                onChange={(event) => onCustomPromptChange?.(event.target.value)}
                placeholder="例如：只保留符合条件的记录，新增计算列，并按指定字段从高到低排序"
                className="min-h-[72px] flex-1 resize-none rounded-lg border border-brand/30 bg-white px-3 py-2.5 text-sm leading-relaxed text-slate-700 outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
              />
              <Button variant="brand" disabled={!canContinue || submitting} onClick={onSubmitDirection}>
                {submitting ? "正在生成并验证" : "生成方案"}
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
              </Button>
            </div>
          </div>
        </div>

        <aside className="min-w-0 bg-slate-50/60 p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-brand" />
            <h3 className="text-sm font-bold text-brand-dark">数据可执行性</h3>
          </div>
          <div className="mt-3 border-l-2 border-brand bg-white px-3 py-2.5">
            <strong className="text-xs text-brand-dark">{readiness?.label ?? "已识别源表结构"}</strong>
            {(readiness?.reasons ?? ["方案基于真实字段类型和数据分布生成。"] ).map((reason) => (
              <p key={reason} className="mt-1 text-[11px] leading-relaxed text-slate-600">{reason}</p>
            ))}
          </div>

          {dataset.planningMode === "basic" && (
            <div className="mt-3 border-l-2 border-amber-400 bg-amber-50 px-3 py-2.5 text-[10px] leading-relaxed text-amber-800">
              <strong className="block text-[11px]">AI 建模当前未生效</strong>
              当前仅展示不推断业务含义的基础可执行方案。{dataset.planningNotice ? ` ${dataset.planningNotice}` : ""}
            </div>
          )}

          <section className="mt-5">
            <strong className="text-xs text-brand-dark">数据质量诊断</strong>
            <div className="mt-2 grid gap-2">
              {dataset.qualityFindings?.length ? dataset.qualityFindings.slice(0, 5).map((finding, index) => (
                <div key={finding.message + index} className={cn(
                  "border-l-2 px-2 py-1.5 text-[10px] leading-relaxed",
                  finding.severity === "blocking" ? "border-red-400 bg-red-50 text-red-700" : "border-amber-400 bg-amber-50 text-amber-800",
                )}>
                  {finding.message}
                </div>
              )) : (
                <p className="border-b border-slate-200 pb-2 text-[11px] leading-relaxed text-slate-600">
                  未发现会直接阻断当前制表的字段质量问题。
                </p>
              )}
              {readiness?.warnings?.slice(0, 3).map((warning) => (
                <div key={warning} className="border-l-2 border-amber-400 bg-amber-50 px-2 py-1.5 text-[10px] text-amber-800">{warning}</div>
              ))}
            </div>
          </section>

          {selected && (
            <section className="mt-5">
              <strong className="text-xs text-brand-dark">当前方案依据</strong>
              <div className="mt-2 grid gap-2">
                {(selected.evidence ?? []).slice(0, 5).map((evidence) => (
                  <div key={evidence} className="border-b border-slate-200 pb-2 text-[11px] leading-relaxed text-slate-600">{evidence}</div>
                ))}
                {selected.warnings?.map((warning) => (
                  <div key={warning} className="border-l-2 border-amber-400 bg-amber-50 px-2 py-1.5 text-[10px] text-amber-800">{warning}</div>
                ))}
              </div>
            </section>
          )}

          <details className="group mt-5 border-t border-slate-200 pt-3">
            <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-bold text-brand-dark">
              识别字段依据
              <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-2 max-h-[260px] overflow-auto">
              {fieldRoles.map((row) => (
                <div key={row.rawField} className="grid min-h-[38px] grid-cols-[minmax(90px,1fr)_18px_minmax(90px,1fr)] items-center gap-1 border-b border-slate-100 text-[10px]">
                  <span className="truncate text-slate-600" title={row.rawField}>{row.rawField}</span>
                  <span className="text-center text-slate-400">→</span>
                  <span className="truncate font-semibold text-brand">{row.role}</span>
                </div>
              ))}
            </div>
          </details>

          <div className="mt-5 flex justify-end">
            <Button variant="brand" disabled={!selectedDirectionId || submitting} onClick={onSubmitDirection}>
              {submitting ? "正在验证预览" : "使用选中方向"}
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            </Button>
          </div>
        </aside>
      </div>

      <Dialog open={expandedId !== null} onOpenChange={(open) => !open && setExpandedId(null)}>
        <DialogContent className="w-[calc(100vw-2rem)] max-w-[1320px] rounded-lg p-0">
          <DialogHeader className="border-b border-slate-200 px-5 py-4">
            <DialogTitle>{directions.find((direction) => direction.id === expandedId)?.title ?? "目标表预览"}</DialogTitle>
            <DialogDescription>最多展示100行代表性计算结果。最终成品仍会重新读取并校验全部源数据。</DialogDescription>
          </DialogHeader>
          <div className="min-w-0 w-full p-5">
            <TablePreview preview={expandedPreview} loading={previewLoading} />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

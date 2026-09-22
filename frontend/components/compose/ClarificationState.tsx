"use client";

import * as React from "react";
import { AlertCircle, ArrowLeft, ArrowRight, Bot, ChevronDown, Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ComposeTablePreview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ComposeBadge } from "./ComposeBadge";
import { DatasetSummaryBar } from "./DatasetSummaryBar";
import { TablePreview } from "./TablePreview";
import type { ChatMessage, DatasetSummary, StructuredPlan } from "./presentation-types";

export function ComposeChat({
  messages,
  round,
  busy = false,
  onSendMessage,
  onSelectQuickOption,
}: {
  messages: ChatMessage[];
  round: number;
  busy?: boolean;
  onSendMessage?: (text: string, optionId?: string) => void;
  onSelectQuickOption?: (messageId: string, optionId: string) => void;
}) {
  const [draft, setDraft] = React.useState("");
  const logRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages, busy]);

  function handleSend() {
    const text = draft.trim();
    if (!text || busy) return;
    onSendMessage?.(text);
    setDraft("");
  }

  return (
    <aside id="compose-clarification-chat" className="flex min-h-[560px] min-w-0 flex-col bg-slate-50/60 xl:min-h-0">
      <div className="flex h-[58px] items-center justify-between border-b border-slate-100 px-4">
        <div>
          <strong className="text-[13px] text-brand-dark">用普通语言调整方案</strong>
          <span className="block text-[10px] text-slate-400">每次修改都会重新计算目标表预览</span>
        </div>
        <ComposeBadge variant="neutral">第 {round} 轮</ComposeBadge>
      </div>

      <div ref={logRef} className="min-h-0 flex-1 overflow-auto p-4">
        {messages.map((message) => (
          <div key={message.id}>
            <div className={cn("mb-3 flex gap-2", message.role === "user" && "justify-end")}>
              {message.role === "assistant" && (
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand text-white">
                  <Bot className="h-3.5 w-3.5" aria-hidden />
                </span>
              )}
              <div className={cn(
                "max-w-[86%] rounded-lg border px-2.5 py-2 text-[11px] leading-relaxed",
                message.role === "assistant" ? "border-slate-200 bg-white text-slate-600" : "border-brand/20 bg-brand/5 text-brand/90",
              )}>
                {message.content}
                {message.evidence && (
                  <span className="mt-2 block border-t border-slate-100 pt-2 text-[10px] leading-relaxed text-slate-400">
                    数据依据：{message.evidence}
                  </span>
                )}
              </div>
            </div>

            {!!message.quickOptions?.length && (
              <div className="mb-4 ml-9 grid gap-1.5">
                {message.quickOptions.map((option) => {
                  const selected = message.selectedOptionId === option.id;
                  return (
                    <button
                      key={option.id}
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        onSelectQuickOption?.(message.id, option.id);
                        onSendMessage?.(option.message ?? option.label, option.id);
                      }}
                      className={cn(
                        "flex min-h-9 items-center gap-2 rounded-md border px-2.5 text-left text-[11px] transition-colors disabled:opacity-50",
                        selected ? "border-brand bg-brand/[0.02] text-brand" : "border-slate-200 bg-white text-slate-600 hover:border-brand/30",
                      )}
                    >
                      <span className={cn("h-3.5 w-3.5 shrink-0 rounded-full border", selected ? "border-[4px] border-brand" : "border-slate-300")} />
                      <span className="min-w-0 flex-1">{option.label}</span>
                      {option.recommended && <span className="shrink-0 text-[9px] font-bold text-brand">推荐</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="ml-9 flex items-center gap-2 py-2 text-[11px] text-brand">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            正在理解修改并重新验证预览
          </div>
        )}
      </div>

      <div className="border-t border-slate-100 bg-white p-3">
        <div className="flex items-center gap-1.5">
          <input
            aria-label="补充制表要求"
            value={draft}
            disabled={busy}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                handleSend();
              }
            }}
            placeholder="例如：改成每个客户一行，并去掉月份"
            className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:bg-slate-50"
          />
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={handleSend}
                disabled={!draft.trim() || busy}
                aria-label="发送"
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-white disabled:opacity-40"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </button>
            </TooltipTrigger>
            <TooltipContent>发送并更新预览</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </aside>
  );
}

export function ClarificationState({
  dataset,
  plan,
  preview,
  previewLoading = false,
  planUpdating = false,
  messages,
  chatRound,
  onBackToDirections,
  onExecutePlan,
  onSendMessage,
  onSelectQuickOption,
}: {
  dataset: DatasetSummary;
  plan: StructuredPlan;
  preview?: ComposeTablePreview | null;
  previewLoading?: boolean;
  planUpdating?: boolean;
  messages: ChatMessage[];
  chatRound: number;
  onBackToDirections?: () => void;
  onExecutePlan?: () => void;
  onSendMessage?: (text: string, optionId?: string) => void;
  onSelectQuickOption?: (messageId: string, optionId: string) => void;
}) {
  const readyForExecution = plan.readyForExecution !== false;
  const blockingCount = plan.blockingQuestions?.length ?? (readyForExecution ? 0 : plan.pendingQuestionCount);
  const grainOptions = [
    { value: null, label: "每个对象一行", message: "每个汇总对象一行，不按时间拆分" },
    { value: "month", label: "按月", message: "按月份拆分，每个对象每月一行" },
    { value: "quarter", label: "按季度", message: "按季度拆分，每个对象每季度一行" },
    { value: "year", label: "按年", message: "按年度拆分，每个对象每年一行" },
  ] as const;

  return (
    <div className="overflow-hidden rounded-xl border border-slate-100 bg-white shadow-apple">
      <DatasetSummaryBar dataset={dataset} mode="plan" />
      <div className="grid min-h-[690px] w-full grid-cols-1 xl:grid-cols-[minmax(0,1.55fr)_minmax(340px,0.55fr)]">
        <div className="min-w-0 border-b border-slate-100 p-5 xl:border-b-0 xl:border-r">
          <div className="mb-3 flex items-start justify-between gap-4">
            <div>
              <h3 className="text-[15px] font-bold text-brand-dark">{plan.title}</h3>
              <p className="text-xs text-slate-500">先看结果长什么样，再确认业务口径和全量生成。</p>
            </div>
            <ComposeBadge variant={readyForExecution ? "green" : "amber"}>
              {readyForExecution ? "可以全量生成" : `还需确认 ${Math.max(1, blockingCount)} 项`}
            </ComposeBadge>
          </div>

          {plan.requirementSummary && (
            <div className={cn(
              "mb-3 border-l-2 px-3 py-2.5",
              plan.intentConfirmationPending ? "border-amber-400 bg-amber-50" : "border-brand bg-brand/[0.035]",
            )}>
              <div className="flex items-center justify-between gap-3">
                <strong className="text-xs text-brand-dark">系统理解的目标表</strong>
                {plan.intentConfirmationPending && <ComposeBadge variant="amber">等待你确认</ComposeBadge>}
              </div>
              <p className="mt-1.5 whitespace-pre-line text-[11px] leading-relaxed text-slate-700">{plan.requirementSummary}</p>
            </div>
          )}

          {plan.modelNotice && (
            <div className="mb-3 flex gap-2 border border-red-200 bg-red-50 px-3 py-2 text-[10px] leading-relaxed text-red-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {plan.modelNotice}
            </div>
          )}

          {!plan.tableMode && <div className="mb-3 flex flex-wrap items-center gap-1.5 border-y border-slate-100 py-2.5">
            <span className="mr-1 text-[11px] font-bold text-brand-dark">每行代表</span>
            {grainOptions.map((option) => (
              <button
                key={option.label}
                type="button"
                disabled={planUpdating}
                onClick={() => onSendMessage?.(option.message)}
                className={cn(
                  "h-8 rounded-md border px-2.5 text-[10px] font-semibold transition-colors disabled:opacity-50",
                  plan.timeGrain === option.value ? "border-brand bg-brand/8 text-brand" : "border-slate-200 bg-white text-slate-500 hover:border-brand/30",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>}

          <TablePreview preview={preview} loading={previewLoading || planUpdating} />

          <div className="mt-3 grid grid-cols-3 border-y border-slate-100 text-[11px]">
            <div className="min-w-0 border-r border-slate-100 px-3 py-2.5">
              <span className="block text-slate-400">行粒度</span>
              <strong className="mt-1 block truncate text-brand-dark">{plan.grainLabel || "全部数据一行"}</strong>
            </div>
            <div className="min-w-0 border-r border-slate-100 px-3 py-2.5">
              <span className="block text-slate-400">分组字段</span>
              <strong className="mt-1 block truncate text-brand-dark" title={plan.dimensionLabels?.join("、")}>{plan.dimensionLabels?.join("、") || "无额外分组"}</strong>
            </div>
            <div className="min-w-0 px-3 py-2.5">
              <span className="block text-slate-400">结果指标</span>
              <strong className="mt-1 block truncate text-brand-dark" title={plan.metricLabels?.join("、")}>{plan.metricLabels?.join("、") || `${plan.metricCount} 项指标`}</strong>
            </div>
          </div>

          {!readyForExecution && (
            <div className="mt-3 flex gap-2.5 border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11px] text-amber-900">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div>
                <strong className="block text-xs">这是业务信息待确认，不是系统故障</strong>
                <span className="mt-1 block leading-relaxed">{plan.blockingQuestions?.[0] ?? "请在右侧选择建议答案或用普通语言补充。"}</span>
              </div>
            </div>
          )}

          <details className="group mt-3 border-t border-slate-100 pt-3">
            <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-bold text-brand-dark">
              高级规则与输出工作表
              <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-3 grid gap-3 text-[10px]">
              <div>
                <strong className="text-slate-600">输出工作表</strong>
                {plan.outputSheets.map((sheet) => (
                  <div key={sheet.name} className="mt-1 grid grid-cols-[minmax(130px,1fr)_minmax(0,2fr)_80px] gap-2 border-b border-slate-100 py-1.5 text-slate-500">
                    <span className="font-semibold text-slate-700">{sheet.name}</span>
                    <span className="truncate" title={sheet.fields}>{sheet.fields || "无额外分组"}</span>
                    <span>{sheet.columnCount}</span>
                  </div>
                ))}
              </div>
              <div>
                <strong className="text-slate-600">计算规则</strong>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {plan.formulas.map((formula) => (
                    <span key={formula.label} className="border border-slate-200 bg-slate-50 px-2 py-1.5 text-slate-600">
                      <b className="text-brand-dark">{formula.label}</b>：{formula.definition}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </details>

          <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-100 pt-3.5">
            <p className="text-[11px] leading-relaxed text-slate-500">
              <strong className="text-brand">确认后重新读取全量数据</strong><br />
              {plan.footerNote}
            </p>
            <div className="flex shrink-0 gap-2">
              <Button variant="ghost" disabled={planUpdating} onClick={onBackToDirections}>
                <ArrowLeft className="h-4 w-4" />更换方向
              </Button>
              <Button variant={readyForExecution ? "brand" : "amber"} disabled={planUpdating} onClick={onExecutePlan}>
                {readyForExecution ? "按此方案全量生成" : `先确认 ${Math.max(1, blockingCount)} 项口径`}
                <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>

        <ComposeChat
          messages={messages}
          round={chatRound}
          busy={planUpdating}
          onSendMessage={onSendMessage}
          onSelectQuickOption={onSelectQuickOption}
        />
      </div>
    </div>
  );
}

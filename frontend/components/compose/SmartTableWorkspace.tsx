"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { AlertCircle, ArrowLeft, Loader2, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/Toast";
import { UploadState } from "./UploadState";
import { DirectionState } from "./DirectionState";
import { ClarificationState } from "./ClarificationState";
import { ResultState } from "./ResultState";
import {
  type ChatMessage,
  type ComposePhase,
  type ComposePreviewStep,
  type ComposeSourceFile,
  PREVIEW_CHAT_MESSAGES,
  PREVIEW_DATASET_DIRECTION,
  PREVIEW_DATASET_PLAN,
  PREVIEW_DATASET_RESULT,
  PREVIEW_DIRECTIONS,
  PREVIEW_FIELD_ROLES,
  PREVIEW_PLAN,
  PREVIEW_QUALITY,
  PREVIEW_RESULT_TABS,
  PREVIEW_SOURCE_FILES,
  phaseToPreviewStep,
} from "./presentation-types";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { humanizeError } from "@/lib/errors";
import type {
  ComposeDirection,
  ComposeExecuteSuccess,
  ComposeExpression,
  ComposePlan,
  ComposeQuality,
  ComposeResultPage,
  ComposeSourcePreview,
  ComposeTablePreview,
} from "@/lib/types";
import type {
  DatasetSummary,
  DirectionOption,
  FieldRoleMapping,
  QualitySummaryData,
  ResultSheetTab,
  ResultTableRow,
} from "./presentation-types";

const PREVIEW_STEPS: { id: ComposePreviewStep; label: string }[] = [
  { id: "upload", label: "1 上传源表" },
  { id: "direction", label: "2 推荐方向" },
  { id: "clarify", label: "3 确认口径" },
  { id: "result", label: "4 结果确认" },
];

function previewStepToPhase(step: ComposePreviewStep): ComposePhase {
  switch (step) {
    case "upload":
      return "upload";
    case "direction":
      return "directions";
    case "clarify":
      return "clarifying";
    case "result":
      return "review";
    default:
      return "upload";
  }
}

function PreviewSmartTableWorkspace() {
  const searchParams = useSearchParams();
  const toast = useToast();

  const initialStep = (searchParams.get("state") as ComposePreviewStep | null) ?? "upload";
  const validInitial =
    PREVIEW_STEPS.some((s) => s.id === initialStep) ? initialStep : "upload";

  const [phase, setPhase] = React.useState<ComposePhase>(
    previewStepToPhase(validInitial),
  );
  const [previewMode, setPreviewMode] = React.useState(true);
  const [files, setFiles] = React.useState<ComposeSourceFile[]>(
    validInitial === "upload" ? [] : PREVIEW_SOURCE_FILES,
  );
  const [selectedDirectionId, setSelectedDirectionId] = React.useState<string | null>(
    "d1",
  );
  const [customPrompt, setCustomPrompt] = React.useState("");
  const [chatMessages, setChatMessages] =
    React.useState<ChatMessage[]>(PREVIEW_CHAT_MESSAGES);
  const [chatRound, setChatRound] = React.useState(2);
  const [activeResultTabId, setActiveResultTabId] = React.useState("customer");
  const [resultPage, setResultPage] = React.useState(1);
  const [completed, setCompleted] = React.useState(false);
  const [profilingProgress, setProfilingProgress] = React.useState<
    { label: string; percent: number } | undefined
  >();
  const [executingProgress, setExecutingProgress] = React.useState<
    { label: string; processed: number; total: number } | undefined
  >();
  const [errorMessage, setErrorMessage] = React.useState<string>();

  const previewStep = phaseToPreviewStep(phase);

  function goToStep(step: ComposePreviewStep) {
    setPhase(previewStepToPhase(step));
    setErrorMessage(undefined);
    if (step !== "upload" && files.length === 0) {
      setFiles(PREVIEW_SOURCE_FILES);
    }
  }

  function handleFilesAdd(fileList: FileList) {
    const added: ComposeSourceFile[] = Array.from(fileList).map((f, i) => ({
      id: `local-${Date.now()}-${i}`,
      name: f.name,
      sheetCount: 0,
      rowCount: 0,
      status: "pending" as const,
    }));
    setFiles((prev) => [...prev, ...added]);
    // 本地预览：模拟扫描完成
    setTimeout(() => {
      setFiles((prev) =>
        prev.map((f) =>
          added.some((a) => a.id === f.id)
            ? {
                ...f,
                sheetCount: 5,
                rowCount: Math.floor(250000 + Math.random() * 60000),
                status: "ready",
              }
            : f,
        ),
      );
    }, 600);
  }

  function handleStartProfiling() {
    if (files.filter((f) => f.status === "ready").length === 0) return;
    setPhase("profiling");
    setProfilingProgress({ label: "扫描文件结构与 Sheet", percent: 12 });
    const timer = setInterval(() => {
      setProfilingProgress((p) => {
        if (!p) return p;
        const next = Math.min(p.percent + 18, 100);
        if (next >= 100) {
          clearInterval(timer);
          setTimeout(() => {
            setProfilingProgress(undefined);
            setPhase("directions");
          }, 400);
        }
        return {
          label:
            next < 50
              ? "扫描文件结构与 Sheet"
              : next < 85
                ? "分层抽样与字段画像"
                : "生成推荐方向",
          percent: next,
        };
      });
    }, 350);
  }

  function handleSubmitDirection() {
    setPhase("clarifying");
  }

  function handleExecutePlan() {
    setPhase("executing");
    const total = PREVIEW_DATASET_RESULT.plannedRows;
    setExecutingProgress({
      label: "全量读取销售流水",
      processed: 0,
      total,
    });
    let processed = 0;
    const timer = setInterval(() => {
      processed = Math.min(processed + Math.floor(total * 0.12), total);
      setExecutingProgress({
        label:
          processed < total * 0.4
            ? "全量读取销售流水"
            : processed < total * 0.8
              ? "聚合客户汇总与分类"
              : "执行金额对账",
        processed,
        total,
      });
      if (processed >= total) {
        clearInterval(timer);
        setTimeout(() => {
          setExecutingProgress(undefined);
          setPhase("review");
        }, 500);
      }
    }, 280);
  }

  function handleSendMessage(text: string) {
    setChatMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", content: text },
    ]);
    setChatRound((r) => r + 1);
    setTimeout(() => {
      setChatMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          content: "已记录你的补充要求，方案已更新。",
        },
      ]);
    }, 500);
  }

  function handleSelectQuickOption(messageId: string, optionId: string) {
    setChatMessages((prev) =>
      prev.map((m) =>
        m.id === messageId ? { ...m, selectedOptionId: optionId } : m,
      ),
    );
  }

  function handleCompleteTable() {
    if (!PREVIEW_QUALITY.canComplete) return;
    setPhase("completing");
    setTimeout(() => {
      setCompleted(true);
      setPhase("completed");
      toast.success(
        "目标表已完成",
        "转换逻辑已存入「我的逻辑库」",
      );
    }, 600);
  }

  function handleRetry() {
    setErrorMessage(undefined);
    setPhase("upload");
  }

  return (
    <>
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-[#F3F5F7]">
        <div className="mx-auto flex w-full max-w-[1500px] items-start justify-between gap-6 px-8 pb-4 pt-9">
          <div>
            <h2 className="mb-1 text-[26px] font-bold tracking-tight text-brand-dark">
              智能制表
            </h2>
            <p className="text-sm font-medium text-slate-500">
              没有目标样例，也能从源数据制作可复用的结果表
            </p>
          </div>
          {previewMode && (
            <div className="flex flex-col items-end gap-2 pt-2">
              <span className="flex items-center gap-1.5 text-[11px] font-semibold text-amber-700">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                布局预览 · 金额和客户数为示意数据
              </span>
              <div
                className="flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-1 shadow-sm"
                aria-label="预览页面状态"
              >
                {PREVIEW_STEPS.map((step) => (
                  <button
                    key={step.id}
                    type="button"
                    onClick={() => goToStep(step.id)}
                    className={cn(
                      "h-[30px] whitespace-nowrap rounded-md px-2.5 text-xs font-semibold transition-colors",
                      previewStep === step.id
                        ? "bg-brand-dark text-white"
                        : "text-slate-500 hover:bg-slate-50 hover:text-brand-dark",
                    )}
                  >
                    {step.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </header>

      <div className="mx-auto w-full max-w-[1500px] px-8 pb-8">
        {phase === "error" && errorMessage && (
          <div className="mb-4 flex items-center justify-between rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <span className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4" />
              {errorMessage}
            </span>
            <Button variant="ghost" size="sm" onClick={handleRetry}>
              <RotateCw className="h-3.5 w-3.5" />
              重试
            </Button>
          </div>
        )}

        {(phase === "upload" || phase === "profiling") && (
          <UploadState
            files={files.length > 0 ? files : phase === "profiling" ? PREVIEW_SOURCE_FILES : files}
            profiling={phase === "profiling"}
            profilingProgress={profilingProgress}
            onFilesAdd={handleFilesAdd}
            onFileRemove={(id) =>
              setFiles((prev) => prev.filter((f) => f.id !== id))
            }
            onStartProfiling={handleStartProfiling}
            onCancelProfiling={() => {
              setPhase("upload");
              setProfilingProgress(undefined);
            }}
          />
        )}

        {phase === "directions" && (
          <DirectionState
            dataset={PREVIEW_DATASET_DIRECTION}
            directions={PREVIEW_DIRECTIONS}
            selectedDirectionId={selectedDirectionId}
            customPrompt={customPrompt}
            fieldRoles={PREVIEW_FIELD_ROLES}
            onSelectDirection={setSelectedDirectionId}
            onCustomPromptChange={setCustomPrompt}
            onSubmitDirection={handleSubmitDirection}
          />
        )}

        {phase === "clarifying" && (
          <ClarificationState
            dataset={PREVIEW_DATASET_PLAN}
            plan={PREVIEW_PLAN}
            messages={chatMessages}
            chatRound={chatRound}
            onBackToDirections={() => setPhase("directions")}
            onExecutePlan={handleExecutePlan}
            onSendMessage={handleSendMessage}
            onSelectQuickOption={handleSelectQuickOption}
          />
        )}

        {(phase === "executing" || phase === "review" || phase === "completing" || phase === "completed") && (
          <ResultState
            dataset={PREVIEW_DATASET_RESULT}
            quality={PREVIEW_QUALITY}
            tabs={PREVIEW_RESULT_TABS}
            activeTabId={activeResultTabId}
            page={resultPage}
            completed={completed}
            executing={phase === "executing"}
            executingProgress={executingProgress}
            onTabChange={(id) => {
              setActiveResultTabId(id);
              setResultPage(1);
            }}
            onPageChange={setResultPage}
            onDownloadExcel={() =>
              toast.info("下载 Excel", "预览模式：导出接口待 Codex 接线")
            }
            onAdjustPlan={() => setPhase("clarifying")}
            onCompleteTable={handleCompleteTable}
            onClassificationAction={() =>
              toast.info("分类修正", "预览模式：分类修正接口待 Codex 接线")
            }
          />
        )}
      </div>
    </>
  );
}

const ROLE_LABELS: Record<string, string> = {
  entity_id: "客户稳定编号",
  entity_name: "客户名称",
  date: "交易日期",
  amount: "金额",
  transaction_type: "交易类型",
  document_id: "单据编号",
  department: "部门",
  salesperson: "业务员",
  region: "地区",
  dimension: "可用维度",
};

function liveDatasetSummary(
  dataset: NonNullable<ComposeResultPage["dataset"]>,
  mode: "direction" | "result",
): DatasetSummary {
  const isResult = mode === "result";
  const planningLabel = dataset.planning?.source === "model" || dataset.planning?.source === "hybrid"
    ? "AI 基于全量画像建模"
    : "基础规则模式";
  return {
    title: isResult ? "智能制表结果" : "源数据集",
    subtitle: `${dataset.file_count} 个文件 · ${dataset.sheet_count} 个 Sheet · ${dataset.schema.columns.length} 个字段${!isResult && dataset.planning ? ` · ${planningLabel}` : ""}`,
    fileCount: dataset.file_count,
    sheetCount: dataset.sheet_count,
    plannedRows: dataset.total_rows,
    fieldCount: isResult ? dataset.valid_rows ?? dataset.total_rows : dataset.schema.columns.length,
    scopeLabel: isResult ? "结果基于全量数据" : "字段统计基于全量数据",
    scopeVariant: isResult ? "full" : "plan",
    planningMode: dataset.planning?.source,
    planningNotice: dataset.planning?.notice,
    qualityFindings: dataset.quality_findings?.map((finding) => ({
      severity: finding.severity,
      message: finding.message,
    })),
  };
}

function liveDirectionSummary(dataset: NonNullable<ComposeResultPage["dataset"]>): DatasetSummary {
  return liveDatasetSummary(dataset, "direction");
}

function livePlanSummary(
  dataset: NonNullable<ComposeResultPage["dataset"]>,
  plan: ComposePlan,
): DatasetSummary {
  return {
    title: plan.direction_title,
    subtitle: "当前方案随对话实时更新",
    fileCount: plan.outputs.length,
    sheetCount: plan.outputs.reduce((total, output) => total + output.metrics.length, 0),
    plannedRows: plan.confirmed_facts.length,
    fieldCount: plan.questions.length,
    scopeLabel: `确认后执行全量 ${dataset.total_rows.toLocaleString("zh-CN")} 行`,
    scopeVariant: "plan",
  };
}

function liveFieldRoles(dataset: NonNullable<ComposeResultPage["dataset"]>): FieldRoleMapping[] {
  return (dataset.fields ?? []).map((field) => ({
    rawField: field.name,
    role: ROLE_LABELS[field.role] ?? field.role,
  }));
}

function liveDirections(directions: ComposeDirection[]): DirectionOption[] {
  return directions.map((direction) => ({
    id: direction.id,
    title: direction.title,
    description: direction.description,
    outputs: direction.outputs,
    assessment: direction.assessment ? {
      dataEvidence: direction.assessment.data_evidence === 'strong' ? '数据依据充分' : '数据依据有限',
      executability: direction.assessment.executability === 'executable' ? '可完整执行' : '需要澄清',
      businessFit: direction.assessment.business_fit === 'confirmed' ? '业务已确认' : '业务待确认',
    } : undefined,
    grainLabel: direction.grain_label,
    estimatedRows: direction.estimated_rows,
    evidence: direction.evidence_notes?.length
      ? direction.evidence_notes
      : direction.evidence.map((field) => `使用真实字段「${field}」`),
    warnings: direction.warnings,
    preview: direction.preview,
  }));
}

const EXPRESSION_OPERATORS: Record<string, string> = {
  add: " + ",
  subtract: " - ",
  multiply: " × ",
  divide: " ÷ ",
};

function expressionText(expression?: ComposeExpression): string {
  if (!expression) return "按记录计数";
  if (expression.op === "field") return `字段「${expression.field ?? "未指定"}」`;
  if (expression.op === "literal") return String(expression.value ?? "");
  if (expression.op === "if") {
    const condition = expression.condition;
    return `若 ${condition?.field ?? "字段"} ${condition?.operator ?? ""} ${String(condition?.value ?? "")}，则 ${expressionText(expression.then)}，否则 ${expressionText(expression.else)}`;
  }
  if (expression.op === "coalesce") return `优先取非空值（${(expression.args ?? []).map(expressionText).join("、")}）`;
  if (expression.op === "concat") return `拼接（${(expression.args ?? []).map(expressionText).join(expression.separator ?? "")}）`;
  if (expression.op === "abs") return `绝对值（${expressionText(expression.args?.[0])}）`;
  if (expression.op === "negate") return `取负（${expressionText(expression.args?.[0])}）`;
  const separator = EXPRESSION_OPERATORS[expression.op] ?? ` ${expression.op} `;
  return `（${(expression.args ?? []).map(expressionText).join(separator)}）`;
}

function livePlan(plan: ComposePlan): import("./presentation-types").StructuredPlan {
  if (plan.output_kind === "table" && plan.table) {
    const table = plan.table;
    const outputFields = table.columns?.map((column) => column.name)
      ?? [
        ...(table.group_by ?? []).map((item) => item.name || item.field),
        ...(table.aggregations ?? []).map((item) => item.name),
      ];
    const formulas = [
      ...(table.filters.length
        ? [{
            label: "保留记录",
            definition: table.filters.map((item) => `${item.field} ${item.operator} ${String(item.value ?? "")}`).join("；"),
          }]
        : []),
      ...(table.mode === "detail"
        ? (table.columns ?? []).map((column) => ({
            label: column.name,
            definition: expressionText(column.expression),
          }))
        : [
            ...(table.aggregations ?? []).map((item) => ({
              label: item.name,
              definition: `${expressionText(item.expression)} · ${item.aggregation}`,
            })),
            ...(table.columns ?? []).map((column) => ({
              label: column.name,
              definition: expressionText(column.expression),
            })),
          ]),
    ];
    const groupLabels = (table.group_by ?? []).map((item) => item.name || item.field);
    return {
      title: plan.direction_title,
      subtitle: "受控方案已通过字段与公式校验，预览来自真实源数据",
      outputSheets: [{
        name: table.output_name,
        fields: outputFields.join("、"),
        columnCount: `${outputFields.length} 列`,
        confirmed: plan.intent_confirmation?.confirmed !== false,
      }],
      formulas: formulas.slice(0, 16),
      classifications: [],
      confirmedCount: plan.confirmed_facts.length,
      pendingQuestionCount: plan.questions.length,
      metricCount: outputFields.length,
      footerNote: plan.ready_for_execution
        ? "当前方案已通过受控计划编译与字段校验。"
        : "请先确认需求理解，并补齐会改变结果的业务口径。",
      readyForExecution: plan.ready_for_execution,
      blockingQuestions: plan.questions.filter((question) => question.blocking).map((question) => question.text),
      modelNotice: plan.model_notice,
      grainLabel: table.mode === "detail"
        ? "每条符合条件的源记录"
        : (groupLabels.length ? groupLabels.join(" + ") : "全部数据一行"),
      dimensionLabels: groupLabels,
      metricLabels: outputFields,
      requirementSummary: plan.requirement_summary,
      intentConfirmationPending: plan.intent_confirmation?.required === true && plan.intent_confirmation.confirmed === false,
      tableMode: table.mode,
    };
  }
  const metricLabels = plan.output_kind === "generic_summary"
    ? [...(plan.measures ?? []).map((measure) => measure.name), "记录数"]
    : ["销售单数", "退货单数", "销售金额", "退货金额", "净销售额", "最近交易日期"];
  const signSource = plan.business_rules.transaction_sign_source === "transaction_type"
    ? `按「${plan.role_bindings.transaction_type ?? "交易类型"}」中的确认编码识别`
    : "按金额正负识别：正数为销售，负数为退货";
  const zeroPolicy = plan.business_rules.zero_amount_policy === "count_documents"
    ? "零金额按已确认交易类型计入单数"
    : "零金额不计入销售或退货单数，并在质量报告中单列";
  const formulas = plan.output_kind === "generic_summary"
    ? (plan.measures ?? []).map((measure) => ({
        label: measure.name,
        definition: `${measure.field} · ${{ sum: "求和", average: "平均值", min: "最小值", max: "最大值" }[measure.aggregation] ?? measure.aggregation}`,
      }))
    : [
        { label: "销售与退货", definition: signSource },
        { label: "净销售额", definition: "销售金额减去退货金额" },
        { label: "单据数", definition: "有单据编号时按编号去重" },
        { label: "零金额", definition: zeroPolicy },
      ];
  const grainLabel = plan.output_kind === "generic_summary"
    ? ([plan.time_grain ? { year: "年度", quarter: "季度", month: "月份" }[plan.time_grain] : null, ...plan.dimensions].filter(Boolean).join(" + ") || "全部数据一行")
    : `每个客户或往来单位${plan.time_grain ? `每${{ year: "年", quarter: "季度", month: "月" }[plan.time_grain]}` : "一行"}`;
  return {
    title: plan.direction_title,
    subtitle: "方案已编译为受控规则，并用真实代表性样本生成预览",
    outputSheets: plan.outputs.map((output) => ({
      name: output.name,
      fields: output.group_by.join("、"),
      columnCount: `${output.metrics.length} 项指标`,
      confirmed: true,
    })),
    formulas,
    classifications: plan.classification.enabled
      ? plan.classification.rules.map((rule) => ({
          label: rule.label,
          value: rule.conditions?.map((condition) => `${condition.field} ${condition.operator} ${condition.values.join("/")}`).join("；")
            ?? rule.keywords?.join("、")
            ?? "待确认规则",
        }))
      : [{ label: "当前方案", value: "未启用分类" }],
    confirmedCount: plan.confirmed_facts.length,
    pendingQuestionCount: plan.questions.length,
    metricCount: plan.outputs.reduce((total, output) => total + output.metrics.length, 0),
    footerNote: plan.ready_for_execution
      ? "当前方案已通过字段、指标和分类规则校验。"
      : "请先补齐会改变结果的业务口径。",
    readyForExecution: plan.ready_for_execution,
    blockingQuestions: plan.questions
      .filter((question) => question.blocking)
      .map((question) => question.text),
    modelNotice: plan.model_notice,
    grainLabel,
    timeGrain: plan.time_grain,
    dimensionLabels: plan.dimensions,
    metricLabels,
    zeroAmountPolicy: zeroPolicy,
  };
}

function liveMessages(plan: ComposePlan): ChatMessage[] {
  const introduction: ChatMessage = {
    id: `assistant-${Date.now()}`,
    role: "assistant",
    content: plan.assistant_message ?? (plan.questions.length
      ? `我已根据全量字段画像生成「${plan.direction_title}」。下面的问题会改变最终结果，请确认后再生成。`
      : `我已生成「${plan.direction_title}」，当前方案可以直接进行全量确定性计算。你也可以继续修改。`),
  };
  return [
    introduction,
    ...plan.questions.map((question, index): ChatMessage => ({
      id: `question-${question.code}-${index}-${Date.now()}`,
      role: "assistant",
      content: question.text,
      evidence: question.evidence,
      quickOptions: question.options?.map((option) => ({
        id: option.id,
        label: option.label,
        message: option.message,
        recommended: option.recommended,
      })),
    })),
  ];
}

function resultKey(label: string, index: number): string {
  if (label === "往来单位编号") return "id";
  if (label === "往来单位名称") return "name";
  if (label === "客户终端类型" || label === "分类名称") return "type";
  if (label === "分类状态" || label === "置信状态") return "status";
  if (label === "用户确认类型") return "confirm";
  return `column-${index}`;
}

const MONEY_COLUMNS = new Set(["销售金额", "退货金额", "净销售额", "金额", "销售额", "退货额", "净额"]);
const PERCENT_COLUMNS = new Set(["净销售额占比", "客户数占比", "退货率", "占比"]);
const COUNT_COLUMNS = new Set(["销售单数", "退货单数", "客户数", "行数", "有效行数", "异常行数"]);

function displayResultValue(label: string, value: unknown): string | number {
  if (value == null || value === "") return "";
  if (MONEY_COLUMNS.has(label)) {
    const numeric = Number(value);
    return Number.isFinite(numeric)
      ? numeric.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : String(value);
  }
  if (PERCENT_COLUMNS.has(label)) {
    const numeric = Number(String(value).replace("%", ""));
    return Number.isFinite(numeric) ? `${numeric.toFixed(2)}%` : String(value);
  }
  if (COUNT_COLUMNS.has(label)) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toLocaleString("zh-CN") : String(value);
  }
  return typeof value === "number" ? value : String(value);
}

function resultTab(
  sheetName: string,
  columns: string[],
  rows: unknown[][],
  totalRows: number,
): ResultSheetTab {
  const id = sheetName.includes("待确认")
    ? "pending"
    : sheetName.includes("分类汇总") || sheetName.includes("终端")
      ? "classification"
      : sheetName.includes("客户")
        ? "customer"
        : "dimension";
  const priority = (label: string, index: number) => {
    if (label.includes("编号")) return 0;
    if (label.includes("名称") && !label.includes("分类")) return 1;
    if (label === "销售金额") return 2;
    if (label === "退货金额") return 3;
    if (label === "净销售额" || label.endsWith("合计")) return 4;
    if (label === "销售单数") return 5;
    if (label === "退货单数" || label === "记录数") return 6;
    if (label.includes("占比")) return 20;
    if (label.includes("日期")) return 21;
    return 10 + index / 1000;
  };
  const orderedIndexes = columns.map((_, index) => index).sort((left, right) => priority(columns[left], left) - priority(columns[right], right));
  const keyCounts = new Map<string, number>();
  const columnKeys = columns.map((label, index) => {
    const base = resultKey(label, index);
    const count = keyCounts.get(base) ?? 0;
    keyCounts.set(base, count + 1);
    return count === 0 ? base : `${base}-${count + 1}`;
  });
  const tabColumns = orderedIndexes.map((index) => {
    const label = columns[index];
    return {
    key: columnKeys[index],
    label,
    align: MONEY_COLUMNS.has(label) || PERCENT_COLUMNS.has(label) || COUNT_COLUMNS.has(label) ? "right" as const : "left" as const,
    width: label.includes("名称") || label.includes("依据") ? "190px" : label.includes("编号") ? "150px" : MONEY_COLUMNS.has(label) ? "130px" : "112px",
  }; });
  const tabRows: ResultTableRow[] = rows.map((values, rowIndex) => {
    const cells: Record<string, string | number> = {};
    columns.forEach((label, index) => {
      const value = values[index];
      cells[columnKeys[index]] = displayResultValue(label, value);
    });
    const statusIndex = columns.findIndex((label) => label === "分类状态" || label === "置信状态");
    const status = statusIndex >= 0 ? String(values[statusIndex] ?? "") : "";
    const actionIndex = columns.findIndex((label) => label === "用户确认类型");
    return {
      id: String(values[0] ?? rowIndex),
      cells,
      ...(statusIndex >= 0 ? { pill: { column: columnKeys[statusIndex], variant: status === "待确认" ? "amber" as const : "brand" as const } } : {}),
      ...(actionIndex >= 0 && status === "待确认" ? { action: { column: columnKeys[actionIndex], label: "选择类型" } } : {}),
    };
  });
  return {
    id,
    label: sheetName,
    countLabel: totalRows.toLocaleString("zh-CN"),
    caption: `全量结果 · ${totalRows.toLocaleString("zh-CN")} 行`,
    captionRight: id === "pending" ? "无法确认的分类不会静默归入其他" : "金额显示两位小数，底层按未舍入值对账",
    columns: tabColumns,
    rows: tabRows,
    totalRows,
    pageSize: 100,
    serverPaginated: true,
    live: true,
  };
}

function liveQuality(quality: ComposeQuality, dataset: NonNullable<ComposeResultPage["dataset"]>): QualitySummaryData {
  const reconciliationPassed = quality.reconciliations.every((item) => item.status === "passed");
  const rowsPassed = quality.processed_rows === quality.planned_rows && quality.invalid_rows === 0;
  const pending = quality.pending_classification_count;
  return {
    scope: "full",
    plannedRows: quality.planned_rows,
    processedRows: quality.processed_rows,
    validRows: quality.valid_rows,
    invalidRows: quality.invalid_rows,
    skippedRows: quality.skipped_rows,
    sourceFingerprint: quality.source_fingerprint.slice(0, 16),
    reconciliations: quality.reconciliations.map((item) => ({
      name: item.name,
      label: item.name,
      status: item.status === "passed" ? "passed" : "failed",
      delta: item.delta,
    })),
    canComplete: quality.can_complete,
    blockReason: quality.can_complete
      ? undefined
      : pending > 0
        ? `还有 ${pending} 个客户分类待确认。`
        : (quality.transaction_sign_conflict_rows ?? 0) > 0
          ? `有 ${quality.transaction_sign_conflict_rows} 行的交易类型与金额正负冲突。`
        : quality.invalid_rows > 0
          ? `有 ${quality.invalid_rows} 行无法纳入确定性计算。`
          : "金额或行数对账尚未通过。",
    headline: quality.can_complete ? "全量结果已生成，质量门禁通过" : "全量结果已生成，仍需处理质量问题",
    subline: `${quality.processed_rows.toLocaleString("zh-CN")} / ${quality.planned_rows.toLocaleString("zh-CN")} 行已读取 · ${quality.source_fingerprint.slice(0, 16)}`,
    facts: [
      { value: quality.planned_rows.toLocaleString("zh-CN"), label: "计划行数" },
      { value: quality.processed_rows.toLocaleString("zh-CN"), label: "实际读取" },
      { value: String(quality.invalid_rows), label: "异常行" },
      ...((quality.zero_amount_rows ?? 0) > 0 ? [{ value: (quality.zero_amount_rows ?? 0).toLocaleString("zh-CN"), label: "零值记录" }] : []),
    ],
    warnings: [
      ...(pending > 0 ? [`${pending} 个唯一客户暂未完成分类。`] : []),
      ...(quality.skipped_rows > 0 ? [`导入阶段跳过 ${quality.skipped_rows} 行空行或重复表头。`] : []),
      ...((quality.transaction_sign_conflict_rows ?? 0) > 0 ? [`${quality.transaction_sign_conflict_rows} 行交易方向交叉校验失败。`] : []),
      ...((quality.entity_name_conflict_count ?? 0) > 0 ? [`${quality.entity_name_conflict_count} 个实体编号对应多个名称，结果采用出现次数最多的名称。`] : []),
      ...((quality.zero_amount_rows ?? 0) > 0 ? [`${quality.zero_amount_rows} 条零金额或零值记录已按确认口径处理，并单独保留在质量审计中。`] : []),
      ...((quality.measure_invalid_cells ?? 0) > 0 ? [`${quality.measure_invalid_cells} 个指标单元格无法转换为数字。`] : []),
      ...((quality.excluded_null_measure_cells ?? 0) > 0 ? [`${quality.excluded_null_measure_cells} 个空指标单元格未参与数值计算。`] : []),
    ],
    datasetLabel: `${dataset.file_count} 个文件 · ${dataset.sheet_count} 个 Sheet · 全量`,
    rowsLabel: rowsPassed ? "计划与实际读取一致" : "计划与实际读取不一致",
    rowsPassed,
    reconciliationLabel: reconciliationPassed ? "源表与全部输出表金额一致" : "存在金额对账差异",
    reconciliationPassed,
    classificationLabel: quality.classification_enabled
      ? pending > 0 ? `${pending} 个客户待确认` : "全量唯一客户已完成分类"
      : "当前方案未启用分类",
  };
}

function executingQuality(dataset: NonNullable<ComposeResultPage["dataset"]>): QualitySummaryData {
  return {
    scope: "full",
    plannedRows: dataset.total_rows,
    processedRows: 0,
    validRows: 0,
    invalidRows: 0,
    skippedRows: dataset.skipped_rows,
    sourceFingerprint: dataset.source_fingerprint.slice(0, 16),
    reconciliations: [],
    canComplete: false,
    headline: "正在执行全量确定性计算",
    subline: "结果生成前不会展示抽样值",
    facts: [
      { value: dataset.total_rows.toLocaleString("zh-CN"), label: "计划行数" },
      { value: "0", label: "实际读取" },
      { value: "0", label: "异常行" },
    ],
    warnings: [],
    datasetLabel: `${dataset.file_count} 个文件 · ${dataset.sheet_count} 个 Sheet · 全量`,
    rowsLabel: "执行完成后校验",
    reconciliationLabel: "执行完成后对账",
    classificationLabel: "执行完成后确认",
  };
}

function errorText(error: unknown): string {
  const friendly = humanizeError(error);
  return `${friendly.title}：${friendly.advice}`;
}

function LiveSmartTableWorkspace() {
  const toast = useToast();
  const [phase, setPhase] = React.useState<ComposePhase>("upload");
  const [files, setFiles] = React.useState<ComposeSourceFile[]>([]);
  const [sourcePreview, setSourcePreview] = React.useState<ComposeSourcePreview | null>(null);
  const [preparingPreview, setPreparingPreview] = React.useState(false);
  const [dataset, setDataset] = React.useState<ComposeResultPage["dataset"] | null>(null);
  const [directions, setDirections] = React.useState<ComposeDirection[]>([]);
  const [selectedDirectionId, setSelectedDirectionId] = React.useState<string | null>(null);
  const [customPrompt, setCustomPrompt] = React.useState("");
  const [plan, setPlan] = React.useState<ComposePlan | null>(null);
  const [planPreview, setPlanPreview] = React.useState<ComposeTablePreview | null>(null);
  const [planUpdating, setPlanUpdating] = React.useState(false);
  const [chatMessages, setChatMessages] = React.useState<ChatMessage[]>([]);
  const [chatRound, setChatRound] = React.useState(1);
  const [quality, setQuality] = React.useState<QualitySummaryData | null>(null);
  const [resultSummary, setResultSummary] = React.useState<ComposeExecuteSuccess["result_summary"]>({});
  const [resultTabs, setResultTabs] = React.useState<ResultSheetTab[]>([]);
  const [activeResultTabId, setActiveResultTabId] = React.useState("customer");
  const [resultPage, setResultPage] = React.useState(1);
  const [completed, setCompleted] = React.useState(false);
  const [profilingProgress, setProfilingProgress] = React.useState<{ label: string; percent: number }>();
  const [executingProgress, setExecutingProgress] = React.useState<{ label: string; processed: number; total: number }>();
  const [errorMessage, setErrorMessage] = React.useState<string>();
  const [classificationTarget, setClassificationTarget] = React.useState<string | null>(null);
  const [classificationValue, setClassificationValue] = React.useState("");
  const [classificationSaving, setClassificationSaving] = React.useState(false);
  const [exitConfirmOpen, setExitConfirmOpen] = React.useState(false);
  const fileMap = React.useRef(new Map<string, File>());
  const sidRef = React.useRef<string | null>(null);
  const streamRef = React.useRef<api.SSEController | null>(null);
  const profilingCeilingRef = React.useRef(12);

  React.useEffect(() => () => streamRef.current?.close(), []);
  React.useEffect(() => {
    if (phase !== "profiling") return;
    const timer = window.setInterval(() => {
      setProfilingProgress((current) => {
        if (!current) return current;
        const ceiling = profilingCeilingRef.current;
        if (current.percent >= ceiling) return current;
        const step = Math.max(0.25, Math.min(0.8, (ceiling - current.percent) * 0.06));
        return { ...current, percent: Math.min(ceiling, Number((current.percent + step).toFixed(2))) };
      });
    }, 500);
    return () => window.clearInterval(timer);
  }, [phase]);

  function fail(error: unknown) {
    setErrorMessage(errorText(error));
    setPhase("error");
  }

  function handleFilesAdd(fileList: FileList) {
    if (sidRef.current) void api.composeCancel(sidRef.current).catch(() => undefined);
    sidRef.current = null;
    const replaceCurrent = sourcePreview !== null;
    if (replaceCurrent) fileMap.current.clear();
    setSourcePreview(null);
    setDataset(null);
    setDirections([]);
    setPlan(null);
    const added = Array.from(fileList).map((file, index) => {
      const id = `${file.name}-${file.lastModified}-${Date.now()}-${index}`;
      fileMap.current.set(id, file);
      return { id, name: file.name, sheetCount: 0, rowCount: 0, status: "ready" as const };
    });
    setFiles((previous) => replaceCurrent ? added : [...previous, ...added]);
    setErrorMessage(undefined);
  }

  async function handlePreparePreview() {
    const selected = Array.from(fileMap.current.values());
    if (!selected.length || preparingPreview) return;
    setPreparingPreview(true);
    setFiles((previous) => previous.map((file) => ({ ...file, status: 'scanning' as const })));
    try {
      const response = await api.composeImport(selected);
      sidRef.current = response.session_id;
      const preview = await api.composeSourcePreview(response.session_id, 12);
      setSourcePreview(preview);
      const sheetCounts = new Map<string, number>();
      preview.files.forEach((file) => sheetCounts.set(file.filename, file.sheets.length));
      setFiles((previous) => previous.map((file) => ({
        ...file,
        sheetCount: sheetCounts.get(file.name) ?? 0,
        status: 'ready' as const,
      })));
    } catch (error) {
      if (sidRef.current) await api.composeCancel(sidRef.current).catch(() => undefined);
      sidRef.current = null;
      setFiles((previous) => previous.map((file) => ({ ...file, status: 'failed' as const, error: errorText(error) })));
      toast.error(humanizeError('源表预览失败：' + errorText(error)));
    } finally {
      setPreparingPreview(false);
    }
  }

  async function resetSource() {
    streamRef.current?.close();
    if (sidRef.current) await api.composeCancel(sidRef.current).catch(() => undefined);
    sidRef.current = null;
    fileMap.current.clear();
    setFiles([]);
    setSourcePreview(null);
    setDataset(null);
    setDirections([]);
    setPlan(null);
    setCustomPrompt('');
    setPhase('upload');
  }

  async function handleStartProfiling() {
    const sid = sidRef.current;
    if (!sid || !sourcePreview) return;
    setPhase("profiling");
    profilingCeilingRef.current = 12;
    setProfilingProgress({ label: "正在读取工作表并计算文件指纹", percent: 3 });
    setFiles((previous) => previous.map((file) => ({ ...file, status: "scanning" as const })));
    try {
      profilingCeilingRef.current = 58;
      setProfilingProgress((current) => ({ label: "文件已上传，正在读取工作表和建立本地缓存", percent: Math.max(current?.percent ?? 0, 12) }));
      streamRef.current = api.streamSSE('/api/compose/import/stream/' + sid, (type, data) => {
        if (type === "progress") {
          const event = data as { stage?: string; label?: string; processed_rows?: number; total_rows?: number; current_sheet?: string };
          const stage = event.stage ?? "import_rows";
          const stageConfig: Record<string, { floor: number; ceiling: number }> = {
            import_rows: { floor: 14, ceiling: 62 },
            scan_sheet: { floor: 28, ceiling: 66 },
            profile_evidence: { floor: 66, ceiling: 76 },
            plan_directions: { floor: 76, ceiling: 94 },
            preview_directions: { floor: 94, ceiling: 98 },
          };
          const config = stageConfig[stage] ?? { floor: 14, ceiling: 94 };
          profilingCeilingRef.current = config.ceiling;
          const details = event.processed_rows
            ? ` · 已处理 ${event.processed_rows.toLocaleString("zh-CN")} 行${event.current_sheet ? ` · ${event.current_sheet}` : ""}`
            : "";
          setProfilingProgress((current) => ({
            label: `${event.label ?? "扫描源数据"}${details}`,
            percent: Math.max(current?.percent ?? 0, config.floor),
          }));
        } else if (type === "success") {
          const payload = data as { dataset: NonNullable<ComposeResultPage["dataset"]>; directions: ComposeDirection[] };
          setDataset(payload.dataset);
          setDirections(payload.directions);
          setSelectedDirectionId(payload.directions.find((direction) => direction.recommended)?.id ?? payload.directions[0]?.id ?? null);
          const byFile = new Map<string, { sheetCount: number; rowCount: number }>();
          payload.dataset.sheets.forEach((sheet) => {
            const current = byFile.get(sheet.file) ?? { sheetCount: 0, rowCount: 0 };
            current.sheetCount += 1;
            current.rowCount += sheet.rows;
            byFile.set(sheet.file, current);
          });
          setFiles((previous) => previous.map((file) => ({
            ...file,
            ...(byFile.has(file.name) ? byFile.get(file.name) : {}),
            status: "ready" as const,
          })));
          profilingCeilingRef.current = 100;
          setProfilingProgress({ label: "定制方案和表格预览已准备完成", percent: 100 });
          window.setTimeout(() => {
            setProfilingProgress(undefined);
            setPhase("directions");
          }, 350);
        } else if (type === "error") {
          fail((data as { message?: string }).message ?? "导入失败");
        }
      }, "compose");
    } catch (error) {
      fail(error);
      setFiles((previous) => previous.map((file) => ({ ...file, status: "failed" as const, error: errorText(error) })));
    }
  }

  async function cancelCurrent() {
    setProfilingProgress(undefined);
    setExecutingProgress(undefined);
    await resetSource();
  }

  async function handleSubmitDirection() {
    if (!sidRef.current) return;
    setPlanUpdating(true);
    try {
      const response = await api.composePlan(sidRef.current, {
        direction_id: customPrompt.trim() ? undefined : selectedDirectionId ?? undefined,
        message: customPrompt.trim() || undefined,
      });
      setPlan(response.plan);
      setPlanPreview(response.preview ?? null);
      setChatMessages(liveMessages(response.plan));
      setChatRound(response.plan.confirmed_facts.length + 1);
      setPhase("clarifying");
    } catch (error) {
      toast.error(humanizeError(`方案生成失败：${errorText(error)}`));
    } finally {
      setPlanUpdating(false);
    }
  }

  async function handleSendMessage(text: string) {
    if (!sidRef.current) return;
    setChatMessages((previous) => [...previous, { id: `user-${Date.now()}`, role: "user", content: text }]);
    setPlanUpdating(true);
    try {
      const response = await api.composeMessage(sidRef.current, text);
      setPlan(response.plan);
      setPlanPreview(response.preview ?? null);
      setChatRound((round) => round + 1);
      setChatMessages((previous) => [...previous, ...liveMessages(response.plan)]);
    } catch (error) {
      toast.error(humanizeError(`方案更新失败：${errorText(error)}`));
    } finally {
      setPlanUpdating(false);
    }
  }

  function handleSelectQuickOption(messageId: string, optionId: string) {
    setChatMessages((previous) => previous.map((message) => (
      message.id === messageId ? { ...message, selectedOptionId: optionId } : message
    )));
  }

  async function loadResultPage(sheetName: string, page: number) {
    if (!sidRef.current || !dataset) return;
    try {
      const response = await api.composeResult(sidRef.current, sheetName, (page - 1) * 100, 100);
      const tab = resultTab(response.sheet.name, response.sheet.columns, response.sheet.rows, response.sheet.total_rows);
      setResultTabs((previous) => {
        const index = previous.findIndex((item) => item.id === tab.id);
        if (index < 0) return [...previous, tab];
        return previous.map((item, itemIndex) => itemIndex === index ? tab : item);
      });
    } catch (error) {
      toast.error(humanizeError(`结果读取失败：${errorText(error)}`));
    }
  }

  async function loadDirectionPreview(directionId: string): Promise<ComposeTablePreview | null> {
    if (!sidRef.current) return null;
    try {
      return await api.composePreview(sidRef.current, directionId, 100);
    } catch (error) {
      toast.error(humanizeError(`预览加载失败：${errorText(error)}`));
      return directions.find((direction) => direction.id === directionId)?.preview ?? null;
    }
  }

  async function handleExecutePlan() {
    if (!sidRef.current || !plan || planUpdating) return;
    if (!plan.ready_for_execution) {
      document.getElementById("compose-clarification-chat")?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      return;
    }
    streamRef.current?.close();
    setErrorMessage(undefined);
    setQuality(null);
    setResultSummary({});
    setResultTabs([]);
    setCompleted(false);
    setExecutingProgress({
      label: "正在启动全量确定性计算",
      processed: 0,
      total: dataset?.total_rows ?? 0,
    });
    setPhase("executing");
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    try {
      await api.composeExecute(sidRef.current);
      streamRef.current = api.streamSSE(`/api/compose/execute/stream/${sidRef.current}`, (type, data) => {
        if (type === "progress") {
          const event = data as { label?: string; processed_rows?: number; total_rows?: number };
          setExecutingProgress({ label: event.label ?? "全量计算中", processed: event.processed_rows ?? 0, total: event.total_rows ?? dataset?.total_rows ?? 0 });
        } else if (type === "success") {
          const payload = data as ComposeExecuteSuccess;
          setQuality(liveQuality(payload.quality, dataset!));
          setResultSummary(payload.result_summary ?? {});
          const tabs = payload.result_sheets.map((sheet) => resultTab(sheet.name, sheet.columns, sheet.preview, sheet.total_rows));
          setResultTabs(tabs);
          setActiveResultTabId(tabs[0]?.id ?? "customer");
          setResultPage(1);
          setExecutingProgress(undefined);
          setPhase("review");
          void loadResultPage(payload.result_sheets[0]?.name ?? "客户销售汇总", 1);
        } else if (type === "error") {
          fail((data as { message?: string }).message ?? "全量计算失败");
        }
      }, "compose");
    } catch (error) {
      setExecutingProgress(undefined);
      fail(error);
    }
  }

  async function handleCompleteTable() {
    if (!sidRef.current || !plan || !quality?.canComplete) return;
    setPhase("completing");
    try {
      await api.composeComplete(sidRef.current, plan.direction_title);
      setCompleted(true);
      setPhase("completed");
      toast.success("目标表已完成", "转换逻辑已存入「我的逻辑库」");
    } catch (error) {
      setPhase("review");
      toast.error(humanizeError(`逻辑尚未保存：${errorText(error)}`));
    }
  }

  function handleClassificationAction(rowId: string) {
    const firstLabel = plan?.classification.rules[0]?.label ?? "";
    setClassificationTarget(rowId);
    setClassificationValue(firstLabel);
  }

  async function submitClassification() {
    const value = classificationValue.trim();
    if (!value || !classificationTarget || !sidRef.current) return;
    setClassificationSaving(true);
    try {
      const response = await api.composeClassification(sidRef.current, { [classificationTarget]: value });
      if (dataset) setQuality(liveQuality(response.quality, dataset));
      setResultSummary(response.result_summary ?? {});
      setResultTabs(response.result_sheets.map((sheet) => resultTab(sheet.name, sheet.columns, sheet.preview, sheet.total_rows)));
      setClassificationTarget(null);
      toast.success("分类已更新", "受影响的结果表已重新计算并完成对账");
    } catch (error) {
      toast.error(humanizeError(`分类更新失败：${errorText(error)}`));
    } finally {
      setClassificationSaving(false);
    }
  }

  const displayDataset = dataset ? liveDirectionSummary(dataset) : null;
  const displayPlan = plan && dataset ? livePlanSummary(dataset, plan) : null;
  const displayQuality = quality ?? (phase === "executing" && dataset ? executingQuality(dataset) : null);
  const liveStep = phaseToPreviewStep(phase);

  return (
    <>
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-[#F3F5F7]">
        <div className="mx-auto flex w-full max-w-[1500px] items-start justify-between gap-6 px-8 pb-4 pt-9">
          <div>
            <div className="flex items-center gap-2">
              {phase !== "upload" && (
                <button type="button" aria-label="返回智能制表首页" onClick={() => setExitConfirmOpen(true)} className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-white hover:text-brand-dark">
                  <ArrowLeft className="h-4 w-4" />
                </button>
              )}
              <h2 className="text-[26px] font-bold tracking-tight text-brand-dark">智能制表</h2>
            </div>
            <p className="text-sm font-medium text-slate-500">没有目标样例，也能从源数据制作可复用的结果表</p>
          </div>
          <div className="flex flex-col items-end gap-2 pt-1">
            <span className="text-[11px] font-semibold text-brand">最终结果强制全量计算</span>
            <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-1 shadow-sm" aria-label="智能制表进度">
              {PREVIEW_STEPS.map((step) => (
                <span
                  key={step.id}
                  className={cn(
                    "flex h-[30px] items-center whitespace-nowrap rounded-md px-2.5 text-xs font-semibold",
                    liveStep === step.id ? "bg-brand-dark text-white" : "text-slate-400",
                  )}
                >
                  {step.label}
                </span>
              ))}
            </div>
          </div>
        </div>
      </header>
      <div className="mx-auto w-full max-w-[1500px] px-8 pb-8">
        {phase === "error" && errorMessage && (
          <div className="mb-4 flex items-center justify-between rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            <span className="flex items-center gap-2"><AlertCircle className="h-4 w-4" />{errorMessage}</span>
            <Button variant="ghost" size="sm" onClick={() => { setErrorMessage(undefined); setPhase("upload"); }}><RotateCw className="h-3.5 w-3.5" />重试</Button>
          </div>
        )}
        {(phase === "upload" || phase === "profiling") && (
          <UploadState
            files={files}
            sourcePreview={sourcePreview}
            preparingPreview={preparingPreview}
            profiling={phase === "profiling"}
            profilingProgress={profilingProgress}
            onFilesAdd={handleFilesAdd}
            onFileRemove={(id) => { fileMap.current.delete(id); setFiles((previous) => previous.filter((file) => file.id !== id)); }}
            onStartProfiling={handleStartProfiling}
            onPreparePreview={handlePreparePreview}
            onResetSource={() => void resetSource()}
            onCancelProfiling={cancelCurrent}
          />
        )}
        {phase === "directions" && displayDataset && (
          <DirectionState
            dataset={displayDataset}
            directions={liveDirections(directions)}
            selectedDirectionId={selectedDirectionId}
            customPrompt={customPrompt}
            fieldRoles={liveFieldRoles(dataset!)}
            readiness={dataset!.readiness}
            submitting={planUpdating}
            onSelectDirection={(id) => { setSelectedDirectionId(id); setCustomPrompt(""); }}
            onCustomPromptChange={(value) => { setCustomPrompt(value); if (value.trim()) setSelectedDirectionId(null); }}
            onSubmitDirection={handleSubmitDirection}
            onLoadPreview={loadDirectionPreview}
          />
        )}
        {phase === "clarifying" && displayPlan && plan && (
          <ClarificationState
            dataset={displayPlan}
            plan={livePlan(plan)}
            preview={planPreview}
            previewLoading={!planPreview && planUpdating}
            planUpdating={planUpdating}
            messages={chatMessages}
            chatRound={chatRound}
            onBackToDirections={() => setPhase("directions")}
            onExecutePlan={handleExecutePlan}
            onSendMessage={handleSendMessage}
            onSelectQuickOption={handleSelectQuickOption}
          />
        )}
        {(phase === "executing" || phase === "review" || phase === "completing" || phase === "completed") && displayQuality && dataset && (
          <ResultState
            dataset={liveDatasetSummary(dataset, "result")}
            quality={displayQuality}
            resultSummary={resultSummary}
            tabs={resultTabs}
            activeTabId={activeResultTabId}
            page={resultPage}
            completed={completed}
            executing={phase === "executing"}
            executingProgress={executingProgress}
            onTabChange={(id) => { setActiveResultTabId(id); setResultPage(1); const tab = resultTabs.find((item) => item.id === id); if (tab) void loadResultPage(tab.label, 1); }}
            onPageChange={(page) => { setResultPage(page); const tab = resultTabs.find((item) => item.id === activeResultTabId); if (tab) void loadResultPage(tab.label, page); }}
            onDownloadExcel={() => sidRef.current && void api.composeExport(sidRef.current).catch((error) => toast.error(humanizeError(`导出失败：${errorText(error)}`)))}
            onAdjustPlan={() => setPhase("clarifying")}
            onCompleteTable={handleCompleteTable}
            onBackHome={() => void resetSource()}
            onClassificationAction={handleClassificationAction}
          />
        )}
      </div>
      <Dialog open={exitConfirmOpen} onOpenChange={setExitConfirmOpen}>
        <DialogContent className="max-w-sm rounded-lg">
          <DialogHeader>
            <DialogTitle>返回智能制表首页？</DialogTitle>
            <DialogDescription>当前制表会话将结束，尚未保存的方案和结果不会保留。</DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setExitConfirmOpen(false)}>继续制表</Button>
            <Button variant="brand" onClick={() => { setExitConfirmOpen(false); void resetSource(); }}>确认返回</Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={classificationTarget !== null} onOpenChange={(open) => { if (!open && !classificationSaving) setClassificationTarget(null); }}>
        <DialogContent className="max-w-md rounded-lg">
          <DialogHeader>
            <DialogTitle>确认分类</DialogTitle>
            <DialogDescription>
              为实体 {classificationTarget} 选择最终分类。保存后会重新计算所有受影响的结果表。
            </DialogDescription>
          </DialogHeader>
          <Select value={classificationValue} onValueChange={setClassificationValue}>
            <SelectTrigger aria-label="最终分类">
              <SelectValue placeholder="选择分类" />
            </SelectTrigger>
            <SelectContent>
              {(plan?.classification.rules ?? []).map((rule) => (
                <SelectItem key={rule.label} value={rule.label}>{rule.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" disabled={classificationSaving} onClick={() => setClassificationTarget(null)}>取消</Button>
            <Button variant="brand" disabled={!classificationValue || classificationSaving} onClick={() => void submitClassification()}>
              {classificationSaving && <Loader2 className="h-4 w-4 animate-spin" />}
              保存并重新对账
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function SmartTableWorkspace() {
  const searchParams = useSearchParams();
  const state = searchParams.get("state") as ComposePreviewStep | null;
  return api.MOCK && state && PREVIEW_STEPS.some((step) => step.id === state)
    ? <PreviewSmartTableWorkspace />
    : <LiveSmartTableWorkspace />;
}

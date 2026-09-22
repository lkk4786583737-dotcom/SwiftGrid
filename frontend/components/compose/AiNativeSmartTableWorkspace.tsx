"use client";

import * as React from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  Download,
  Eye,
  FileCode2,
  FileSpreadsheet,
  History,
  Library,
  Loader2,
  MessageSquareText,
  Pencil,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/Toast";
import * as api from "@/lib/api";
import { humanizeError } from "@/lib/errors";
import type {
  ComposeDataset,
  ComposeDirection,
  ComposeExecuteSuccess,
  ComposePlan,
  ComposeQuality,
  ComposeSheetSummary,
  ComposeSourcePreview,
  ComposeTablePreview,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { SourceTablePreview } from "./SourceTablePreview";
import { TablePreview } from "./TablePreview";

type ComposePhase =
  | "upload"
  | "preview"
  | "profiling"
  | "directions"
  | "aligning"
  | "clarifying"
  | "executing"
  | "review"
  | "completed"
  | "error";

type FeedItem = {
  id: string;
  role: "agent" | "user" | "note";
  text: string;
  subtext?: string;
};

type LocalFile = { id: string; name: string; size: number; file?: File };

type HistorySnapshot = {
  phase: ComposePhase;
  files: Omit<LocalFile, "file">[];
  sourcePreview: ComposeSourcePreview | null;
  dataset: ComposeDataset | null;
  directions: ComposeDirection[];
  selectedDirectionId: string | null;
  plan: ComposePlan | null;
  planPreview: ComposeTablePreview | null;
  quality: ComposeQuality | null;
  resultSheets: ComposeSheetSummary[];
  resultSummary: ComposeExecuteSuccess["result_summary"];
  feed: FeedItem[];
  demoMode: boolean;
  completed: boolean;
};

type HistoryEntry = {
  id: string;
  sid: string;
  title: string;
  status: "unfinished" | "ready" | "completed";
  updatedAt: string;
  fileNames: string[];
  snapshot: HistorySnapshot;
};

const HISTORY_KEY = "swiftgrid.compose.history.v1";
const STAGES = [
  { label: "上传与体检", phases: ["upload", "preview", "profiling"] },
  { label: "AI 建模", phases: ["directions"] },
  { label: "确认方案", phases: ["aligning", "clarifying"] },
  { label: "生成与入库", phases: ["executing", "review", "completed"] },
] as const;

const DEMO_SOURCE_PREVIEW: ComposeSourcePreview = {
  scope: "source_preview",
  sheet_count: 1,
  max_rows_per_sheet: 5,
  truncated_sheets: false,
  files: [{
    filename: "销售明细.xlsx",
    size_bytes: 23800000,
    sheets: [{
      name: "销售流水",
      header_row: 1,
      columns: ["日期", "客户名称", "业务员", "单据标识", "含税金额"],
      rows: [
        ["2026-01-03", "华东商贸", "陈明", "XHC", 28640.5],
        ["2026-01-03", "城北便利", "刘佳", "XHC", 12890],
        ["2026-01-05", "华东商贸", "陈明", "XHH", -1260],
      ],
      preview_rows: 3,
    }],
  }],
};

const DEMO_DATASET: ComposeDataset = {
  session_id: "compose-demo",
  file_count: 1,
  sheet_count: 3,
  planned_rows: 275426,
  total_rows: 275426,
  valid_rows: null,
  skipped_rows: 0,
  source_fingerprint: "demo-q1-sales",
  schema: {
    columns: ["日期", "客户编号", "客户名称", "业务员", "部门名称", "单据标识", "单据编号", "含税金额"],
    signature: "demo-sales",
  },
  sheets: [
    { file: "销售明细.xlsx", sheet: "Sheet1", columns: ["日期", "客户编号", "客户名称", "业务员", "单据标识", "含税金额"], signature: "demo", rows: 120000 },
    { file: "销售明细.xlsx", sheet: "Sheet2", columns: ["日期", "客户编号", "客户名称", "业务员", "单据标识", "含税金额"], signature: "demo", rows: 100000 },
    { file: "销售明细.xlsx", sheet: "Sheet3", columns: ["日期", "客户编号", "客户名称", "业务员", "单据标识", "含税金额"], signature: "demo", rows: 55426 },
  ],
  sample: { scope: "stratified", rows: 120, max_per_sheet: 40 },
  quality_findings: [{
    code: "NAME_VARIANTS",
    severity: "warning",
    field: "客户名称",
    message: "少量客户编号对应多种名称写法，生成时将保留最新名称。",
  }],
  readiness: {
    level: 3,
    label: "可以建模",
    reasons: ["日期、业务对象和金额字段可稳定识别", "多个 Sheet 结构一致，可按行合并"],
    warnings: ["客户名称存在少量写法差异"],
  },
  planning: { source: "model", model_configured: true },
};

function preview(title: string): ComposeTablePreview {
  return {
    scope: "representative_sample",
    source_rows: 120,
    max_rows: 6,
    sheets: [{
      name: title,
      columns: ["月份", "客户名称", "销售金额", "退货金额", "净销售额"],
      rows: [
        ["2026-01", "华东商贸", 286400.5, 12600, 273800.5],
        ["2026-01", "新景百货", 218950.2, 3200, 215750.2],
        ["2026-02", "华东商贸", 301420, 8650, 292770],
      ],
      sample_result_rows: 3,
    }],
  };
}

const DEMO_DIRECTIONS: ComposeDirection[] = [
  {
    id: "customer-month",
    title: "客户月度销售归纳表",
    description: "按月份和客户归纳销售、退货与净额，适合持续复用。",
    evidence: ["日期", "客户编号", "客户名称", "单据标识", "含税金额"],
    evidence_notes: ["客户编号可作为稳定汇总键", "日期覆盖连续月份", "交易标识与金额方向一致"],
    outputs: ["客户月度汇总", "客户贡献排名"],
    recommended: true,
    grain_label: "月份 × 客户",
    estimated_rows: 4820,
    preview: preview("客户月度汇总"),
  },
  {
    id: "salesperson-month",
    title: "业务员月度业绩表",
    description: "按业务员和月份汇总净销售额与单据量。",
    evidence: ["日期", "业务员", "单据编号", "含税金额"],
    evidence_notes: ["业务员字段覆盖率高", "单据编号适合去重计数"],
    outputs: ["业务员月度汇总"],
    recommended: false,
    grain_label: "月份 × 业务员",
    estimated_rows: 180,
  },
  {
    id: "department-summary",
    title: "部门销售与退货汇总表",
    description: "按部门归纳销售规模、退货和净额。",
    evidence: ["部门名称", "单据标识", "含税金额"],
    evidence_notes: ["部门分类稳定", "销售与退货可以用真实交易标识区分"],
    outputs: ["部门汇总"],
    recommended: false,
    grain_label: "部门",
    estimated_rows: 16,
  },
];

const DEMO_QUALITY: ComposeQuality = {
  scope: "full",
  planned_rows: 275426,
  processed_rows: 275426,
  valid_rows: 275408,
  invalid_rows: 18,
  skipped_rows: 0,
  source_fingerprint: "demo-q1-sales",
  reconciliations: [
    { name: "源表与目标表金额对账", status: "passed", delta: "0.00" },
    { name: "处理行数守恒", status: "passed", delta: "0" },
  ],
  pending_classification_count: 0,
  can_complete: true,
};

const DEMO_RESULTS: ComposeSheetSummary[] = [
  {
    name: "客户月度汇总",
    columns: ["月份", "客户编号", "客户名称", "销售金额", "退货金额", "净销售额"],
    rows: 4820,
    total_rows: 4820,
    preview: [
      ["2026-01", "C0018", "华东商贸", "286,400.50", "12,600.00", "273,800.50"],
      ["2026-01", "C0031", "新景百货", "218,950.20", "3,200.00", "215,750.20"],
      ["2026-02", "C0018", "华东商贸", "301,420.00", "8,650.00", "292,770.00"],
    ],
  },
  {
    name: "客户贡献排名",
    columns: ["客户编号", "客户名称", "净销售额", "单据数", "贡献占比"],
    rows: 860,
    total_rows: 860,
    preview: [
      ["C0018", "华东商贸", "868,420.50", 96, "8.42%"],
      ["C0031", "新景百货", "704,220.80", 81, "6.83%"],
      ["C0046", "城北便利", "582,196.00", 74, "5.65%"],
    ],
  },
];

function initialFeed(): FeedItem[] {
  return [{
    id: "welcome",
    role: "agent",
    text: "把源表交给我，我先检查数据，再和你一起确定要做成什么表。",
    subtext: "我会在关键节点主动汇报，只询问真正影响结果的口径。",
  }];
}

function uid(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function readHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(HISTORY_KEY) ?? "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function demoPlan(direction: ComposeDirection, request = ""): ComposePlan {
  return {
    schema_version: 1,
    dataset_id: DEMO_DATASET.session_id,
    direction_id: direction.id,
    direction_title: request ? "按你的描述生成的归纳表" : direction.title,
    output_kind: "generic_summary",
    role_bindings: {
      entity_id: "客户编号",
      entity_name: "客户名称",
      date: "日期",
      amount: "含税金额",
      transaction_type: "单据标识",
    },
    business_rules: {
      amount_basis: "统一使用含税金额",
      transaction_mapping: ["XHC 作为销售", "XHH 作为退货"],
      precision: "金额保留两位小数",
    },
    filters: [],
    time_grain: "month",
    dimensions: ["月份", "客户编号", "客户名称"],
    outputs: [
      { name: "客户月度汇总", key: "customer_month", group_by: ["月份", "客户编号"], metrics: ["销售金额", "退货金额", "净销售额"] },
      { name: "客户贡献排名", key: "customer_rank", group_by: ["客户编号"], metrics: ["净销售额", "单据数", "贡献占比"] },
    ],
    requirement_summary: request
      ? `生成一张归纳表：${request}。未提及的金额口径使用含税金额，结果保留两位小数。`
      : `生成“${direction.title}”：${direction.description}，使用真实客户编号、日期、交易标识和含税金额。`,
    intent_confirmation: { required: true, confirmed: false },
    unresolved_requirements: [],
    decision_log: [],
    classification: { enabled: false, rule_pack: "none", rule_pack_version: "1", rules: [], unresolved_policy: "keep" },
    confirmed_facts: [],
    questions: [
      {
        code: "amount_basis",
        text: "销售额、退货额和净额统一使用含税金额计算吗？",
        blocking: true,
        options: [
          { id: "taxed", label: "使用含税金额", message: "统一使用含税金额计算。", recommended: true },
          { id: "other", label: "补充其他口径", message: "金额口径需要调整。" },
        ],
      },
      {
        code: "name_policy",
        text: "同一客户编号出现多种名称时，目标表保留哪一种？",
        blocking: true,
        options: [
          { id: "latest", label: "保留最新名称", message: "同一客户编号保留日期最新的名称。", recommended: true },
          { id: "first", label: "保留首次名称", message: "同一客户编号保留首次出现的名称。" },
        ],
      },
    ],
    ready_for_execution: false,
    assistant_message: "我已经把你的目标整理成具体方案，请先确认我的理解。",
  };
}

function stageIndex(phase: ComposePhase): number {
  const index = STAGES.findIndex((stage) => (stage.phases as readonly string[]).includes(phase));
  return Math.max(0, index);
}

function progressHint(phase: ComposePhase, seconds: number): string {
  if (phase === "profiling") {
    if (seconds >= 45) return "数据量较大，我还在验证候选方案，完成后会自动继续。";
    if (seconds >= 15) return "正在读取全量字段画像，你可以暂时离开这个页面。";
    return "模型只接收字段画像和必要证据，原始明细留在本地。";
  }
  if (seconds >= 60) return "全量数据仍在本地计算，复杂表可能需要几分钟。";
  if (seconds >= 20) return "正在生成目标表和可复用逻辑，页面可以暂时放在后台。";
  return "最终结果使用全量数据计算，不用模型抽样值替代。";
}

function AgentMessage({ item }: { item: FeedItem }) {
  if (item.role === "note") {
    return (
      <div className="flex items-center justify-center gap-2 py-1 text-[11px] font-medium text-slate-400">
        <span className="h-px w-10 bg-slate-200" />{item.text}<span className="h-px w-10 bg-slate-200" />
      </div>
    );
  }
  if (item.role === "user") {
    return <div className="flex justify-end"><div className="max-w-[72%] rounded-lg bg-brand-dark px-4 py-3 text-[13px] leading-6 text-white shadow-sm">{item.text}</div></div>;
  }
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand text-white shadow-sm"><Sparkles className="h-4 w-4" /></span>
      <div className="max-w-[760px] rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <p className="text-[13px] font-semibold leading-6 text-brand-dark">{item.text}</p>
        {item.subtext && <p className="mt-1 text-xs leading-5 text-slate-500">{item.subtext}</p>}
      </div>
    </div>
  );
}

function ResultPreview({ sheet }: { sheet: ComposeSheetSummary }) {
  return (
    <div className="max-h-[360px] overflow-auto rounded-lg border border-slate-200">
      <table className="min-w-full border-collapse text-[11px]">
        <thead className="sticky top-0 bg-slate-50"><tr>{sheet.columns.map((column) => <th key={column} className="whitespace-nowrap border-b border-r border-slate-200 px-3 py-2 text-left font-bold text-brand-dark last:border-r-0">{column}</th>)}</tr></thead>
        <tbody>{sheet.preview.map((row, rowIndex) => <tr key={rowIndex} className="odd:bg-slate-50/50">{sheet.columns.map((column, columnIndex) => <td key={`${column}-${columnIndex}`} className="whitespace-nowrap border-b border-r border-slate-100 px-3 py-2 text-slate-600 last:border-r-0">{String(row[columnIndex] ?? "")}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export function AiNativeSmartTableWorkspace() {
  const toast = useToast();
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const streamRef = React.useRef<api.SSEController | null>(null);
  const sidRef = React.useRef<string | null>(null);
  const [phase, setPhase] = React.useState<ComposePhase>("upload");
  const [files, setFiles] = React.useState<LocalFile[]>([]);
  const [sourcePreview, setSourcePreview] = React.useState<ComposeSourcePreview | null>(null);
  const [dataset, setDataset] = React.useState<ComposeDataset | null>(null);
  const [directions, setDirections] = React.useState<ComposeDirection[]>([]);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [plan, setPlan] = React.useState<ComposePlan | null>(null);
  const [planPreview, setPlanPreview] = React.useState<ComposeTablePreview | null>(null);
  const [quality, setQuality] = React.useState<ComposeQuality | null>(null);
  const [resultSheets, setResultSheets] = React.useState<ComposeSheetSummary[]>([]);
  const [resultSummary, setResultSummary] = React.useState<ComposeExecuteSuccess["result_summary"]>({});
  const [activeSheetName, setActiveSheetName] = React.useState("");
  const [feed, setFeed] = React.useState<FeedItem[]>(initialFeed);
  const [progress, setProgress] = React.useState({ label: "", percent: 0, processed: 0, total: 0 });
  const [elapsed, setElapsed] = React.useState(0);
  const [demoMode, setDemoMode] = React.useState(false);
  const [completed, setCompleted] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [customOpen, setCustomOpen] = React.useState(false);
  const [fineTune, setFineTune] = React.useState(false);
  const [customText, setCustomText] = React.useState("");
  const [answerText, setAnswerText] = React.useState("");
  const [answered, setAnswered] = React.useState<string[]>([]);
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [history, setHistory] = React.useState<HistoryEntry[]>([]);
  const [exitOpen, setExitOpen] = React.useState(false);
  const [importOpen, setImportOpen] = React.useState(false);
  const [logicName, setLogicName] = React.useState("");

  const selected = directions.find((item) => item.id === selectedId) ?? directions[0];
  const activeSheet = resultSheets.find((item) => item.name === activeSheetName) ?? resultSheets[0];
  const currentQuestion = plan?.questions.find((item) => item.blocking && !answered.includes(item.code))
    ?? plan?.questions.find((item) => !answered.includes(item.code));
  const blockingQuality = dataset?.quality_findings?.filter((item) => item.severity === "blocking") ?? [];

  const addFeed = React.useCallback((role: FeedItem["role"], text: string, subtext?: string) => {
    setFeed((items) => [...items, { id: uid(role), role, text, subtext }]);
  }, []);

  React.useEffect(() => {
    setHistory(readHistory());
    return () => streamRef.current?.close();
  }, []);

  React.useEffect(() => {
    if (phase !== "profiling" && phase !== "executing") return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  React.useEffect(() => {
    const timer = window.setTimeout(() => {
      document.getElementById("compose-current-step")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [phase]);

  React.useEffect(() => {
    const sid = sidRef.current;
    if (!sid || (!dataset && !sourcePreview)) return;
    const snapshot: HistorySnapshot = {
      phase: phase === "executing" ? "clarifying" : phase,
      files: files.map(({ id, name, size }) => ({ id, name, size })),
      sourcePreview,
      dataset,
      directions,
      selectedDirectionId: selectedId,
      plan,
      planPreview,
      quality,
      resultSheets,
      resultSummary,
      feed,
      demoMode,
      completed,
    };
    const entry: HistoryEntry = {
      id: sid,
      sid,
      title: plan?.direction_title ?? selected?.title ?? files[0]?.name ?? "未命名制表会话",
      status: completed ? "completed" : resultSheets.length ? "ready" : "unfinished",
      updatedAt: new Date().toISOString(),
      fileNames: files.map((file) => file.name),
      snapshot,
    };
    const next = [entry, ...readHistory().filter((item) => item.id !== sid)].slice(0, 30);
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
    setHistory(next);
  }, [completed, dataset, demoMode, directions, feed, files, phase, plan, planPreview, quality, resultSheets, resultSummary, selected, selectedId, sourcePreview]);

  function enterDemoMode(reason: string) {
    setDemoMode(true);
    addFeed("note", `真实服务暂不可用，已切换交互演示：${reason}`);
  }

  function errorText(error: unknown): string {
    const friendly = humanizeError(error);
    return friendly.advice ? `${friendly.title}：${friendly.advice}` : friendly.title;
  }

  function resetWorkspace() {
    streamRef.current?.close();
    sidRef.current = null;
    setPhase("upload");
    setFiles([]);
    setSourcePreview(null);
    setDataset(null);
    setDirections([]);
    setSelectedId(null);
    setPlan(null);
    setPlanPreview(null);
    setQuality(null);
    setResultSheets([]);
    setResultSummary({});
    setActiveSheetName("");
    setFeed(initialFeed());
    setProgress({ label: "", percent: 0, processed: 0, total: 0 });
    setDemoMode(false);
    setCompleted(false);
    setBusy(false);
    setCustomOpen(false);
    setFineTune(false);
    setCustomText("");
    setAnswerText("");
    setAnswered([]);
  }

  async function acceptFiles(fileList: FileList | File[]) {
    const incoming = Array.from(fileList);
    if (!incoming.length) return;
    const localFiles = incoming.map((file) => ({ id: uid("file"), name: file.name, size: file.size, file }));
    setFiles(localFiles);
    setBusy(true);
    setPhase("preview");
    addFeed("user", `已上传 ${incoming.map((file) => file.name).join("、")}`);
    addFeed("agent", "文件收到了，我先读几行真实数据给你确认。", "这里只做预览，还没有开始正式建模。");
    try {
      const imported = await api.composeImport(incoming);
      sidRef.current = imported.session_id;
      setSourcePreview(await api.composeSourcePreview(imported.session_id, 10));
    } catch (error) {
      sidRef.current = uid("demo-session");
      setSourcePreview({
        ...DEMO_SOURCE_PREVIEW,
        files: DEMO_SOURCE_PREVIEW.files.map((file, index) => ({
          ...file,
          filename: localFiles[index]?.name ?? file.filename,
          size_bytes: localFiles[index]?.size ?? file.size_bytes,
        })),
      });
      enterDemoMode(errorText(error));
    } finally {
      setBusy(false);
    }
  }

  function finishDemoProfiling() {
    window.setTimeout(() => setProgress({ label: "分析字段含义和可用关系", percent: 58, processed: 170000, total: 275426 }), 450);
    window.setTimeout(() => setProgress({ label: "生成有价值的制表方向", percent: 88, processed: 275426, total: 275426 }), 900);
    window.setTimeout(() => {
      setDataset({ ...DEMO_DATASET, session_id: sidRef.current ?? DEMO_DATASET.session_id });
      setDirections(DEMO_DIRECTIONS);
      setSelectedId(DEMO_DIRECTIONS[0].id);
      setProgress({ label: "建模方向已准备完成", percent: 100, processed: 275426, total: 275426 });
      addFeed("agent", "体检完成，可以开始做表。", "我找到了 3 个不同粒度的方向；有 1 条名称一致性提醒，不阻断生成。");
      setPhase("directions");
      setBusy(false);
    }, 1400);
  }

  async function startProfiling() {
    if (!sourcePreview || !sidRef.current) return;
    setBusy(true);
    setPhase("profiling");
    setProgress({ label: "检查 Sheet 结构与数据质量", percent: 12, processed: 0, total: 0 });
    addFeed("agent", "现在开始体检和建模。", "我会先找出字段、表间关系和数据问题，再让模型提出制表方向。");
    if (demoMode) return finishDemoProfiling();
    try {
      streamRef.current?.close();
      streamRef.current = api.streamSSE(`/api/compose/import/stream/${sidRef.current}`, (type, data) => {
        if (type === "progress") {
          const event = data as { stage?: string; label?: string; processed_rows?: number; total_rows?: number };
          const floors: Record<string, number> = { import_rows: 18, scan_sheet: 35, profile_evidence: 62, plan_directions: 78, preview_directions: 92 };
          const total = event.total_rows ?? 0;
          const processed = event.processed_rows ?? 0;
          const ratio = total ? Math.min(16, Math.round((processed / total) * 16)) : 0;
          setProgress((current) => ({
            label: event.label ?? "分析源数据",
            percent: Math.max(current.percent, (floors[event.stage ?? ""] ?? 18) + ratio),
            processed,
            total,
          }));
        } else if (type === "success") {
          const payload = data as { dataset: ComposeDataset; directions: ComposeDirection[] };
          setDataset(payload.dataset);
          setDirections(payload.directions);
          setSelectedId((payload.directions.find((item) => item.recommended) ?? payload.directions[0])?.id ?? null);
          addFeed("agent", "体检完成，可以开始做表。", payload.directions.length ? `我找到了 ${payload.directions.length} 个有执行基础的方向。` : "你可以直接描述想要的目标表。");
          setPhase("directions");
          setBusy(false);
          streamRef.current?.close();
        } else if (type === "error") {
          enterDemoMode((data as { message?: string }).message ?? "建模服务暂不可用");
          finishDemoProfiling();
        }
      }, "compose");
    } catch (error) {
      enterDemoMode(errorText(error));
      finishDemoProfiling();
    }
  }

  async function preparePlan(request = "") {
    const direction = selected ?? DEMO_DIRECTIONS[0];
    setBusy(true);
    setPhase("aligning");
    setCustomOpen(false);
    setFineTune(false);
    setAnswered([]);
    addFeed("user", request.trim() || `我选择“${direction.title}”。`);
    addFeed("agent", "我先把你的要求整理成具体方案，再请你确认。", "确认理解一致后，才会进入口径澄清和生成。");
    try {
      let nextPlan: ComposePlan;
      let nextPreview = direction.preview ?? null;
      if (demoMode || !sidRef.current) {
        await new Promise((resolve) => window.setTimeout(resolve, 600));
        nextPlan = demoPlan(direction, request);
      } else {
        const response = await api.composePlan(sidRef.current, {
          direction_id: direction.id,
          message: request.trim() || undefined,
        });
        nextPlan = response.plan;
        nextPreview = response.preview ?? nextPreview;
      }
      setPlan(nextPlan);
      setPlanPreview(nextPreview);
      setLogicName(nextPlan.direction_title);
      addFeed("agent", nextPlan.assistant_message || "我已经把要求整理好了，请确认下面的理解。");
    } catch (error) {
      enterDemoMode(errorText(error));
      const fallback = demoPlan(direction, request);
      setPlan(fallback);
      setPlanPreview(direction.preview ?? null);
      setLogicName(fallback.direction_title);
    } finally {
      setBusy(false);
    }
  }

  function confirmAlignment() {
    if (!plan) return;
    addFeed("user", "理解正确，继续。");
    if (plan.questions.length) {
      setPhase("clarifying");
      addFeed("agent", `生成前还有 ${plan.questions.filter((item) => item.blocking).length || plan.questions.length} 个口径需要确认。`, "我会逐个问，只保留会改变结果的问题。");
    } else {
      void startExecution();
    }
  }

  async function answerQuestion(text: string, code?: string) {
    const value = text.trim();
    if (!value || !plan) return;
    const nextAnswered = code ? [...answered, code] : answered;
    setAnswered(nextAnswered);
    setAnswerText("");
    setBusy(true);
    addFeed("user", value);
    try {
      let nextPlan = plan;
      if (demoMode || !sidRef.current) {
        await new Promise((resolve) => window.setTimeout(resolve, 400));
        const remaining = plan.questions.filter((item) => !nextAnswered.includes(item.code));
        nextPlan = {
          ...plan,
          questions: remaining,
          confirmed_facts: [...plan.confirmed_facts, { turn: plan.confirmed_facts.length + 1, text: value }],
          ready_for_execution: remaining.length === 0,
          intent_confirmation: { required: true, confirmed: remaining.length === 0 },
        };
      } else {
        const response = await api.composeMessage(sidRef.current, value);
        nextPlan = response.plan;
        setPlanPreview(response.preview ?? planPreview);
      }
      setPlan(nextPlan);
      const remaining = nextPlan.questions.filter((item) => !nextAnswered.includes(item.code));
      addFeed("agent", nextPlan.ready_for_execution || !remaining.length ? "口径已经对齐，我会按确认内容生成全量结果。" : "这条口径已记录，继续确认下一项。");
    } catch (error) {
      toast.error(humanizeError(error));
    } finally {
      setBusy(false);
    }
  }

  function finishExecution(payload?: ComposeExecuteSuccess) {
    const nextQuality = payload?.quality ?? DEMO_QUALITY;
    const nextSheets = payload?.result_sheets ?? DEMO_RESULTS;
    setQuality(nextQuality);
    setResultSheets(nextSheets);
    setResultSummary(payload?.result_summary ?? { source_sales: "10,884,322.60", source_returns: "-562,418.30", source_net: "10,321,904.30" });
    setActiveSheetName(nextSheets[0]?.name ?? "");
    setPhase("review");
    setBusy(false);
    addFeed("agent", "表格已经生成，可以先看预览再下载。", nextQuality.can_complete ? "全量处理已完成，现有质量门禁通过。" : "结果已生成，但仍有质量提醒。");
  }

  function finishDemoExecution() {
    window.setTimeout(() => setProgress({ label: "全量读取与标准化", percent: 38, processed: 105000, total: 275426 }), 450);
    window.setTimeout(() => setProgress({ label: "分组计算并保留两位小数", percent: 72, processed: 226000, total: 275426 }), 900);
    window.setTimeout(() => setProgress({ label: "生成目标表与逻辑脚本", percent: 92, processed: 275426, total: 275426 }), 1350);
    window.setTimeout(() => finishExecution(), 1750);
  }

  async function startExecution() {
    if (!plan) return;
    setBusy(true);
    setPhase("executing");
    setProgress({ label: "准备全量数据", percent: 8, processed: 0, total: dataset?.total_rows ?? 0 });
    addFeed("user", "口径没问题，开始生成。");
    addFeed("agent", "开始全量生成。", "数据量较大时可能需要几分钟，我会持续汇报当前步骤。");
    if (demoMode || !sidRef.current) return finishDemoExecution();
    try {
      await api.composeExecute(sidRef.current);
      streamRef.current?.close();
      streamRef.current = api.streamSSE(`/api/compose/execute/stream/${sidRef.current}`, (type, data) => {
        if (type === "progress") {
          const event = data as { label?: string; processed_rows?: number; total_rows?: number };
          const total = event.total_rows ?? dataset?.total_rows ?? 0;
          const processed = event.processed_rows ?? 0;
          setProgress({
            label: event.label ?? "全量计算中",
            percent: total ? Math.min(96, 10 + Math.round((processed / total) * 84)) : 18,
            processed,
            total,
          });
        } else if (type === "success") {
          finishExecution(data as ComposeExecuteSuccess);
          streamRef.current?.close();
        } else if (type === "error") {
          enterDemoMode((data as { message?: string }).message ?? "真实执行暂不可用");
          finishDemoExecution();
        }
      }, "compose");
    } catch (error) {
      enterDemoMode(errorText(error));
      finishDemoExecution();
    }
  }

  async function downloadResult() {
    if (demoMode || !sidRef.current) {
      toast.info("交互演示", "下载入口已保留，真实 XLSX 将在执行后端接通后输出。");
      return;
    }
    try {
      await api.composeExport(sidRef.current);
    } catch (error) {
      toast.error(humanizeError(error));
    }
  }

  async function importLogic() {
    if (!logicName.trim()) return;
    setBusy(true);
    try {
      if (!demoMode && sidRef.current) await api.composeComplete(sidRef.current, logicName.trim());
      setCompleted(true);
      setPhase("completed");
      setImportOpen(false);
      addFeed("agent", "逻辑已经存入我的逻辑库。", "下次处理同类源表时，可以直接复用这套转换方式。");
      toast.success("已导入逻辑库", logicName.trim());
    } catch (error) {
      toast.error(humanizeError(error));
    } finally {
      setBusy(false);
    }
  }

  function restoreHistory(entry: HistoryEntry) {
    const value = entry.snapshot;
    sidRef.current = entry.sid;
    setPhase(value.phase === "error" ? "directions" : value.phase);
    setFiles(value.files);
    setSourcePreview(value.sourcePreview);
    setDataset(value.dataset);
    setDirections(value.directions);
    setSelectedId(value.selectedDirectionId);
    setPlan(value.plan);
    setPlanPreview(value.planPreview);
    setQuality(value.quality);
    setResultSheets(value.resultSheets);
    setResultSummary(value.resultSummary);
    setActiveSheetName(value.resultSheets[0]?.name ?? "");
    setFeed(value.feed.length ? value.feed : initialFeed());
    setDemoMode(value.demoMode);
    setCompleted(value.completed);
    setHistoryOpen(false);
  }

  function deleteHistory(id: string) {
    const next = readHistory().filter((item) => item.id !== id);
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
    setHistory(next);
  }

  function renderUpload() {
    if (phase !== "upload" && phase !== "preview") return null;
    return (
      <section className="ml-11 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <input ref={fileInputRef} type="file" multiple accept=".xlsx,.xls,.csv" className="hidden"
          onChange={(event) => event.target.files && void acceptFiles(event.target.files)} />
        {!files.length ? (
          <button type="button" onClick={() => fileInputRef.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => { event.preventDefault(); void acceptFiles(event.dataTransfer.files); }}
            className="flex min-h-[176px] w-full flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-6 text-center transition-colors hover:border-brand hover:bg-brand/[0.025]">
            <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-white text-brand shadow-sm"><Upload className="h-5 w-5" /></span>
            <strong className="text-sm text-brand-dark">拖入或点击上传 Excel</strong>
            <span className="mt-1 text-xs text-slate-400">支持 XLSX、XLS 和 CSV，可一次上传多个文件</span>
          </button>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <div className="space-y-2">
                {files.map((file) => (
                  <div key={file.id} className="flex items-center gap-3 text-xs">
                    <FileSpreadsheet className="h-4 w-4 text-emerald-600" />
                    <strong className="text-brand-dark">{file.name}</strong>
                    <span className="text-slate-400">{file.size >= 1048576 ? `${(file.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`}</span>
                  </div>
                ))}
              </div>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => fileInputRef.current?.click()}><Plus className="h-4 w-4" />更换文件</Button>
            </div>
            {busy && !sourcePreview && <div className="flex h-32 items-center justify-center text-xs text-slate-500"><Loader2 className="mr-2 h-4 w-4 animate-spin text-brand" />正在读取真实预览</div>}
            {sourcePreview && <SourceTablePreview preview={sourcePreview} />}
            <div className="flex justify-end">
              <Button variant="brand" disabled={!sourcePreview || busy} onClick={() => void startProfiling()}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}开始体检与建模
              </Button>
            </div>
          </div>
        )}
      </section>
    );
  }

  function renderProgress() {
    if (phase !== "profiling" && phase !== "executing") return null;
    return (
      <section className="ml-11 rounded-lg border border-brand/20 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand/10 text-brand"><Loader2 className="h-4 w-4 animate-spin" /></span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-4">
              <div><strong className="text-[13px] text-brand-dark">{phase === "profiling" ? "正在理解这份数据" : "正在全量生成"}</strong><p className="mt-0.5 text-xs text-slate-500">{progress.label}</p></div>
              <span className="text-xs font-bold tabular-nums text-brand">{Math.min(100, progress.percent)}%</span>
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-brand transition-[width] duration-500" style={{ width: `${Math.min(100, progress.percent)}%` }} /></div>
            <div className="mt-3 flex items-start gap-2 rounded-md bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
              <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{progressHint(phase, elapsed)}{progress.total > 0 && ` 已处理 ${progress.processed.toLocaleString("zh-CN")} / ${progress.total.toLocaleString("zh-CN")} 行。`}</span>
            </div>
          </div>
        </div>
      </section>
    );
  }

  function renderDirections() {
    if (phase !== "directions") return null;
    return (
      <section className="ml-11 space-y-4">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                <h2 className="text-sm font-bold text-brand-dark">源表体检完成</h2>
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                {dataset?.sheet_count ?? 0} 个 Sheet，共 {(dataset?.total_rows ?? 0).toLocaleString("zh-CN")} 行。AI 只保留有执行基础的制表方向。
              </p>
            </div>
            <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-semibold", blockingQuality.length ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700")}>
              {blockingQuality.length ? `${blockingQuality.length} 项阻断问题` : "可以继续制表"}
            </span>
          </div>
          {!!dataset?.quality_findings?.length && (
            <div className="mt-4 space-y-2">
              {dataset.quality_findings.slice(0, 3).map((finding, index) => (
                <div key={`${finding.code}-${index}`} className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{finding.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(300px,0.82fr)_minmax(440px,1.18fr)]">
          <div className="space-y-3">
            <div className="flex items-center justify-between px-1">
              <h2 className="text-sm font-bold text-brand-dark">有价值的制表方向</h2>
              <span className="text-[11px] text-slate-400">{directions.length ? `${directions.length} 个` : "无需硬凑数量"}</span>
            </div>
            {directions.map((direction) => {
              const active = direction.id === selected?.id;
              return (
                <button key={direction.id} type="button" onClick={() => setSelectedId(direction.id)}
                  className={cn("w-full rounded-lg border bg-white p-4 text-left shadow-sm transition-colors", active ? "border-brand ring-1 ring-brand/20" : "border-slate-200 hover:border-slate-300")}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-[13px] text-brand-dark">{direction.title}</strong>
                        {direction.recommended && <span className="rounded bg-brand/10 px-1.5 py-0.5 text-[10px] font-bold text-brand">推荐</span>}
                      </div>
                      <p className="mt-1 text-xs leading-5 text-slate-500">{direction.description}</p>
                    </div>
                    <span className={cn("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border", active ? "border-brand bg-brand text-white" : "border-slate-300 text-transparent")}><Check className="h-3 w-3" /></span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {direction.grain_label && <span className="rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-600">粒度：{direction.grain_label}</span>}
                    {!!direction.estimated_rows && <span className="rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-600">约 {direction.estimated_rows.toLocaleString("zh-CN")} 行</span>}
                  </div>
                </button>
              );
            })}
            {!directions.length && <div className="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-xs text-slate-500">没有足够可靠的自动推荐，请直接描述你希望得到的表。</div>}
            <button type="button" onClick={() => { setCustomOpen(true); setFineTune(false); }} className="flex w-full items-center justify-between rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-left text-xs font-semibold text-brand-dark hover:border-brand">
              这些都不合适，描述我想要的表<ChevronRight className="h-4 w-4 text-slate-400" />
            </button>
            {selected && <button type="button" onClick={() => { setFineTune(true); setCustomOpen(false); }} className="flex w-full items-center gap-2 px-2 py-1 text-xs font-semibold text-brand hover:text-brand-dark"><Pencil className="h-3.5 w-3.5" />微调当前方案</button>}
          </div>

          <aside className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            {selected ? (
              <div className="space-y-5">
                <div>
                  <span className="text-[10px] font-bold uppercase text-brand">当前方案详情</span>
                  <h3 className="mt-1 text-base font-bold text-brand-dark">{selected.title}</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">{selected.description}</p>
                </div>
                <div>
                  <h4 className="text-xs font-bold text-brand-dark">为什么选择这些关键字段</h4>
                  <div className="mt-2 flex flex-wrap gap-1.5">{selected.evidence.map((field) => <span key={field} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-semibold text-slate-700">{field}</span>)}</div>
                  {!!selected.evidence_notes?.length && <ul className="mt-2 space-y-1">{selected.evidence_notes.map((note) => <li key={note} className="flex items-start gap-2 text-[11px] leading-5 text-slate-500"><Check className="mt-1 h-3 w-3 shrink-0 text-emerald-600" />{note}</li>)}</ul>}
                </div>
                <div>
                  <h4 className="text-xs font-bold text-brand-dark">将生成</h4>
                  <div className="mt-2 flex flex-wrap gap-1.5">{selected.outputs.map((output) => <span key={output} className="rounded bg-brand/10 px-2 py-1 text-[11px] font-semibold text-brand">{output}</span>)}</div>
                </div>
                {selected.preview ? <TablePreview preview={selected.preview} /> : <div className="flex h-28 items-center justify-center rounded-lg border border-dashed border-slate-200 text-xs text-slate-400"><Eye className="mr-2 h-4 w-4" />选择后生成真实预览</div>}
              </div>
            ) : <div className="flex min-h-[320px] items-center justify-center text-xs text-slate-400">描述目标后，我会只生成最符合要求的方案。</div>}
          </aside>
        </div>

        {(customOpen || fineTune) && (
          <div className="rounded-lg border border-brand/20 bg-white p-4 shadow-sm">
            <label className="text-xs font-bold text-brand-dark">{fineTune ? "怎么调整当前方案？" : "你希望最终得到什么表？"}</label>
            <textarea value={customText} onChange={(event) => setCustomText(event.target.value)} rows={3} autoFocus
              placeholder={fineTune ? "例如：增加按季度汇总，并按净销售额从高到低排序" : "例如：按客户和月份归纳销售、退货、净销售额，并给出客户贡献排名"}
              className="mt-2 w-full resize-none rounded-md border border-slate-200 px-3 py-2 text-xs leading-5 outline-none focus:border-brand focus:ring-2 focus:ring-brand/10" />
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => { setCustomOpen(false); setFineTune(false); }}>取消</Button>
              <Button variant="brand" size="sm" disabled={!customText.trim() || busy} onClick={() => void preparePlan(customText)}><Send className="h-3.5 w-3.5" />让 AI 整理方案</Button>
            </div>
          </div>
        )}
        {!customOpen && !fineTune && selected && (
          <div className="flex justify-end"><Button variant="brand" disabled={blockingQuality.length > 0 || busy} onClick={() => void preparePlan()}><Check className="h-4 w-4" />选用这个方案</Button></div>
        )}
      </section>
    );
  }

  function renderAlignmentAndClarification() {
    if (phase !== "aligning" && phase !== "clarifying") return null;
    if (!plan || busy && phase === "aligning") {
      return (
        <section className="ml-11 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-3 text-sm font-semibold text-brand-dark"><Loader2 className="h-4 w-4 animate-spin text-brand" />AI 正在把需求整理成可执行方案</div>
          <p className="mt-2 pl-7 text-xs text-slate-500">它会明确字段、粒度、计算口径和输出表，不会直接开始生成。</p>
        </section>
      );
    }

    if (phase === "aligning") {
      return (
        <section className="ml-11 space-y-4">
          <div className="rounded-lg border border-brand/20 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand/10 text-brand"><MessageSquareText className="h-4 w-4" /></span>
              <div className="min-w-0 flex-1">
                <span className="text-[10px] font-bold uppercase text-brand">请确认我的理解</span>
                <h2 className="mt-1 text-base font-bold text-brand-dark">{plan.direction_title}</h2>
                <p className="mt-2 text-[13px] leading-6 text-slate-600">{plan.requirement_summary || plan.assistant_message}</p>
              </div>
            </div>

            <div className="mt-5 grid gap-3 md:grid-cols-2">
              <div className="rounded-lg bg-slate-50 p-4">
                <h3 className="text-xs font-bold text-brand-dark">关键字段与口径</h3>
                <dl className="mt-3 space-y-2 text-[11px]">
                  {Object.entries(plan.role_bindings).slice(0, 6).map(([role, field]) => <div key={role} className="flex items-center justify-between gap-3"><dt className="text-slate-400">{role}</dt><dd className="font-semibold text-slate-700">{field}</dd></div>)}
                </dl>
              </div>
              <div className="rounded-lg bg-slate-50 p-4">
                <h3 className="text-xs font-bold text-brand-dark">将生成的表</h3>
                <div className="mt-3 space-y-2">{plan.outputs.map((output) => <div key={output.key} className="rounded-md border border-slate-200 bg-white px-3 py-2"><strong className="text-[11px] text-brand-dark">{output.name}</strong><p className="mt-1 text-[10px] leading-4 text-slate-500">{[...output.group_by, ...output.metrics].join(" · ")}</p></div>)}</div>
              </div>
            </div>
            {planPreview && <div className="mt-4"><TablePreview preview={planPreview} /></div>}
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Button variant="ghost" onClick={() => { setPhase("directions"); setFineTune(true); }}><Pencil className="h-4 w-4" />还需要调整</Button>
              <Button variant="brand" onClick={confirmAlignment}><Check className="h-4 w-4" />理解正确，继续</Button>
            </div>
          </div>
        </section>
      );
    }

    return (
      <section className="ml-11 space-y-4">
        {plan.confirmed_facts.length > 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="flex items-center gap-2 text-xs font-bold text-brand-dark"><CheckCircle2 className="h-4 w-4 text-emerald-600" />已确认口径</h3>
            <div className="mt-2 space-y-1">{plan.confirmed_facts.map((fact, index) => <p key={`${fact.turn}-${index}`} className="text-[11px] leading-5 text-slate-500">{index + 1}. {fact.text}</p>)}</div>
          </div>
        )}
        {currentQuestion ? (
          <div className="rounded-lg border border-brand/20 bg-white p-5 shadow-sm">
            <span className="text-[10px] font-bold uppercase text-brand">生成前确认</span>
            <h2 className="mt-1 text-sm font-bold leading-6 text-brand-dark">{currentQuestion.text}</h2>
            {currentQuestion.evidence && <p className="mt-1 text-xs leading-5 text-slate-500">依据：{currentQuestion.evidence}</p>}
            {!!currentQuestion.options?.length && (
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {currentQuestion.options.map((option) => (
                  <button key={option.id} type="button" disabled={busy} onClick={() => void answerQuestion(option.message, currentQuestion.code)}
                    className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3 text-left transition-colors hover:border-brand hover:bg-brand/[0.025] disabled:opacity-50">
                    <span className="text-xs font-semibold text-brand-dark">{option.label}</span>
                    <span className="flex items-center gap-1 text-[10px] text-slate-400">{option.recommended && "建议"}<ChevronRight className="h-3.5 w-3.5" /></span>
                  </button>
                ))}
              </div>
            )}
            <div className="mt-4 flex items-end gap-2">
              <textarea value={answerText} onChange={(event) => setAnswerText(event.target.value)} rows={2} placeholder="也可以直接补充你的真实业务口径"
                className="min-h-[64px] flex-1 resize-none rounded-md border border-slate-200 px-3 py-2 text-xs leading-5 outline-none focus:border-brand focus:ring-2 focus:ring-brand/10" />
              <Button variant="brand" className="h-9" disabled={!answerText.trim() || busy} onClick={() => void answerQuestion(answerText, currentQuestion.code)}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}</Button>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-emerald-200 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-600" />
              <div><h2 className="text-sm font-bold text-brand-dark">制表口径已经对齐</h2><p className="mt-1 text-xs leading-5 text-slate-500">接下来会用源表全量数据确定性计算，同时生成可复用的逻辑脚本。</p></div>
            </div>
            <div className="mt-4 flex justify-end"><Button variant="brand" disabled={busy} onClick={() => void startExecution()}><Sparkles className="h-4 w-4" />生成表格与逻辑</Button></div>
          </div>
        )}
      </section>
    );
  }

  function renderResult() {
    if (phase !== "review" && phase !== "completed") return null;
    return (
      <section className="ml-11 space-y-4">
        <div className={cn("rounded-lg border bg-white p-5 shadow-sm", completed ? "border-emerald-200" : "border-slate-200")}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-600"><CheckCircle2 className="h-5 w-5" /></span>
              <div>
                <h2 className="text-sm font-bold text-brand-dark">{completed ? "表格已生成，逻辑已沉淀" : "表格和逻辑脚本已生成"}</h2>
                <p className="mt-1 text-xs leading-5 text-slate-500">处理 {(quality?.processed_rows ?? dataset?.total_rows ?? 0).toLocaleString("zh-CN")} 行，生成 {resultSheets.length} 个 Sheet。金额统一保留两位小数。</p>
              </div>
            </div>
            {quality && <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-bold", quality.can_complete ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700")}>{quality.can_complete ? "质量门禁通过" : "存在质量提醒"}</span>}
          </div>

          {quality && (
            <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-md bg-slate-50 px-3 py-2"><span className="text-[10px] text-slate-400">有效行</span><strong className="mt-0.5 block text-sm text-brand-dark">{quality.valid_rows.toLocaleString("zh-CN")}</strong></div>
              <div className="rounded-md bg-slate-50 px-3 py-2"><span className="text-[10px] text-slate-400">无效行</span><strong className="mt-0.5 block text-sm text-brand-dark">{quality.invalid_rows.toLocaleString("zh-CN")}</strong></div>
              <div className="rounded-md bg-slate-50 px-3 py-2"><span className="text-[10px] text-slate-400">对账检查</span><strong className="mt-0.5 block text-sm text-brand-dark">{quality.reconciliations.filter((item) => item.status === "passed").length}/{quality.reconciliations.length}</strong></div>
              <div className="rounded-md bg-slate-50 px-3 py-2"><span className="text-[10px] text-slate-400">待确认分类</span><strong className="mt-0.5 block text-sm text-brand-dark">{quality.pending_classification_count.toLocaleString("zh-CN")}</strong></div>
            </div>
          )}

          {Object.keys(resultSummary ?? {}).length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {resultSummary.source_sales && <span className="rounded-md border border-slate-200 px-3 py-2 text-[11px] text-slate-500">销售额 <strong className="ml-1 text-brand-dark">{resultSummary.source_sales}</strong></span>}
              {resultSummary.source_returns && <span className="rounded-md border border-slate-200 px-3 py-2 text-[11px] text-slate-500">退货额 <strong className="ml-1 text-brand-dark">{resultSummary.source_returns}</strong></span>}
              {resultSummary.source_net && <span className="rounded-md border border-slate-200 px-3 py-2 text-[11px] text-slate-500">净额 <strong className="ml-1 text-brand-dark">{resultSummary.source_net}</strong></span>}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex min-h-12 items-center gap-1 overflow-x-auto border-b border-slate-200 px-3">
            {resultSheets.map((sheet) => <button key={sheet.name} type="button" onClick={() => setActiveSheetName(sheet.name)} className={cn("h-12 whitespace-nowrap border-b-2 px-3 text-xs font-semibold", activeSheet?.name === sheet.name ? "border-brand text-brand" : "border-transparent text-slate-500 hover:text-brand-dark")}>{sheet.name}<span className="ml-1.5 text-[10px] font-normal text-slate-400">{sheet.total_rows.toLocaleString("zh-CN")} 行</span></button>)}
          </div>
          <div className="p-4">{activeSheet ? <ResultPreview sheet={activeSheet} /> : <div className="flex h-40 items-center justify-center text-xs text-slate-400">暂无结果预览</div>}</div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-emerald-50 text-emerald-600"><FileSpreadsheet className="h-4 w-4" /></span>
            <div className="min-w-0 flex-1"><strong className="block truncate text-xs text-brand-dark">{plan?.direction_title ?? "智能制表结果"}.xlsx</strong><span className="text-[10px] text-slate-400">可下载的全量目标表</span></div>
            <Button variant="ghost" size="icon" title="下载 Excel" onClick={() => void downloadResult()}><Download className="h-4 w-4" /></Button>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-sky-50 text-sky-600"><FileCode2 className="h-4 w-4" /></span>
            <div className="min-w-0 flex-1"><strong className="block truncate text-xs text-brand-dark">可复用逻辑脚本</strong><span className="text-[10px] text-slate-400">通过确认后导入我的逻辑库</span></div>
            {completed ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Button variant="ghost" size="icon" title="导入逻辑库" onClick={() => setImportOpen(true)}><Library className="h-4 w-4" /></Button>}
          </div>
        </div>

        <div className="flex flex-wrap justify-between gap-2">
          <Button variant="ghost" onClick={() => { setPhase("directions"); setFineTune(true); }}><Pencil className="h-4 w-4" />调整方案</Button>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void downloadResult()}><Download className="h-4 w-4" />下载 Excel</Button>
            {!completed && <Button variant="brand" disabled={quality ? !quality.can_complete : false} onClick={() => setImportOpen(true)}><Library className="h-4 w-4" />导入逻辑库</Button>}
            {completed && <Button variant="brand" onClick={resetWorkspace}><Plus className="h-4 w-4" />开始新制表</Button>}
          </div>
        </div>
      </section>
    );
  }

  const activeStage = stageIndex(phase);

  return (
    <>
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-[#F3F5F7]/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1360px] items-start justify-between gap-6 px-6 pb-4 pt-7 lg:px-8">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {phase !== "upload" && <button type="button" aria-label="返回智能制表首页" onClick={() => setExitOpen(true)} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-white hover:text-brand-dark"><ArrowLeft className="h-4 w-4" /></button>}
              <h1 className="text-[24px] font-bold text-brand-dark">智能制表</h1>
              {demoMode && <span className="rounded bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-700">交互演示</span>}
            </div>
            <p className={cn("mt-0.5 text-xs font-medium text-slate-500", phase !== "upload" && "pl-10")}>从一张源表出发，把业务问题变成可复用的目标表</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)}><History className="h-4 w-4" />历史对话{history.length > 0 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{history.length}</span>}</Button>
        </div>
        <div className="mx-auto flex w-full max-w-[1360px] items-center px-6 pb-4 lg:px-8">
          {STAGES.map((stage, index) => (
            <React.Fragment key={stage.label}>
              <div className="flex min-w-0 items-center gap-2">
                <span className={cn("flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold", index < activeStage ? "border-emerald-600 bg-emerald-600 text-white" : index === activeStage ? "border-brand bg-brand text-white" : "border-slate-300 bg-white text-slate-400")}>{index < activeStage ? <Check className="h-3 w-3" /> : index + 1}</span>
                <span className={cn("whitespace-nowrap text-[11px] font-semibold", index === activeStage ? "text-brand-dark" : "text-slate-400")}>{stage.label}</span>
              </div>
              {index < STAGES.length - 1 && <span className={cn("mx-3 h-px min-w-4 flex-1", index < activeStage ? "bg-emerald-300" : "bg-slate-200")} />}
            </React.Fragment>
          ))}
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1120px] px-6 py-6 lg:px-8">
        <div className="space-y-4">
          {feed.map((item) => <AgentMessage key={item.id} item={item} />)}
          <div id="compose-current-step" className="scroll-mt-[148px]">
            {renderUpload()}
            {renderProgress()}
            {renderDirections()}
            {renderAlignmentAndClarification()}
            {renderResult()}
            {phase === "error" && (
              <section className="ml-11 rounded-lg border border-red-200 bg-white p-5 shadow-sm">
                <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-4 w-4 text-red-600" /><div><strong className="text-sm text-brand-dark">当前步骤未完成</strong><p className="mt-1 text-xs text-slate-500">会话已保存在历史对话中，可以稍后继续或重新上传。</p></div></div>
                <div className="mt-4 flex justify-end"><Button variant="outline" onClick={resetWorkspace}><RotateCcw className="h-4 w-4" />重新开始</Button></div>
              </section>
            )}
          </div>
        </div>
      </main>

      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="max-w-2xl rounded-lg">
          <DialogHeader><DialogTitle>历史对话</DialogTitle><DialogDescription>已完成和未完成的智能制表会话都会保留在当前浏览器中。</DialogDescription></DialogHeader>
          <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
            {!history.length && <div className="flex h-36 items-center justify-center rounded-lg border border-dashed border-slate-200 text-xs text-slate-400">还没有历史对话</div>}
            {history.map((entry) => (
              <button key={entry.id} type="button" onClick={() => restoreHistory(entry)} className="flex w-full items-center gap-3 rounded-lg border border-slate-200 bg-white p-3 text-left hover:border-brand">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-500"><FileSpreadsheet className="h-4 w-4" /></span>
                <span className="min-w-0 flex-1"><strong className="block truncate text-xs text-brand-dark">{entry.title}</strong><span className="mt-0.5 block truncate text-[10px] text-slate-400">{entry.fileNames.join("、") || "源表会话"} · {new Date(entry.updatedAt).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span></span>
                <span className={cn("rounded px-2 py-1 text-[10px] font-semibold", entry.status === "completed" ? "bg-emerald-50 text-emerald-700" : entry.status === "ready" ? "bg-sky-50 text-sky-700" : "bg-amber-50 text-amber-700")}>{entry.status === "completed" ? "已入库" : entry.status === "ready" ? "待入库" : "未完成"}</span>
                <span role="button" tabIndex={0} title="删除记录" onClick={(event) => { event.stopPropagation(); deleteHistory(entry.id); }} onKeyDown={(event) => { if (event.key === "Enter") { event.stopPropagation(); deleteHistory(entry.id); } }} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-red-50 hover:text-red-600"><Trash2 className="h-4 w-4" /></span>
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={exitOpen} onOpenChange={setExitOpen}>
        <DialogContent className="max-w-sm rounded-lg">
          <DialogHeader><DialogTitle>返回智能制表首页？</DialogTitle><DialogDescription>当前进度会保留在历史对话中，你可以稍后继续。</DialogDescription></DialogHeader>
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setExitOpen(false)}>继续制表</Button><Button variant="brand" onClick={() => { setExitOpen(false); resetWorkspace(); }}>确认返回</Button></div>
        </DialogContent>
      </Dialog>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="max-w-md rounded-lg">
          <DialogHeader><DialogTitle>导入我的逻辑库？</DialogTitle><DialogDescription>目标表和逻辑脚本已经生成。确认后，同类源表可直接复用这套转换方式。</DialogDescription></DialogHeader>
          <label className="text-xs font-semibold text-brand-dark">逻辑名称<input value={logicName} onChange={(event) => setLogicName(event.target.value)} className="mt-2 h-9 w-full rounded-md border border-slate-200 px-3 text-xs outline-none focus:border-brand focus:ring-2 focus:ring-brand/10" /></label>
          <div className="flex justify-end gap-2"><Button variant="ghost" disabled={busy} onClick={() => setImportOpen(false)}>暂不导入</Button><Button variant="brand" disabled={!logicName.trim() || busy} onClick={() => void importLogic()}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Library className="h-4 w-4" />}确认导入</Button></div>
        </DialogContent>
      </Dialog>
    </>
  );
}

/**
 * 智能制表展示层类型与 preview fixture。
 * 示意数据仅用于本地 UI 预览，不代表真实计算结果。
 */

export type ComposePhase =
  | "upload"
  | "profiling"
  | "directions"
  | "clarifying"
  | "executing"
  | "review"
  | "completing"
  | "completed"
  | "error";

/** 预览用四步主状态（与 mockup 状态切换器对应） */
export type ComposePreviewStep = "upload" | "direction" | "clarify" | "result";

export type ComposeFileStatus = "pending" | "scanning" | "ready" | "failed";

export interface ComposeSourceFile {
  id: string;
  name: string;
  sheetCount: number;
  rowCount: number;
  status: ComposeFileStatus;
  error?: string;
}

export interface DatasetSummary {
  title: string;
  subtitle: string;
  fileCount: number;
  sheetCount: number;
  plannedRows: number;
  fieldCount: number;
  scopeLabel: string;
  scopeVariant: "sample" | "full" | "plan";
  planningMode?: string;
  planningNotice?: string;
  qualityFindings?: { severity: string; message: string }[];
}

export interface FieldRoleMapping {
  rawField: string;
  role: string;
}

export interface DirectionOption {
  id: string;
  title: string;
  description: string;
  outputs: string[];
  assessment?: {
    dataEvidence: string;
    executability: string;
    businessFit: string;
  };
  grainLabel?: string;
  estimatedRows?: number | null;
  evidence?: string[];
  warnings?: string[];
  preview?: import("@/lib/types").ComposeTablePreview | null;
}

export interface PlanOutputSheet {
  name: string;
  fields: string;
  columnCount: string;
  confirmed: boolean;
}

export interface PlanFormula {
  label: string;
  definition: string;
}

export interface PlanClassification {
  label: string;
  value: string;
}

export interface StructuredPlan {
  title: string;
  subtitle: string;
  outputSheets: PlanOutputSheet[];
  formulas: PlanFormula[];
  classifications: PlanClassification[];
  confirmedCount: number;
  pendingQuestionCount: number;
  metricCount: number;
  footerNote: string;
  readyForExecution?: boolean;
  blockingQuestions?: string[];
  modelNotice?: string;
  grainLabel?: string;
  timeGrain?: "year" | "quarter" | "month" | null;
  dimensionLabels?: string[];
  metricLabels?: string[];
  zeroAmountPolicy?: string;
  requirementSummary?: string;
  intentConfirmationPending?: boolean;
  tableMode?: "detail" | "aggregate";
}

export type ChatRole = "assistant" | "user";

export interface QuickOption {
  id: string;
  label: string;
  message?: string;
  recommended?: boolean;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  evidence?: string;
  quickOptions?: QuickOption[];
  selectedOptionId?: string;
}

export type ReconciliationStatus = "passed" | "failed" | "pending";

export interface ReconciliationItem {
  name: string;
  label: string;
  status: ReconciliationStatus;
  delta?: string;
}

export interface QualitySummaryData {
  scope: "sample" | "full";
  plannedRows: number;
  processedRows: number;
  validRows: number;
  invalidRows: number;
  skippedRows: number;
  sourceFingerprint: string;
  reconciliations: ReconciliationItem[];
  canComplete: boolean;
  blockReason?: string;
  headline: string;
  subline: string;
  facts: { value: string; label: string }[];
  warnings: string[];
  datasetLabel?: string;
  rowsLabel?: string;
  rowsPassed?: boolean;
  reconciliationLabel?: string;
  reconciliationPassed?: boolean;
  classificationLabel?: string;
}

export interface ResultTableColumn {
  key: string;
  label: string;
  align?: "left" | "right";
  width?: string;
}

export interface ResultTableRow {
  id: string;
  cells: Record<string, string | number>;
  pill?: { column: string; variant: "default" | "brand" | "amber" };
  action?: { column: string; label: string };
}

export interface ResultSheetTab {
  id: string;
  label: string;
  countLabel: string;
  caption: string;
  captionRight?: string;
  columns: ResultTableColumn[];
  rows: ResultTableRow[];
  totalRows: number;
  pageSize: number;
  serverPaginated?: boolean;
  live?: boolean;
}

export interface ComposeWorkspaceProps {
  phase: ComposePhase;
  previewStep?: ComposePreviewStep;
  files: ComposeSourceFile[];
  dataset: DatasetSummary | null;
  fieldRoles: FieldRoleMapping[];
  directions: DirectionOption[];
  selectedDirectionId: string | null;
  customPrompt: string;
  plan: StructuredPlan | null;
  chatMessages: ChatMessage[];
  chatRound: number;
  quality: QualitySummaryData | null;
  resultTabs: ResultSheetTab[];
  activeResultTabId: string;
  resultPage: number;
  completed: boolean;
  profilingProgress?: { label: string; percent: number };
  executingProgress?: { label: string; processed: number; total: number };
  errorMessage?: string;
  planPreview?: import("@/lib/types").ComposeTablePreview | null;
  onPreviewStepChange?: (step: ComposePreviewStep) => void;
  onFilesAdd?: (files: FileList) => void;
  onFileRemove?: (id: string) => void;
  onStartProfiling?: () => void;
  onCancelProfiling?: () => void;
  onSelectDirection?: (id: string) => void;
  onCustomPromptChange?: (value: string) => void;
  onSubmitDirection?: () => void;
  onBackToDirections?: () => void;
  onExecutePlan?: () => void;
  onSendMessage?: (text: string, optionId?: string) => void;
  onSelectQuickOption?: (messageId: string, optionId: string) => void;
  onResultTabChange?: (tabId: string) => void;
  onResultPageChange?: (page: number) => void;
  onDownloadExcel?: () => void;
  onAdjustPlan?: () => void;
  onCompleteTable?: () => void;
  onEnterAnalysis?: () => void;
  onClassificationAction?: (rowId: string) => void;
  onRetry?: () => void;
}

// ── Preview fixtures（示意数据）────────────────────────────────────────────

export const PREVIEW_SOURCE_FILES: ComposeSourceFile[] = [
  {
    id: "f1",
    name: "销售数据（1-3月）.xlsx",
    sheetCount: 5,
    rowCount: 275426,
    status: "ready",
  },
  {
    id: "f2",
    name: "销售数据（4-6月）.xlsx",
    sheetCount: 5,
    rowCount: 282866,
    status: "ready",
  },
  {
    id: "f3",
    name: "销售数据（7-9月）.xlsx",
    sheetCount: 5,
    rowCount: 277676,
    status: "ready",
  },
  {
    id: "f4",
    name: "销售数据（10-12月）.xlsx",
    sheetCount: 5,
    rowCount: 310134,
    status: "ready",
  },
];

export const PREVIEW_DATASET_DIRECTION: DatasetSummary = {
  title: "2025 年销售流水",
  subtitle: "4 个文件结构一致，可纵向合并",
  fileCount: 4,
  sheetCount: 20,
  plannedRows: 1146102,
  fieldCount: 12,
  scopeLabel: "推荐基于分层抽样",
  scopeVariant: "sample",
};

export const PREVIEW_DATASET_PLAN: DatasetSummary = {
  title: "客户销售汇总与终端类型结构",
  subtitle: "当前方案随对话实时更新",
  fileCount: 3,
  sheetCount: 5,
  plannedRows: 2,
  fieldCount: 1,
  scopeLabel: "确认后执行全量数据",
  scopeVariant: "plan",
};

export const PREVIEW_DATASET_RESULT: DatasetSummary = {
  title: "客户销售结构目标表",
  subtitle: "2025-01-02 至 2025-12-31",
  fileCount: 4,
  sheetCount: 20,
  plannedRows: 1146102,
  fieldCount: 1146102,
  scopeLabel: "结果基于全量数据",
  scopeVariant: "full",
};

export const PREVIEW_FIELD_ROLES: FieldRoleMapping[] = [
  { rawField: "往来单位编号", role: "实体编号" },
  { rawField: "往来单位名称", role: "实体名称" },
  { rawField: "日期", role: "交易日期" },
  { rawField: "含税金额", role: "金额" },
  { rawField: "单据类型", role: "交易类型" },
  { rawField: "单据编号", role: "单据标识" },
];

export const PREVIEW_DIRECTIONS: DirectionOption[] = [
  {
    id: "d1",
    title: "客户销售汇总与终端类型结构",
    description:
      "按往来单位汇总销售、退货和净销售额，再识别医院、药店、诊所等终端类型。",
    outputs: ["客户汇总", "类型占比", "待确认分类"],
  },
  {
    id: "d2",
    title: "月度与季度销售退货汇总",
    description: "按月份和季度汇总正向销售、退货金额、净销售额和单据数量。",
    outputs: ["月度汇总", "季度汇总"],
  },
  {
    id: "d3",
    title: "部门与业务员销售汇总",
    description: "按组织和人员维度制作销售汇总表，保留客户数、单据数和净销售额。",
    outputs: ["部门汇总", "业务员汇总"],
  },
  {
    id: "d4",
    title: "退货客户与退货明细",
    description: "筛选退货业务，按客户统计退货金额、退货单数和退货占比。",
    outputs: ["退货客户", "退货明细"],
  },
];

export const PREVIEW_PLAN: StructuredPlan = {
  title: "目标表方案",
  subtitle: "先确认表结构和业务口径，再开始全量计算。",
  outputSheets: [
    {
      name: "客户销售汇总",
      fields: "客户编号、名称、终端类型、销售、退货、净销售额",
      columnCount: "约 11 列",
      confirmed: true,
    },
    {
      name: "终端类型汇总",
      fields: "类型、客户数、客户数占比、净销售额、金额占比",
      columnCount: "约 8 列",
      confirmed: true,
    },
    {
      name: "待确认分类",
      fields: "客户编号、名称、候选类型、依据、用户确认",
      columnCount: "约 6 列",
      confirmed: true,
    },
  ],
  formulas: [
    { label: "销售金额", definition: "正向金额" },
    { label: "退货金额", definition: "负向金额绝对值" },
    { label: "净销售额", definition: "销售 - 退货" },
    { label: "客户主键", definition: "编号 + 名称" },
    { label: "占比", definition: "客户数 + 净销售额" },
  ],
  classifications: [
    { label: "规则包", value: "医药客户终端类型 v1" },
    { label: "分类对象", value: "从全量数据提取的唯一往来单位" },
    { label: "低置信结果", value: "进入「待确认分类」，不静默归入其他" },
    { label: "已确认", value: "退货单独列示，同时计算净销售额" },
  ],
  confirmedCount: 2,
  pendingQuestionCount: 1,
  metricCount: 5,
  footerNote: "计划处理 4 个文件、20 个 Sheet、1,146,102 行。",
};

export const PREVIEW_CHAT_MESSAGES: ChatMessage[] = [
  {
    id: "m1",
    role: "assistant",
    content: "我会按往来单位汇总销售，并输出终端类型占比。退货金额希望如何处理？",
  },
  {
    id: "m2",
    role: "user",
    content: "退货单独一列，同时计算净销售额。",
  },
  {
    id: "m3",
    role: "assistant",
    content:
      "已记录。名称中包含「卫生室」的客户，是否要合并到「诊所」类型？这个口径会影响客户数占比。",
    quickOptions: [
      { id: "o1", label: "归入诊所" },
      { id: "o2", label: "保留为独立类型" },
      { id: "o3", label: "先放入待确认" },
    ],
    selectedOptionId: "o1",
  },
];

export const PREVIEW_QUALITY: QualitySummaryData = {
  scope: "full",
  plannedRows: 1146102,
  processedRows: 1146102,
  validRows: 1146102,
  invalidRows: 0,
  skippedRows: 0,
  sourceFingerprint: "SG-2025-7F2A...91C8",
  reconciliations: [
    {
      name: "source_to_customer_amount",
      label: "源表 → 客户汇总",
      status: "passed",
      delta: "0.00",
    },
    {
      name: "customer_to_category_amount",
      label: "客户 → 类型汇总",
      status: "passed",
      delta: "0.00",
    },
  ],
  canComplete: true,
  headline: "全量计算与质量检查已完成",
  subline:
    "全部源数据已读取，未发现遗漏 Sheet；页面表格仅分页展示，导出包含完整结果。",
  facts: [
    { value: "1,146,102 / 1,146,102", label: "已处理 / 计划行数" },
    { value: "三层金额一致", label: "源数据 → 客户 → 类型" },
    { value: "0 个结构冲突", label: "20 个 Sheet 同构" },
  ],
  warnings: [
    "存在低置信分类。它们已单独进入「待确认分类」，未被静默并入其他类型。",
  ],
};

export const PREVIEW_RESULT_TABS: ResultSheetTab[] = [
  {
    id: "customer",
    label: "客户销售汇总",
    countLabel: "完整",
    caption: "按往来单位编号 + 名称汇总 · 下表金额仅用于布局示意",
    captionRight: "净销售额 = 销售金额 - 退货金额",
    columns: [
      { key: "id", label: "往来单位编号", width: "105px" },
      { key: "name", label: "往来单位名称", width: "180px" },
      { key: "type", label: "终端类型", width: "90px" },
      { key: "orders", label: "销售单数", align: "right", width: "84px" },
      { key: "sales", label: "销售金额", align: "right", width: "112px" },
      { key: "returns", label: "退货金额", align: "right", width: "105px" },
      { key: "net", label: "净销售额", align: "right", width: "112px" },
      { key: "share", label: "占比", align: "right", width: "80px" },
    ],
    rows: [
      {
        id: "r1",
        cells: {
          id: "C000128",
          name: "示例市第一人民医院",
          type: "医院",
          orders: "1,286",
          sales: "8,420,516.20",
          returns: "126,503.00",
          net: "8,294,013.20",
          share: "4.82%",
        },
        pill: { column: "type", variant: "default" },
      },
      {
        id: "r2",
        cells: {
          id: "C000247",
          name: "示例大药房连锁有限公司",
          type: "连锁药店",
          orders: "965",
          sales: "6,318,240.00",
          returns: "84,120.50",
          net: "6,234,119.50",
          share: "3.62%",
        },
        pill: { column: "type", variant: "brand" },
      },
      {
        id: "r3",
        cells: {
          id: "C000391",
          name: "示例区同康诊所",
          type: "诊所",
          orders: "522",
          sales: "3,140,820.60",
          returns: "48,300.00",
          net: "3,092,520.60",
          share: "1.80%",
        },
        pill: { column: "type", variant: "amber" },
      },
      {
        id: "r4",
        cells: {
          id: "C000518",
          name: "示例医药商业有限公司",
          type: "商业公司",
          orders: "488",
          sales: "2,998,410.00",
          returns: "57,118.90",
          net: "2,941,291.10",
          share: "1.71%",
        },
        pill: { column: "type", variant: "default" },
      },
      {
        id: "r5",
        cells: {
          id: "C000673",
          name: "示例街道社区卫生服务中心",
          type: "社区卫生",
          orders: "407",
          sales: "2,104,677.00",
          returns: "31,552.00",
          net: "2,073,125.00",
          share: "1.20%",
        },
        pill: { column: "type", variant: "amber" },
      },
    ],
    totalRows: 4053,
    pageSize: 5,
  },
  {
    id: "type",
    label: "终端类型汇总",
    countLabel: "9",
    caption: "终端类型汇总 · 下表客户数和金额仅用于布局示意",
    captionRight: "客户数占比与金额占比分开计算",
    columns: [
      { key: "type", label: "客户终端类型", width: "160px" },
      { key: "count", label: "客户数", align: "right", width: "100px" },
      { key: "countShare", label: "客户数占比", align: "right", width: "115px" },
      { key: "sales", label: "销售金额", align: "right", width: "145px" },
      { key: "returns", label: "退货金额", align: "right", width: "135px" },
      { key: "net", label: "净销售额", align: "right", width: "145px" },
      { key: "netShare", label: "净销售额占比", align: "right", width: "120px" },
    ],
    rows: [
      {
        id: "t1",
        cells: {
          type: "医院",
          count: "1,284",
          countShare: "31.68%",
          sales: "68,420,516.20",
          returns: "1,126,503.00",
          net: "67,294,013.20",
          netShare: "39.08%",
        },
      },
      {
        id: "t2",
        cells: {
          type: "连锁药店",
          count: "936",
          countShare: "23.09%",
          sales: "42,318,240.00",
          returns: "884,120.50",
          net: "41,434,119.50",
          netShare: "24.06%",
        },
      },
      {
        id: "t3",
        cells: {
          type: "诊所",
          count: "721",
          countShare: "17.79%",
          sales: "26,140,820.60",
          returns: "548,300.00",
          net: "25,592,520.60",
          netShare: "14.86%",
        },
      },
      {
        id: "t4",
        cells: {
          type: "社区卫生机构",
          count: "468",
          countShare: "11.55%",
          sales: "18,104,677.00",
          returns: "331,552.00",
          net: "17,773,125.00",
          netShare: "10.32%",
        },
      },
      {
        id: "t5",
        cells: {
          type: "其他及待确认",
          count: "644",
          countShare: "15.89%",
          sales: "20,510,420.00",
          returns: "405,138.00",
          net: "20,105,282.00",
          netShare: "11.68%",
        },
      },
    ],
    totalRows: 9,
    pageSize: 5,
  },
  {
    id: "pending",
    label: "待确认分类",
    countLabel: "示意",
    caption: "低置信结果不会静默归入「其他」 · 下表为布局示意",
    captionRight: "确认后重新执行分类汇总与对账",
    columns: [
      { key: "id", label: "往来单位编号", width: "115px" },
      { key: "name", label: "往来单位名称", width: "210px" },
      { key: "suggest", label: "建议类型", width: "110px" },
      { key: "reason", label: "判断依据", width: "200px" },
      { key: "status", label: "置信状态", width: "95px" },
      { key: "confirm", label: "用户确认类型", width: "145px" },
    ],
    rows: [
      {
        id: "p1",
        cells: {
          id: "C001018",
          name: "示例健康服务部",
          suggest: "诊所",
          reason: "名称含「健康服务」，证据不足",
          status: "待确认",
          confirm: "",
        },
        pill: { column: "status", variant: "amber" },
        action: { column: "confirm", label: "选择类型" },
      },
      {
        id: "p2",
        cells: {
          id: "C001231",
          name: "示例便民服务站",
          suggest: "社区卫生",
          reason: "名称含「服务站」，存在歧义",
          status: "待确认",
          confirm: "",
        },
        pill: { column: "status", variant: "amber" },
        action: { column: "confirm", label: "选择类型" },
      },
      {
        id: "p3",
        cells: {
          id: "C001407",
          name: "示例康复中心",
          suggest: "医院",
          reason: "无法判断是否为医疗机构",
          status: "待确认",
          confirm: "",
        },
        pill: { column: "status", variant: "amber" },
        action: { column: "confirm", label: "选择类型" },
      },
    ],
    totalRows: 3,
    pageSize: 5,
  },
];

/** 将 ComposePhase 映射到预览四步 */
export function phaseToPreviewStep(phase: ComposePhase): ComposePreviewStep {
  switch (phase) {
    case "upload":
    case "profiling":
    case "error":
      return "upload";
    case "directions":
      return "direction";
    case "clarifying":
      return "clarify";
    case "executing":
    case "review":
    case "completing":
    case "completed":
      return "result";
    default:
      return "upload";
  }
}

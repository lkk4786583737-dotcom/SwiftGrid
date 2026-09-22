// 纯前端演示用假数据 + 模拟 SSE 流（NEXT_PUBLIC_MOCK=true 时启用）。
// 形状严格对齐 lib/types.ts，便于后端就绪后无缝切换。

import type {
  ChartsResp,
  ComposeDataset,
  ComposeDirection,
  ComposeExecuteSuccess,
  ComposeImportResp,
  ComposePlan,
  ComposePlanResp,
  ComposeResultPage,
  LogicMeta,
  MatchResp,
  ModelsResp,
  ParseResp,
  SessionResp,
  Sheet,
  SSEType,
} from "./types";
import type { SSEController, SSEHandler } from "./api";

export const models: ModelsResp = {
  recommended: "deepseek-v4-pro",
  options: [
    { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro（推荐）", base_url: "https://api.deepseek.com" },
    { id: "deepseek-v4-flash", label: "DeepSeek V4 Flash", base_url: "https://api.deepseek.com" },
    { id: "custom", label: "企业私有部署（自填 base_url / model）", base_url: "" },
  ],
};

export let logics: LogicMeta[] = [
  {
    id: "lg_001",
    name: "渠道转化率核算 · 过滤异常后按渠道汇总",
    created_at: "2025-06-12",
    last_used_at: "2025-06-20",
  },
  {
    id: "lg_002",
    name: "大区销量透视 · 门店×品类月度",
    created_at: "2025-06-08",
    last_used_at: "2025-06-18",
  },
  {
    id: "lg_003",
    name: "工资核算 · 在职员工实发降序",
    created_at: "2025-06-01",
    last_used_at: "2025-06-15",
  },
];

let composeMode: "import" | "execute" = "import";

const MOCK_COMPOSE_DATASET: ComposeDataset = {
  session_id: "compose_mock",
  file_count: 1,
  sheet_count: 1,
  planned_rows: 128,
  total_rows: 128,
  valid_rows: 128,
  skipped_rows: 0,
  source_fingerprint: "mock-compose-fingerprint",
  schema: { columns: ["往来单位编号", "往来单位名称", "含税金额"], signature: "mock" },
  sheets: [{ file: "演示销售流水.xlsx", sheet: "销售流水", columns: ["往来单位编号", "往来单位名称", "含税金额"], signature: "mock", rows: 128 }],
  sample: { scope: "per_sheet_reservoir_sample", rows: 128, max_per_sheet: 300 },
  fields: [
    { name: "往来单位编号", role: "entity_id", confidence: 1, null_rate: 0, unique_count: 18, examples: ["C001"], sample_rows: 128 },
    { name: "往来单位名称", role: "entity_name", confidence: 1, null_rate: 0, unique_count: 18, examples: ["演示医院"], sample_rows: 128 },
    { name: "含税金额", role: "amount", confidence: 1, null_rate: 0, unique_count: 90, examples: [12000], sample_rows: 128 },
  ],
};

const MOCK_COMPOSE_DIRECTIONS: ComposeDirection[] = [
  { id: "customer_terminal", title: "客户销售汇总与终端类型结构", description: "按客户汇总销售、退货、净销售额，并生成分类对账表。", evidence: ["往来单位编号", "往来单位名称", "含税金额"], outputs: ["客户销售汇总", "客户终端类型汇总", "待确认分类"], recommended: true },
  { id: "customer_sales", title: "按往来单位汇总销售与退货", description: "保留客户层级的销售、退货和金额对账。", evidence: ["往来单位编号", "含税金额"], outputs: ["客户销售汇总", "金额质量对账"], recommended: false },
  { id: "transaction_quality", title: "交易流水汇总与质量对账", description: "输出全量读取、异常行和金额对账结果。", evidence: ["含税金额"], outputs: ["交易汇总", "金额质量对账"], recommended: false },
];

const MOCK_COMPOSE_PLAN: ComposePlan = {
  schema_version: 1,
  dataset_id: "compose_mock",
  direction_id: "customer_terminal",
  direction_title: "客户销售汇总与终端类型结构",
  output_kind: "classification",
  role_bindings: { entity_id: "往来单位编号", entity_name: "往来单位名称", amount: "含税金额" },
  business_rules: { positive_amount: "amount > 0", negative_amount: "amount < 0", net_amount: "sales_amount - return_amount" },
  filters: [],
  time_grain: null,
  dimensions: [],
  outputs: [
    { name: "客户销售汇总", key: "customer_sales", group_by: ["entity_id", "entity_name"], metrics: ["sales_amount", "return_amount", "net_amount"] },
    { name: "客户终端类型汇总", key: "terminal_summary", group_by: ["terminal_type"], metrics: ["customer_count", "net_amount"] },
    { name: "待确认分类", key: "pending_classification", group_by: ["entity_id", "entity_name"], metrics: ["suggested_type"] },
  ],
  classification: { enabled: true, rule_pack: "generic_terminal_v1", rule_pack_version: "1.0", rules: [{ label: "医院", keywords: ["医院"] }, { label: "药店", keywords: ["药店"] }, { label: "诊所", keywords: ["诊所"] }], unresolved_policy: "pending_review" },
  confirmed_facts: [],
  questions: [],
  ready_for_execution: true,
};

const MOCK_COMPOSE_RESULT_ROWS: Record<string, unknown[][]> = {
  客户销售汇总: [["C001", "演示医院", "医院", "规则匹配", "关键词：医院", 12, 1, "12000.00", "400.00", "11600.00", "32.22%", "2026-03-31"]],
  客户终端类型汇总: [["医院", 1, "50%", "12000.00", "400.00", "11600.00", "55.23%", "3.33%"], ["待确认", 1, "50%", "10000.00", "0.00", "10000.00", "44.77%", "0.00%"]],
  待确认分类: [["C002", "演示服务站", "", "未命中分类规则", "待确认", ""]],
};

const MOCK_COMPOSE_QUALITY = {
  scope: "full" as const,
  planned_rows: 128,
  processed_rows: 128,
  valid_rows: 128,
  invalid_rows: 0,
  skipped_rows: 0,
  source_fingerprint: MOCK_COMPOSE_DATASET.source_fingerprint,
  reconciliations: [
    { name: "source_to_customer_amount", status: "passed", delta: "0.00" },
    { name: "customer_to_category_amount", status: "passed", delta: "0.00" },
  ],
  pending_classification_count: 1,
  can_complete: false,
};

const sourcePreview: Sheet = {
  name: "销售明细",
  columns: ["日期", "门店", "品类", "销量", "单价", "金额"],
  rows: 248,
  total_rows: 248,
  preview: [
    ["2025-01", "华东店", "饮料", 980, 6.5, 6370],
    ["2025-01", "华东店", "零食", 420, 9.0, 3780],
    ["2025-02", "华北店", "饮料", 360, 6.5, 2340],
    ["2025-02", "华南店", "乳品", 540, 12.0, 6480],
    ["2025-03", "华东店", "饮料", 760, 6.5, 4940],
  ],
};

const resultPreview: Sheet = {
  name: "结果表",
  columns: ["门店", "品类", "1月", "2月", "3月"],
  rows: 5,
  total_rows: 5,
  preview: [
    ["华东店", "饮料", 2900, 950, 2350],
    ["华东店", "零食", 1300, 1800, 1900],
    ["华北店", "饮料", 700, 1260, 820],
    ["华北店", "乳品", 1540, 1120, 1680],
    ["华南店", "饮料", 2210, 990, 1430],
  ],
};

let parseSeq = 0;
export function parseInput(fd: FormData): Promise<ParseResp> {
  parseSeq += 1;
  const slot = (fd.get("slot") as string) || "source";
  const sheet: Sheet =
    slot === "target"
      ? { ...resultPreview, name: "目标表" }
      : { ...sourcePreview };
  return delay(420, {
    handle: `h_${slot}_${parseSeq}`,
    sheets: [sheet],
    oversize: false,
  });
}

export function deleteLogic(id: string) {
  logics = logics.filter((l) => l.id !== id);
  return Promise.resolve({ ok: true });
}
export function renameLogic(id: string, name: string) {
  logics = logics.map((l) => (l.id === id ? { ...l, name } : l));
  return Promise.resolve({ ok: true });
}

// 学习流：第 1 次走澄清，澄清后第 2 次成功（演示 HITL 闭环）。
let learnRound = 0;
export function learnStart(): Promise<SessionResp> {
  learnRound = 0;
  return delay(180, { session_id: "sess_learn_mock" });
}
export function learnClarify(clarification: string) {
  learnRound += 1;
  return delay(120, { ok: true, round: learnRound });
}
export function learnSave(name: string) {
  const id = `lg_${Date.now()}`;
  logics = [
    { id, name, created_at: today(), last_used_at: today() },
    ...logics,
  ];
  return delay(160, { id });
}

export function runMatch(): Promise<MatchResp> {
  return delay(360, {
    auto: false,
    best: { id: "lg_001", name: "渠道转化率核算 · 过滤异常后按渠道汇总", score: 0.92 },
    candidates: [
      { id: "lg_001", name: "渠道转化率核算 · 过滤异常后按渠道汇总", score: 0.92 },
      { id: "lg_004", name: "渠道成本月报 · 按渠道汇总成本与曝光", score: 0.78 },
      { id: "lg_002", name: "大区销量透视 · 门店×品类月度", score: 0.41 },
    ],
  });
}
export function runExecute(): Promise<SessionResp> {
  return delay(160, { session_id: "sess_run_mock" });
}

export function suggestCharts(): Promise<ChartsResp> {
  return delay(200, {
    roles: { time: ["1月", "2月", "3月"], category: ["门店", "品类"], numeric: ["1月", "2月", "3月"] },
    suggestions: [
      { type: "line", x: "1月", y: "2月", label: "折线 · 各月趋势" },
      { type: "bar", x: "门店", y: "1月", label: "柱状 · 门店对比" },
      { type: "pie", x: "品类", y: "1月", label: "饼图 · 品类占比" },
    ],
  });
}

export function exportXlsx(): Promise<void> {
  // mock：生成一个占位文本文件，证明导出按钮通路可用
  const blob = new Blob(["SwiftGrid mock export\n（真实模式将下载后端生成的 xlsx）"], {
    type: "text/plain",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "swiftgrid_result_mock.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return Promise.resolve();
}

export function composeImport(): Promise<ComposeImportResp> {
  composeMode = "import";
  return Promise.resolve({ session_id: "compose_mock", status: "UPLOADING", files: [] });
}

export function composePlan(): Promise<ComposePlanResp> {
  return Promise.resolve({ plan: MOCK_COMPOSE_PLAN, questions: MOCK_COMPOSE_PLAN.questions, directions: MOCK_COMPOSE_DIRECTIONS, dataset: MOCK_COMPOSE_DATASET });
}

export function composeMessage(): Promise<ComposePlanResp> {
  return Promise.resolve({ plan: MOCK_COMPOSE_PLAN, questions: MOCK_COMPOSE_PLAN.questions, facts: MOCK_COMPOSE_PLAN.confirmed_facts });
}

export function composeExecute(): Promise<{ session_id: string; status: string }> {
  composeMode = "execute";
  return Promise.resolve({ session_id: "compose_mock", status: "QUEUED" });
}

function composeResultPage(sheetName = "客户销售汇总", offset = 0, limit = 100): ComposeResultPage {
  const rows = MOCK_COMPOSE_RESULT_ROWS[sheetName] ?? [];
  return {
    sheet: { name: sheetName, columns: sheetName === "客户销售汇总" ? ["往来单位编号", "往来单位名称", "客户终端类型", "分类状态", "分类依据", "销售单数", "退货单数", "销售金额", "退货金额", "净销售额", "净销售额占比", "最近交易日期"] : sheetName === "待确认分类" ? ["往来单位编号", "往来单位名称", "建议类型", "判断依据", "置信状态", "用户确认类型"] : ["客户终端类型", "客户数", "客户数占比", "销售金额", "退货金额", "净销售额", "净销售额占比", "退货率"], offset, limit, total_rows: rows.length, rows: rows.slice(offset, offset + limit) },
    quality: MOCK_COMPOSE_QUALITY,
    dataset: MOCK_COMPOSE_DATASET,
    result_summary: { source_sales: "22000.00", source_returns: "400.00", source_net: "21600.00" },
  };
}

export function composeResult(_sid: string, sheet?: string, offset = 0, limit = 100): Promise<ComposeResultPage> {
  return Promise.resolve(composeResultPage(sheet, offset, limit));
}

export function composeClassification(): Promise<ComposeExecuteSuccess & { ok: boolean }> {
  return Promise.resolve({ ok: true, quality: { ...MOCK_COMPOSE_QUALITY, pending_classification_count: 0, can_complete: true }, result_sheets: Object.entries(MOCK_COMPOSE_RESULT_ROWS).map(([name, rows]) => ({ name, columns: composeResultPage(name).sheet.columns, rows: rows.length, total_rows: rows.length, preview: rows })), result_summary: { source_sales: "22000.00", source_returns: "400.00", source_net: "21600.00" } });
}

export function composeComplete(_sid: string, name: string): Promise<{ ok: boolean; logic_id: string; name: string }> {
  return Promise.resolve({ ok: true, logic_id: "compose_mock_logic", name });
}

// ── 模拟 SSE：按时间序列触发事件 ───────────────────────────────────────────
export function streamSSE(
  onEvent: SSEHandler,
  kind: "learn" | "run" | "compose",
): SSEController {
  let cancelled = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  const fire = (ms: number, type: SSEType, data: unknown) =>
    timers.push(
      setTimeout(() => {
        if (!cancelled) onEvent(type, data);
      }, ms),
    );

  if (kind === "compose") {
    if (composeMode === "import") {
      fire(300, "progress", { stage: "scan_sheet", label: "扫描文件结构与 Sheet", processed_rows: 0, total_rows: 0 });
      fire(900, "progress", { stage: "profile", label: "分层抽样与字段画像", processed_rows: 128, total_rows: 128 });
      fire(1500, "success", { dataset: MOCK_COMPOSE_DATASET, directions: MOCK_COMPOSE_DIRECTIONS, plan: null });
    } else {
      fire(300, "progress", { stage: "aggregate", label: "按方案执行全量确定性计算", processed_rows: 0, total_rows: 128 });
      fire(1100, "progress", { stage: "reconcile", label: "执行金额对账", processed_rows: 128, total_rows: 128 });
      fire(1800, "success", { quality: MOCK_COMPOSE_QUALITY, result_sheets: Object.entries(MOCK_COMPOSE_RESULT_ROWS).map(([name, rows]) => ({ name, columns: composeResultPage(name).sheet.columns, rows: rows.length, total_rows: rows.length, preview: rows })), result_summary: { source_sales: "22000.00", source_returns: "400.00", source_net: "21600.00" } });
    }
  } else if (kind === "learn") {
    fire(300, "progress", { stage: "read", label: "读取源表" });
    fire(900, "progress", { stage: "target", label: "理解目标样例" });
    fire(1600, "progress", { stage: "infer", label: "推导处理逻辑 · 本地校验中" });
    if (learnRound === 0) {
      // 首轮 → 需澄清
      fire(2600, "clarify", {
        diagnosis:
          "结果比你的样例多保留了 3 行。翻查源表，这 3 行的「转化量 > 点击量」——这在现实里不可能，大概率是脏数据。你做样例时手动剔掉了它们，但没在表头体现，AI 没看出这条隐藏规则。",
        suggested_clarification: "请过滤掉「转化量大于点击量」的异常行，再做后续汇总。",
        diff: "结果 5 行 vs 目标 2 行；多出 3 行异常数据。",
        round: 1,
        max: 3,
      });
    } else {
      // 澄清后 → 成功
      fire(2600, "success", {
        code: MOCK_CODE,
        suggested_name: "门店品类月度销量透视 · 过滤异常",
        result_handle: "rh_mock",
        result_preview: [resultPreview],
      });
    }
  } else {
    fire(300, "progress", { stage: "apply", label: "套用已学逻辑处理新表" });
    fire(1200, "progress", { stage: "heal", label: "新表结构有变，已自动自愈逻辑" });
    fire(2200, "success", {
      result_handle: "rh_mock_run",
      healed: true,
      result_preview: [resultPreview],
      before_preview: [sourcePreview],
    });
  }

  return {
    close: () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    },
  };
}

const MOCK_CODE = `import pandas as pd

def process_data(dfs: dict) -> pd.DataFrame:
    df = dfs["销售明细"]
    # 过滤异常行：转化量不应大于点击量（此处为示例占位）
    df = df.copy()
    df["月份"] = pd.to_datetime(df["日期"]).dt.month.astype(str) + "月"
    pivot = (
        df.pivot_table(index=["门店", "品类"], columns="月份",
                       values="销量", aggfunc="sum", fill_value=0)
          .reset_index()
    )
    return pivot
`;

function delay<T>(ms: number, value: T): Promise<T> {
  return new Promise((res) => setTimeout(() => res(value), ms));
}
function today(): string {
  return new Date().toISOString().slice(0, 10);
}

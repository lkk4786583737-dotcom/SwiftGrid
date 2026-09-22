// SwiftGrid API 客户端（纯客户端 fetch + SSE）。
// 开发(A)：.env.local 设 NEXT_PUBLIC_API_BASE=http://localhost:8000
// 发布(B)：留空 → 同源相对路径 /api/...
// 无后端：NEXT_PUBLIC_MOCK=true → 走 lib/mock.ts 假数据，跑通全部交互。

import type {
  AnalysisSummaryResp,
  ChartDataResp,
  ChartsResp,
  ComposeExecuteSuccess,
  ComposeImportResp,
  ComposePlanResp,
  ComposeResultPage,
  ComposeSourcePreview,
  ComposeTablePreview,
  ConfigStatusResp,
  LogicMeta,
  MatchResp,
  ModelsResp,
  ParseResp,
  SessionResp,
  SSEType,
} from "./types";
import * as mock from "./mock";

// The protected internal-test proxy exposes frontend and API on one origin.
// Keep this opt-in so normal development and static deployment are unchanged.
const CONFIGURED_API = process.env.NEXT_PUBLIC_INTERNAL_TEST_PROXY === "true"
  ? ""
  : process.env.NEXT_PUBLIC_API_BASE ?? "";

function resolveApiBase(base: string): string {
  if (!base || typeof window === "undefined") return base;
  try {
    const url = new URL(base);
    const localAliases = new Set(["localhost", "127.0.0.1"]);
    if (localAliases.has(url.hostname) && localAliases.has(window.location.hostname)) {
      url.hostname = window.location.hostname;
      return url.origin;
    }
  } catch {
    // Relative API bases are already tied to the current page origin.
  }
  return base;
}

export const API = resolveApiBase(CONFIGURED_API);
export const MOCK = process.env.NEXT_PUBLIC_MOCK === "true";

class ApiError extends Error {
  constructor(public code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function asJson<T>(p: Promise<Response>): Promise<T> {
  const r = await p;
  const text = await r.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!r.ok) {
    // 后端约定：{ error:{code,message} } 或 FastAPI 的 { detail }
    const b = body as {
      error?: { code?: string; message?: string };
      detail?: string | { code?: string; message?: string };
    };
    const detail = typeof b?.detail === "object" ? b.detail : undefined;
    const msg = b?.error?.message ?? detail?.message ?? (typeof b?.detail === "string" ? b.detail : undefined) ?? `请求失败（${r.status}）`;
    throw new ApiError(b?.error?.code ?? detail?.code ?? String(r.status), msg);
  }
  return body as T;
}

// ── 配置 ────────────────────────────────────────────────────────────────────
export function getModels(): Promise<ModelsResp> {
  if (MOCK) return Promise.resolve(mock.models);
  return asJson(fetch(`${API}/api/models`));
}

export function getConfigStatus(): Promise<ConfigStatusResp> {
  if (MOCK) return Promise.resolve({ configured: true, model: "演示模式" });
  return asJson(fetch(`${API}/api/config/status`));
}

export function setConfig(body: {
  api_key: string;
  model?: string;
  base_url?: string;
}): Promise<{ ok: boolean }> {
  if (MOCK) return Promise.resolve({ ok: true });
  return asJson(
    fetch(`${API}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// ── 解析（上传/粘贴二合一）─────────────────────────────────────────────────
export function parseInput(fd: FormData): Promise<ParseResp> {
  if (MOCK) return mock.parseInput(fd);
  return asJson(fetch(`${API}/api/parse`, { method: "POST", body: fd }));
}

// ── 逻辑库 ──────────────────────────────────────────────────────────────────
export function listLogics(): Promise<LogicMeta[]> {
  if (MOCK) return Promise.resolve(mock.logics);
  return asJson(fetch(`${API}/api/logics`));
}

export function deleteLogic(id: string): Promise<{ ok: boolean }> {
  if (MOCK) return mock.deleteLogic(id);
  return asJson(fetch(`${API}/api/logics/${id}`, { method: "DELETE" }));
}

export function renameLogic(id: string, name: string): Promise<{ ok: boolean }> {
  if (MOCK) return mock.renameLogic(id, name);
  return asJson(
    fetch(`${API}/api/logics/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

// ── 学习流 ──────────────────────────────────────────────────────────────────
export function learnStart(body: {
  source_handle: string;
  target_handle: string;
  formula_hints?: string;
}): Promise<SessionResp> {
  if (MOCK) return mock.learnStart();
  return asJson(
    fetch(`${API}/api/learn/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export function learnClarify(
  sid: string,
  clarification: string,
): Promise<{ ok: boolean; round: number }> {
  if (MOCK) return mock.learnClarify(clarification);
  return asJson(
    fetch(`${API}/api/learn/clarify/${sid}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clarification }),
    }),
  );
}

export function learnSave(sid: string, name: string): Promise<{ id: string }> {
  if (MOCK) return mock.learnSave(name);
  return asJson(
    fetch(`${API}/api/learn/save/${sid}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export function learnCancel(sid: string): Promise<{ ok: boolean }> {
  if (MOCK) return Promise.resolve({ ok: true });
  return asJson(fetch(`${API}/api/learn/cancel/${sid}`, { method: "POST" }));
}

// ── 智能制表流 ─────────────────────────────────────────────────────────────
export function composeImport(files: File[]): Promise<ComposeImportResp> {
  if (MOCK) return mock.composeImport();
  const fd = new FormData();
  files.forEach((file) => fd.append("files", file, file.name));
  return asJson(fetch(`${API}/api/compose/import`, { method: "POST", body: fd }));
}

export function composeSourcePreview(sid: string, limit = 12): Promise<ComposeSourcePreview> {
  const params = new URLSearchParams({ limit: String(limit) });
  return asJson(fetch(`${API}/api/compose/preview/${sid}?${params.toString()}`));
}

export function composePlan(sid: string, body: { direction_id?: string; message?: string }): Promise<ComposePlanResp> {
  if (MOCK) return mock.composePlan();
  return asJson(fetch(`${API}/api/compose/plan/${sid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

export function composeMessage(sid: string, message: string): Promise<ComposePlanResp> {
  if (MOCK) return mock.composeMessage();
  return asJson(fetch(`${API}/api/compose/message/${sid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  }));
}

export function composeExecute(sid: string): Promise<{ session_id: string; status: string }> {
  if (MOCK) return mock.composeExecute();
  return asJson(fetch(`${API}/api/compose/execute/${sid}`, { method: "POST" }));
}

export function composeResult(sid: string, sheet?: string, offset = 0, limit = 100): Promise<ComposeResultPage> {
  if (MOCK) return mock.composeResult(sid, sheet, offset, limit);
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (sheet) params.set("sheet", sheet);
  return asJson(fetch(`${API}/api/compose/result/${sid}?${params.toString()}`));
}

export function composePreview(sid: string, directionId?: string, limit = 100): Promise<ComposeTablePreview> {
  if (MOCK) return Promise.resolve({ scope: "representative_sample", source_rows: 0, max_rows: limit, sheets: [] });
  const params = new URLSearchParams({ limit: String(limit) });
  if (directionId) params.set("direction_id", directionId);
  return asJson(fetch(`${API}/api/compose/preview/${sid}?${params.toString()}`));
}

export function composeClassification(sid: string, mapping: Record<string, string>): Promise<ComposeExecuteSuccess & { ok: boolean }> {
  if (MOCK) return mock.composeClassification();
  return asJson(fetch(`${API}/api/compose/classification/${sid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mapping }),
  }));
}

export function composeComplete(sid: string, name: string): Promise<{ ok: boolean; logic_id: string; name: string }> {
  if (MOCK) return Promise.resolve({ ok: true, logic_id: "compose_mock_logic", name });
  return asJson(fetch(`${API}/api/compose/complete/${sid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }));
}

export async function composeExport(sid: string): Promise<void> {
  if (MOCK) return mock.exportXlsx();
  const r = await fetch(`${API}/api/compose/export/${sid}`);
  if (!r.ok) throw new ApiError(String(r.status), "导出失败");
  const disposition = r.headers.get("content-disposition") ?? "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  let filename = "智能制表结果.xlsx";
  try {
    filename = encodedName ? decodeURIComponent(encodedName) : plainName || filename;
  } catch {
    filename = plainName || filename;
  }
  downloadBlob(await r.blob(), filename);
}

export function composeCancel(sid: string): Promise<{ ok: boolean }> {
  if (MOCK) return Promise.resolve({ ok: true });
  return asJson(fetch(`${API}/api/compose/cancel/${sid}`, { method: "POST" }));
}

// ── 执行流 ──────────────────────────────────────────────────────────────────
export function runMatch(source_handle: string): Promise<MatchResp> {
  if (MOCK) return mock.runMatch();
  return asJson(
    fetch(`${API}/api/run/match`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_handle }),
    }),
  );
}

export function runExecute(body: {
  logic_id: string;
  source_handle: string;
}): Promise<SessionResp> {
  if (MOCK) return mock.runExecute();
  return asJson(
    fetch(`${API}/api/run/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export function runCancel(sid: string): Promise<{ ok: boolean }> {
  if (MOCK) return Promise.resolve({ ok: true });
  return asJson(fetch(`${API}/api/run/cancel/${sid}`, { method: "POST" }));
}

// ── 图表建议 ────────────────────────────────────────────────────────────────
export function suggestCharts(
  result_handle: string,
  sheet?: string,
): Promise<ChartsResp> {
  if (MOCK) return mock.suggestCharts();
  return asJson(
    fetch(`${API}/api/charts/suggest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result_handle, sheet }),
    }),
  );
}

export function chartData(body: {
  result_handle: string;
  sheet?: string;
  chart_type: string;
  x: string;
  y?: string | null;
  agg?: string;
}): Promise<ChartDataResp> {
  if (MOCK) {
    return Promise.resolve({
      points: [
        { label: "A", value: 120 },
        { label: "B", value: 90 },
        { label: "C", value: 60 },
      ],
      total_rows: 3,
      source_rows: 3,
      aggregation: body.agg ?? "sum",
      limited: false,
      limit: 80,
    });
  }
  return asJson(
    fetch(`${API}/api/charts/data`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export function analysisSummary(body: {
  result_handle: string;
  sheet?: string;
}): Promise<AnalysisSummaryResp> {
  if (MOCK) {
    return Promise.resolve({
      profile: {
        total_rows: 128,
        total_columns: 4,
        columns: [
          { name: "月份", role: "time", null_rate: 0, unique_count: 6, examples: ["2026-01"] },
          { name: "地区", role: "category", null_rate: 0, unique_count: 4, examples: ["华东"] },
          { name: "销售额", role: "numeric", null_rate: 0, unique_count: 90, examples: [12000] },
        ],
        time_columns: ["月份"],
        category_columns: ["地区"],
        numeric_columns: ["销售额"],
      },
      recommended_questions: ["销售额随月份是否有明显趋势？", "不同地区的销售额差异如何？"],
      recommended_charts: [
        {
          title: "销售额趋势",
          chart_type: "line",
          x: "月份",
          y: "销售额",
          agg: "sum",
          reason: "检测到时间字段和销售额字段，适合观察趋势。",
          source: "builtin",
        },
      ],
      insights: ["当前表包含 128 行、4 列，具备基础聚合分析条件。"],
      provider: "builtin",
    });
  }
  return asJson(
    fetch(`${API}/api/analysis/summary`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// ── 导出 ────────────────────────────────────────────────────────────────────
export async function exportXlsx(result_handle: string): Promise<void> {
  if (MOCK) return mock.exportXlsx();
  const r = await fetch(`${API}/api/export/xlsx`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result_handle }),
  });
  if (!r.ok) throw new ApiError(String(r.status), "导出失败");
  const blob = await r.blob();
  downloadBlob(blob, "swiftgrid_result.xlsx");
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── SSE 控制器 ──────────────────────────────────────────────────────────────
export type SSEHandler = (type: SSEType, data: unknown) => void;
export type SSEController = { close: () => void };

/**
 * 订阅 SSE 流（学习/执行进度）。
 * 真实模式用浏览器 EventSource（仅 GET）；mock 模式用定时器模拟整条流。
 */
export function streamSSE(
  url: string,
  onEvent: SSEHandler,
  mockKind?: "learn" | "run" | "compose",
): SSEController {
  if (MOCK) return mock.streamSSE(onEvent, mockKind ?? "learn");

  const es = new EventSource(`${API}${url}`);
  const types: SSEType[] = ["progress", "success", "clarify", "error", "cancelled"];
  const terminal: SSEType[] = ["success", "clarify", "error", "cancelled"];
  types.forEach((t) =>
    es.addEventListener(t, (e) => {
      let data: unknown = {};
      try {
        data = JSON.parse((e as MessageEvent).data);
      } catch {
        /* ignore */
      }
      onEvent(t, data);
      if (terminal.includes(t)) es.close();
    }),
  );
  es.onerror = () => {
    // 连接异常（区别于业务 error 事件）
    onEvent("error", { message: "连接中断，请重试" });
    es.close();
  };
  return { close: () => es.close() };
}

export { ApiError };

export function isConfigRequired(error: unknown): boolean {
  return error instanceof ApiError && error.code === "401";
}

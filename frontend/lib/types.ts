// 与后端真实契约对齐（swiftgrid/backend/api/*.py + services/tableio.summarize）。
// 注意：后端 summarize() 的 preview 是「二维数组」(行数组)，不是对象数组。

export type Sheet = {
  name: string;
  columns: string[];
  rows: number;
  total_rows: number;
  preview: (string | number | boolean | null)[][];
};

export type ParseResp = {
  handle: string;
  sheets: Sheet[];
  oversize: boolean;
};

export type ModelOption = { id: string; label: string; base_url: string };
export type ModelsResp = { recommended: string; options: ModelOption[] };
export type ConfigStatusResp = { configured: boolean; model: string };

export type LogicMeta = {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string;
  learn_profile?: unknown[];
  origin?: "learn" | "smart_table" | string;
  schema_version?: number;
};

export type MatchCandidate = { id: string; name: string; score: number };
export type MatchResp = {
  auto: boolean;
  best: MatchCandidate | null;
  candidates: MatchCandidate[];
};

export type ChartSuggestion = {
  type: "line" | "bar" | "pie" | "scatter" | "hist";
  x: string;
  y: string | null;
  label: string;
};
export type ChartsResp = {
  roles: { time: string[]; category: string[]; numeric: string[] };
  suggestions: ChartSuggestion[];
};

export type ChartPoint = {
  label: string;
  value: number;
  x?: number;
};

export type ChartDataResp = {
  points: ChartPoint[];
  total_rows: number;
  source_rows: number;
  aggregation: string;
  limited: boolean;
  limit: number;
};

export type AnalysisChart = {
  title: string;
  chart_type: "line" | "bar" | "pie" | "scatter" | "hist";
  x: string;
  y: string | null;
  agg: string;
  reason: string;
  source: string;
};

export type AnalysisColumn = {
  name: string;
  role: "time" | "numeric" | "category" | "text";
  null_rate: number;
  unique_count: number;
  examples: (string | number | boolean | null)[];
};

export type AnalysisSummaryResp = {
  profile: {
    total_rows: number;
    total_columns: number;
    columns: AnalysisColumn[];
    time_columns: string[];
    category_columns: string[];
    numeric_columns: string[];
  };
  recommended_questions: string[];
  recommended_charts: AnalysisChart[];
  insights: string[];
  provider: string;
};

// ── SSE 事件载荷 ───────────────────────────────────────────────────────────
export type SSEType = "progress" | "success" | "clarify" | "error" | "cancelled";

export type ProgressEvent = { stage: string; label: string };

export type LearnSuccessEvent = {
  code: string;
  suggested_name: string;
  result_handle: string;
  result_preview: Sheet[];
};

export type ClarifyEvent = {
  diagnosis: string;
  suggested_clarification: string;
  diff: string;
  round: number;
  max: number;
};

export type RunSuccessEvent = {
  result_handle: string;
  healed: boolean;
  result_preview: Sheet[];
  before_preview: Sheet[];
};

export type RunDecisionEvent = {
  phase: string;
  title: string;
  message: string;
  active_issue: {
    evidence?: Record<string, unknown>;
    options: { id: string; label: string }[];
  };
};

export type ErrorEvent = { message: string };

export type SessionResp = { session_id: string };

// ── 智能制表（compose）契约 ───────────────────────────────────────────────
export type ComposeField = {
  name: string;
  role: string;
  confidence: number;
  data_type?: "number" | "date" | "text" | "mixed" | string;
  null_rate: number;
  nonempty_count?: number;
  numeric_count?: number;
  positive_count?: number;
  negative_count?: number;
  zero_count?: number;
  unique_count: number;
  unique_count_exact?: boolean;
  top_values?: { value: string; count: number }[];
  examples: (string | number | boolean | null)[];
  sample_rows: number;
  profile_scope?: "full" | "sample" | string;
  warnings?: string[];
};

export type ComposeSourcePreviewSheet = {
  name: string;
  header_row: number;
  columns: string[];
  rows: unknown[][];
  preview_rows: number;
};

export type ComposeSourcePreview = {
  scope: 'source_preview';
  files: {
    filename: string;
    size_bytes: number;
    sheets: ComposeSourcePreviewSheet[];
  }[];
  sheet_count: number;
  max_rows_per_sheet: number;
  truncated_sheets: boolean;
};

export type ComposePreviewSheet = {
  name: string;
  columns: string[];
  rows: unknown[][];
  sample_result_rows: number;
};

export type ComposeTablePreview = {
  scope?: "representative_sample" | string;
  source_rows?: number;
  max_rows?: number;
  estimated_total_rows?: number | null;
  sheets?: ComposePreviewSheet[];
  warnings?: string[];
  error?: string;
  code?: string;
};

export type ComposeSheetSummary = {
  name: string;
  columns: string[];
  rows: number;
  total_rows: number;
  preview: unknown[][];
};

export type ComposeDirection = {
  id: string;
  title: string;
  description: string;
  evidence: string[];
  evidence_notes?: string[];
  outputs: string[];
  recommended: boolean;
  assessment?: {
    data_evidence: 'strong' | 'limited' | string;
    executability: 'executable' | 'needs_clarification' | 'unsupported' | string;
    business_fit: 'pending_confirmation' | 'confirmed' | string;
  };
  confidence?: number;
  grain_label?: string;
  estimated_rows?: number | null;
  warnings?: string[];
  preview?: ComposeTablePreview | null;
};

export type ComposeQuestion = {
  code: string;
  text: string;
  blocking: boolean;
  evidence?: string;
  options?: {
    id: string;
    label: string;
    message: string;
    recommended?: boolean;
  }[];
};

export type ComposeExpression = {
  op: string;
  field?: string;
  value?: unknown;
  args?: ComposeExpression[];
  separator?: string;
  condition?: { field: string; operator: string; value?: unknown };
  then?: ComposeExpression;
  else?: ComposeExpression;
};

export type ComposeTablePlan = {
  mode: 'detail' | 'aggregate';
  output_name: string;
  filters: { field: string; operator: string; value?: unknown }[];
  columns?: { name: string; expression: ComposeExpression }[];
  group_by?: { field: string; name: string }[];
  aggregations?: { name: string; aggregation: string; expression?: ComposeExpression }[];
  sort: { field: string; direction: 'asc' | 'desc' }[];
  limit?: number | null;
};

export type ComposePlan = {
  schema_version: number;
  dataset_id: string;
  direction_id: string;
  direction_title: string;
  output_kind: "customer_sales" | "dimension_summary" | "classification" | "generic_summary" | string;
  output_names?: Record<string, string>;
  role_bindings: Record<string, string>;
  business_rules: Record<string, string | string[]>;
  filters: unknown[];
  time_grain: "year" | "quarter" | "month" | null;
  dimensions: string[];
  measures?: { field: string; aggregation: "sum" | "average" | "min" | "max" | string; name: string }[];
  outputs: { name: string; key: string; group_by: string[]; metrics: string[] }[];
  table?: ComposeTablePlan;
  requirement_summary?: string;
  intent_confirmation?: { required: boolean; confirmed: boolean };
  unresolved_requirements?: string[];
  decision_log?: { turn: number; type?: string; text: string }[];
  classification: {
    enabled: boolean;
    rule_pack: string;
    rule_pack_version: string;
    rules: {
      label: string;
      keywords?: string[];
      conditions?: { field: string; operator: "contains" | "prefix" | "equals" | "regex"; values: string[] }[];
      match?: "any" | "all";
    }[];
    unresolved_policy: string;
  };
  confirmed_facts: { turn: number; text: string }[];
  questions: ComposeQuestion[];
  ready_for_execution: boolean;
  assistant_message?: string;
  model_notice?: string;
};

export type ComposeDataset = {
  session_id: string;
  file_count: number;
  sheet_count: number;
  planned_rows: number;
  total_rows: number;
  valid_rows: number | null;
  skipped_rows: number;
  source_fingerprint: string;
  schema: { columns: string[]; signature: string };
  sheets: { file: string; sheet: string; columns: string[]; signature: string; rows: number }[];
  sample: { scope: string; rows: number; max_per_sheet: number };
  fields?: ComposeField[];
  quality_findings?: {
    code: string;
    severity: 'blocking' | 'plan_related' | 'warning' | string;
    field?: string;
    message: string;
  }[];
  modeling_evidence?: {
    candidate_keys?: { fields: string[]; coverage: number; uniqueness: number }[];
    numeric_fields?: string[];
    date_fields?: string[];
    categorical_fields?: { field: string; unique_count: number }[];
  };
  planning?: { source: "model" | "fallback" | string; model_configured: boolean; notice?: string };
  readiness?: {
    level: 1 | 2 | 3 | 4;
    label: string;
    reasons: string[];
    warnings: string[];
  };
  evidence?: {
    transaction?: {
      transaction_field?: string;
      amount_field?: string;
      nonzero_rows?: number;
      zero_rows?: number;
      agreement_rate?: number;
      sales_values?: string[];
      return_values?: string[];
      supports_amount_sign?: boolean;
      supports_code_mapping?: boolean;
      mappings?: {
        value: string;
        positive_rows: number;
        negative_rows: number;
        zero_rows: number;
        suggested: string;
        confidence: number;
      }[];
    };
    fields?: Record<string, { same_as_entity_name_rate?: number; same_as_entity_id_rate?: number }>;
  };
};

export type ComposeQuality = {
  scope: "full" | string;
  planned_rows: number;
  processed_rows: number;
  valid_rows: number;
  invalid_rows: number;
  skipped_rows: number;
  zero_amount_rows?: number;
  filtered_rows?: number;
  transaction_sign_conflict_rows?: number;
  entity_name_conflict_count?: number;
  measure_invalid_cells?: number;
  excluded_null_measure_cells?: number;
  classification_enabled?: boolean;
  source_fingerprint: string;
  reconciliations: { name: string; status: "passed" | "failed" | string; delta: string }[];
  pending_classification_count: number;
  can_complete: boolean;
};

export type ComposeImportResp = {
  session_id: string;
  status: string;
  files: { filename: string; stored_name: string; size_bytes: number; sha256: string }[];
};

export type ComposeImportSuccess = {
  dataset: ComposeDataset;
  directions: ComposeDirection[];
  plan: ComposePlan | null;
};

export type ComposePlanResp = {
  dataset?: ComposeDataset;
  directions?: ComposeDirection[];
  plan: ComposePlan;
  questions: ComposeQuestion[];
  facts?: { turn: number; text: string }[];
  preview?: ComposeTablePreview;
};

export type ComposeExecuteSuccess = {
  quality: ComposeQuality;
  result_sheets: ComposeSheetSummary[];
  result_summary: { source_sales?: string; source_returns?: string; source_net?: string; source_metrics?: Record<string, string> };
};

export type ComposeResultPage = {
  sheet: {
    name: string;
    columns: string[];
    offset: number;
    limit: number;
    total_rows: number;
    rows: unknown[][];
  };
  quality: ComposeQuality;
  dataset: ComposeDataset;
  result_summary: { source_sales?: string; source_returns?: string; source_net?: string; source_metrics?: Record<string, string> };
};

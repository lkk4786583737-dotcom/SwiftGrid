"""Controlled smart-table modeling agent prototype.

The model can explore a local dataset through bounded, privacy-aware tools. It
never receives the workbook or unrestricted row access, and it produces a
high-level workbook blueprint rather than executable code.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import modeling_arch_demo as legacy_demo
from swiftgrid.backend.services import compose, compose_ai


OUTPUT_ROOT = ROOT / "artifacts" / "modeling-agent-demo"
MEMORY_ROOT = OUTPUT_ROOT / "memory"
CONFIRMED_MEMORY_PATH = MEMORY_ROOT / "confirmed_memories.json"
MAX_MODEL_TURNS = 18
MAX_TOOL_CALLS = 32
MAX_CALLS_PER_TURN = 4
MAX_RESULT_ROWS = 20
MIN_CELL_SIZE = 3
RECENT_EVIDENCE_WINDOW = 6
SENSITIVE_TOKENS = (
    "name", "phone", "mobile", "email", "address", "account", "bank", "idcard",
    "姓名", "客户名", "单位名称", "电话", "手机", "邮箱", "地址", "账号", "银行卡", "身份证", "证件",
)


AGENT_PROMPT = r"""
You are a smart-table modeling agent. Design valuable, reusable target workbook
blueprints from source data. Use Chinese for user-facing text and JSON only.

You do not receive raw files. You may autonomously explore the dataset through
safe local tools. Use tool evidence to test hypotheses before recommending a
workbook. Be creative about business value, but never invent data facts,
business definitions, joins, thresholds, causes, targets, rates or formulas.
Retrieved long-term memory is a reusable hint, not current-data evidence. Verify
its field bindings and assumptions with current tools before using it.

On each turn output exactly:
{
  "analysis_summary": "short explanation",
  "memory_update": {
    "hypotheses": ["unconfirmed hypotheses"],
    "open_questions": ["questions that materially change the result"],
    "design_notes": ["current design considerations"]
  },
  "tool_calls": [{"tool": "tool_name", "arguments": {}}],
  "final": null
}

You may request up to four independent tools in one turn. Available tools and
their exact argument schemas are in tool_catalog. Do not repeat an identical
tool call. Inspect only what is useful.

When evidence is sufficient, set tool_calls=[] and final to:
{
  "dataset_understanding": {
    "structure": "what one row or each sheet represents",
    "semantic_roles": [{"role":"generic role", "fields":["real fields"], "source_sheets":["real source sheets when the sheet itself has meaning"], "basis":["E001"]}],
    "uncertainties": ["unconfirmed semantics"]
  },
  "workbook_packages": [
    {
      "title": "workbook title",
      "purpose": "coherent work outcome",
      "why_valuable": "evidence-backed value without advice or causal claims",
      "evidence_ids": ["E001"],
      "clarifications": ["only questions that change calculations"],
      "sheets": [
        {
          "title": "sheet title",
          "purpose": "specific job",
          "grain": "what one row represents",
          "source_scope": ["real source sheet names"],
          "dimensions": [{
            "name": "output dimension name",
            "kind": "field|source_sheet|time_bucket|numeric_bucket",
            "source_fields": ["real fields; empty only for source_sheet"],
            "parameters": {"grain": "day|month|quarter|year for time_bucket", "boundaries": ["numbers for confirmed numeric_bucket"]},
            "requires_confirmation": false
          }],
          "metrics": [{
            "name": "output metric",
            "aggregation": "sum|average|min|max|count_rows|count_nonempty|count_distinct|value_after_uniqueness_check|derived_after_confirmation",
            "source_fields": ["real fields"],
            "calculation_intent": "plain-language deterministic intent"
          }],
          "join_plan": null,
          "selection_logic": "none or evidence-backed record selection",
          "why_included": "how this complements other sheets",
          "evidence_ids": ["E001"],
          "assumptions": []
        }
      ]
    }
  ],
  "blocking_questions": [],
  "execution_status": "ready|needs_clarification",
  "recommended_package_title": "one package title or null when no reliable package exists"
}

For a vague user goal, return zero to three materially valuable packages; for a
precise goal, return zero or one. Never fill a quota. Each returned package has
2-5 complementary sheets and may combine overview, entity, time,
organizational, status or verification sheets when supported. When no package
is reliable, return workbook_packages=[], recommended_package_title=null and at
least one blocking question that could unlock useful modeling.
Data quality findings may trigger a clarification before modeling, but a data
quality inspection, cleaning or completeness workbook is not a target modeling
package unless the user explicitly asks for one. Do not use it to fill a weak
third recommendation.

All fields and source sheets must be real. Every semantic role, package and
sheet must cite tool evidence. Row count must never be called order count,
person count or another business-object count; use count_distinct on a verified
identifier. Model-generated numbers never become final table values. Final
values will be deterministically computed over all rows after user confirmation.
Use count_rows only for source records, count_nonempty for populated cells and
count_distinct for business entities. Use value_after_uniqueness_check for a
source value copied to an entity-level detail row; never invent aggregation
names such as none.
Use check_key_uniqueness before claiming that any source is unique on one or
more fields. Suppressed or empty group_aggregate output is not uniqueness
evidence. If the tool reports duplicate keys, describe the uncertainty or ask a
material de-duplication question; never claim uniqueness.
Derived metric names must match their actual denominator. In particular,
sales divided by foot traffic is per-visitor sales output, not average order
value (客单价); 客单价 requires an order, transaction or customer-count denominator.
Make every analytical title executable: trend sheets need a time_bucket,
segmentation sheets need explicit numeric buckets, concentration sheets need a
share/concentration metric, ranking sheets need sort/selection logic, and
anomaly/risk lists need an evidence-backed rule or a blocking clarification.

Use a field dimension for an unchanged source field, source_sheet when the
sheet name itself carries a needed partition such as region, time_bucket for a
date-derived period, and numeric_bucket for threshold-based segmentation. A
numeric bucket without confirmed boundaries must set requires_confirmation=true.
Any package clarification, derived_after_confirmation metric, or dimension/join
plan requiring confirmation must appear in final blocking_questions. It is not
safe to generate the workbook until those questions are answered.
Do not ask whether two sources agree as a blocking question unless the package
actually defines a reconciliation calculation and the answer changes the
source-of-truth or formula. If only one source owns a measure, bind the metric
to that source and keep cross-source consistency as a non-blocking uncertainty.
Grouping duplicate-key rows before summing does not change a total. Ask about
duplicates only when the alternatives are keeping every record versus a
specific deterministic de-duplication rule and that choice changes an output.
Do not put optional additions such as "whether to add a new calculation,
analysis or classification" into blocking_questions. Either model that output
as derived_after_confirmation because it is essential to the selected package,
or omit it and keep the current deterministic package ready.
Do not write claims such as "safe to join/build" in package prose. The system
renders safety status from validator results.

Every sheet using more than one source sheet must include join_plan. Use
append_aligned_sheets only for row partitions after alias/schema confirmation.
Use aggregate_then_join when combining fact tables: pre-aggregate every source
to the declared join keys before joining. Use lookup_join only when tool evidence
shows the lookup key is unique. Never directly join two many-side fact tables.
An output grouping dimension is not automatically a join key. When a dimension
exists only on a unique lookup/entity sheet, join all sources on a verified
shared entity key, keep that lookup dimension in the lookup source's
pre-aggregation grain, and aggregate to the output dimension after the join.
Every source in source_scope needs its own pre_aggregation step. If a valuable
package already has two safe complementary sheets, omit a repeatedly invalid
optional sheet instead of preserving an unsafe join merely to add breadth.
The exact join_plan schema is:
{
  "strategy": "append_aligned_sheets|aggregate_then_join|lookup_join",
  "join_keys": ["real shared fields; empty only for append"],
  "join_type": "full|left|inner; full is the no-loss default",
  "base_source": "real sheet for left join, otherwise null",
  "pre_aggregation": [{
    "source_sheet":"real sheet",
    "grain":["real fields including all join keys"],
    "metrics":[{
      "name":"intermediate output name",
      "aggregation":"sum|average|min|max|count_rows|count_nonempty|count_distinct|value_after_uniqueness_check",
      "source_fields":["real fields"]
    }]
  }],
  "field_aliases": [{"canonical":"output field", "bindings":[{"source_sheet":"real sheet", "field":"real field"}]}],
  "requires_confirmation": false
}
Final sheet metrics may reference intermediate output names created by
pre_aggregation. This is how to express two-stage calculations such as counting
distinct attendance dates per employee before summing employee attendance by
department. Never put an invented intermediate name into a source-sheet field
binding. The validator will run missing join-key checks automatically; do not
repeat tool calls merely to attach evidence IDs.
For aggregate_then_join, every final metric except count_rows must consume named
pre_aggregation outputs, not raw fields. This preserves source provenance after
joining. Raw fields may remain in final dimensions only when retained in a
pre-aggregation grain.
When final dimensions contain every join key, one joined row represents the
final grain and intermediate metrics may use value_after_uniqueness_check. When
the final dimensions omit a join key, the sheet performs a second grouping
stage: use an explicit composable aggregation such as sum, average, min or max;
never copy an employee/entity-level intermediate with
value_after_uniqueness_check into a broader department/category row.
Do not place a dimension or metric field in a sheet unless the source that owns
it is included in source_scope. When all sources are already aggregated to the
final output grain, final metrics should copy the named intermediate outputs
with value_after_uniqueness_check rather than aggregate the raw fields again.
Call check_join_keys for every cross-sheet join pair. For append, cite
check_sheet_overlap and use a source_sheet dimension when its partition meaning
must remain visible. Copy package clarifications exactly into blocking_questions;
set execution_status=needs_clarification when that list is non-empty, otherwise
set it to ready. Do not list optional preferences as blocking questions.
Evidence cited by a sheet must directly cover that sheet's dimensions and
metric source fields. When multiple sheets share one schema, use
check_sheet_overlap first and avoid repeating the same query per sheet unless
the evidence indicates separate treatment is useful. Do not ask users to
confirm a condition already established by current tool evidence.
When context phase is finalize_or_repair, stop broad exploration. Produce or
repair final using existing evidence and last_rejected_final. Preserve every
unaffected valid sheet and patch the reported errors instead of redesigning the
whole workbook. Call a tool only when validation feedback identifies a specific
missing safety fact.
""".strip()


TOOL_CATALOG = {
    "profile_columns": {
        "description": "Profile up to eight fields on all local rows.",
        "arguments": {"fields": ["field"], "source_sheet": "optional"},
    },
    "sample_masked_rows": {
        "description": "View up to five deterministic representative rows; sensitive/high-cardinality values are masked.",
        "arguments": {"fields": ["field"], "source_sheet": "optional", "limit": 5},
    },
    "group_aggregate": {
        "description": "Run a full-data grouped aggregate with bounded output.",
        "arguments": {"group_by": ["field"], "metrics": [{"field": "field or null", "aggregation": "sum|average|min|max|count_rows|count_nonempty|count_distinct", "name": "name"}], "filters": [{"field": "field", "operator": "equals|not_equals|greater_than|greater_or_equal|less_than|less_or_equal|is_empty|not_empty", "value": "optional"}], "source_sheet": "optional", "sort_by": "metric name", "limit": 20},
    },
    "time_aggregate": {
        "description": "Aggregate a numeric measure by day, month or quarter on all rows.",
        "arguments": {"date_field": "field", "measure_field": "field", "aggregation": "sum|average|count", "grain": "day|month|quarter", "source_sheet": "optional"},
    },
    "check_relationship": {
        "description": "Check one-to-one/one-to-many evidence without returning values.",
        "arguments": {"left_field": "field", "right_field": "field", "source_sheet": "optional"},
    },
    "check_join_keys": {
        "description": "Check cross-sheet composite-key uniqueness and overlap without returning key values.",
        "arguments": {"left_sheet": "real sheet", "right_sheet": "real sheet", "key_fields": ["shared field"]},
    },
    "check_key_uniqueness": {
        "description": "Check whether one sheet is unique on a composite key without returning key values.",
        "arguments": {"source_sheet": "real sheet", "key_fields": ["real field"]},
    },
    "top_concentration": {
        "description": "Calculate anonymous entity concentration; entity values are never returned.",
        "arguments": {"entity_field": "field", "measure_field": "field", "top_n": 10, "source_sheet": "optional"},
    },
    "check_sheet_overlap": {
        "description": "Check whether same-schema sheets overlap on one or more identifiers; no values are returned.",
        "arguments": {"identifier_fields": ["field"]},
    },
}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return _jsonable(value.item())
    return str(value)


def _emit_progress(stage: str, message: str, started: float) -> None:
    print(json.dumps({
        "type": "progress",
        "stage": stage,
        "message": message,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }, ensure_ascii=False), flush=True)


def _schema_signature(dataset: dict[str, Any]) -> str:
    payload = [{"sheet": item.get("sheet"), "columns": item.get("columns", [])} for item in dataset.get("sheets", [])]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _is_sensitive(name: str, profile: dict[str, Any] | None = None) -> bool:
    normalized = re.sub(r"\s+", "", name).lower()
    if any(token.lower() in normalized for token in SENSITIVE_TOKENS):
        return True
    profile = profile or {}
    if profile.get("role") in {"entity_id", "entity_name", "document_id"}:
        return True
    nonempty = max(int(profile.get("nonempty_count", 0)), 1)
    unique_rate = int(profile.get("unique_count", 0)) / nonempty
    return profile.get("data_type") == "text" and (int(profile.get("unique_count", 0)) > 40 or unique_rate >= .8)


class SafeDataTools:
    def __init__(self, sid: str, dataset: dict[str, Any]) -> None:
        self.sid = sid
        self.dataset = dataset
        self.columns = list(dataset.get("schema", {}).get("columns", []))
        self.column_set = set(self.columns)
        self.profiles = {item["name"]: item for item in dataset.get("fields", [])}
        self.sheet_columns = {item["sheet"]: set(item.get("columns", [])) for item in dataset.get("sheets", [])}
        self._frame: pd.DataFrame | None = None
        self._numeric: dict[str, pd.Series] = {}
        self.load_seconds = 0.0
        self.compute_seconds = 0.0
        self._salt = str(dataset.get("source_fingerprint") or sid)

    def dataset_card(self) -> dict[str, Any]:
        fields = []
        sheet_count = max(len(self.sheet_columns), 1)
        for item in self.dataset.get("fields", []):
            name = item["name"]
            supporting_sheets = [sheet for sheet, columns in self.sheet_columns.items() if name in columns]
            values = []
            if item.get("data_type") in {"text", "boolean"} and not _is_sensitive(name, item) and int(item.get("unique_count", 0)) <= 30:
                values = [entry.get("value") for entry in item.get("top_values", [])[:8]]
            fields.append({
                "name": name, "type": item.get("data_type"), "suggested_role": item.get("role"),
                "role_confidence": item.get("confidence"),
                "null_rate": item.get("null_rate") if len(supporting_sheets) == sheet_count else None,
                "null_rate_scope": "all_sheets" if len(supporting_sheets) == sheet_count else "profile_per_sheet_required",
                "supporting_sheets": supporting_sheets,
                "unique_count": item.get("unique_count"), "representative_values": values,
                "values_withheld": _is_sensitive(name, item),
            })
        quality_findings = []
        for finding in self.dataset.get("quality_findings", []):
            field = finding.get("field")
            if finding.get("code") == "HIGH_NULL_RATE" and field and sum(field in columns for columns in self.sheet_columns.values()) < sheet_count:
                continue
            quality_findings.append(finding)
        return {
            "rows": self.dataset.get("total_rows"), "sheet_count": self.dataset.get("sheet_count"),
            "sheets": [{"name": item.get("sheet"), "rows": item.get("rows"), "columns": item.get("columns", []), "schema_signature": item.get("signature")} for item in self.dataset.get("sheets", [])],
            "fields": fields, "quality_findings": quality_findings,
            "transaction_evidence": self.dataset.get("evidence", {}).get("transaction", {}),
            "privacy": {"raw_file_sent": False, "raw_rows_sent": False, "representative_rows_are_transformed": True, "sensitive_values_sent": False, "minimum_group_size": MIN_CELL_SIZE},
        }

    def _load(self) -> pd.DataFrame:
        if self._frame is not None:
            return self._frame
        started = time.perf_counter()
        records = []
        conn = compose._connect(self.sid)
        try:
            for sheet, raw in conn.execute("SELECT sheet_name, values_json FROM source_rows ORDER BY row_id"):
                values = json.loads(raw)
                records.append([*(values[index] if index < len(values) else None for index in range(len(self.columns))), sheet])
        finally:
            conn.close()
        self._frame = pd.DataFrame.from_records(records, columns=[*self.columns, "__source_sheet__"])
        self.load_seconds = time.perf_counter() - started
        return self._frame

    def _series_number(self, field: str, frame: pd.DataFrame) -> pd.Series:
        if field not in self._numeric or len(self._numeric[field]) != len(self._load()):
            self._numeric[field] = pd.to_numeric(self._load()[field].astype(str).str.replace(",", "", regex=False), errors="coerce")
        return self._numeric[field].loc[frame.index]

    def _validate_fields(self, fields: list[str], limit: int = 12) -> list[str]:
        if not isinstance(fields, list) or not fields or len(fields) > limit:
            raise ValueError(f"fields must contain 1-{limit} items")
        unknown = [field for field in fields if field not in self.column_set]
        if unknown:
            raise ValueError("unknown fields: " + ", ".join(unknown))
        return fields

    def _scope(self, fields: list[str], sheet: str | None) -> pd.DataFrame:
        frame = self._load()
        if sheet:
            if sheet not in self.sheet_columns:
                raise ValueError("unknown source_sheet")
            if not set(fields) <= self.sheet_columns[sheet]:
                raise ValueError("requested fields do not coexist on source_sheet")
            return frame[frame["__source_sheet__"] == sheet]
        supporting = [name for name, columns in self.sheet_columns.items() if set(fields) <= columns]
        if not supporting:
            raise ValueError("requested fields do not coexist on any source sheet")
        return frame[frame["__source_sheet__"].isin(supporting)]

    def _mask(self, field: str, value: Any, *, representative_row: bool = False) -> Any:
        value = _jsonable(value)
        if value is None:
            return value
        profile = self.profiles.get(field, {})
        if representative_row and profile.get("data_type") == "number":
            number = compose._to_decimal(value)
            return "<NUMBER_POSITIVE>" if number and number > 0 else "<NUMBER_NEGATIVE>" if number and number < 0 else "<NUMBER_ZERO>"
        if representative_row and profile.get("data_type") == "date":
            return "<DATE>"
        if not _is_sensitive(field, profile):
            return value
        digest = hashlib.sha256(f"{self._salt}|{field}|{value}".encode()).hexdigest()[:8].upper()
        return f"<{field}_{digest}>"

    def _filters(self, frame: pd.DataFrame, filters: list[dict[str, Any]]) -> pd.DataFrame:
        if len(filters) > 4:
            raise ValueError("at most four filters are allowed")
        result = frame
        for condition in filters:
            field = condition.get("field")
            operator = condition.get("operator")
            if field not in self.column_set:
                raise ValueError(f"unknown filter field: {field}")
            series = result[field]
            value = condition.get("value")
            if operator in {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}:
                series = pd.to_numeric(series, errors="coerce")
                value = float(value)
            if operator == "equals": mask = series.astype(str) == str(value)
            elif operator == "not_equals": mask = series.astype(str) != str(value)
            elif operator == "greater_than": mask = series > value
            elif operator == "greater_or_equal": mask = series >= value
            elif operator == "less_than": mask = series < value
            elif operator == "less_or_equal": mask = series <= value
            elif operator == "is_empty": mask = series.isna() | (series.astype(str).str.strip() == "")
            elif operator == "not_empty": mask = series.notna() & (series.astype(str).str.strip() != "")
            else: raise ValueError(f"unsupported operator: {operator}")
            result = result[mask.fillna(False)]
        return result

    def profile_columns(self, fields: list[str], source_sheet: str | None = None) -> dict[str, Any]:
        fields = self._validate_fields(fields, 8)
        frame = self._scope(fields, source_sheet)
        output = []
        for field in fields:
            series = frame[field]
            nonempty = series.dropna()[series.dropna().astype(str).str.strip() != ""]
            item: dict[str, Any] = {"field": field, "rows": len(frame), "nonempty": len(nonempty), "distinct": int(nonempty.astype(str).nunique())}
            numeric = pd.to_numeric(nonempty, errors="coerce")
            if len(nonempty) and numeric.notna().mean() >= 0.8:
                item["numeric"] = {key: _jsonable(value) for key, value in numeric.describe(percentiles=[.25, .5, .75]).to_dict().items()}
                item["sign_counts"] = {"positive": int((numeric > 0).sum()), "negative": int((numeric < 0).sum()), "zero": int((numeric == 0).sum())}
            elif not _is_sensitive(field, self.profiles.get(field)) and item["distinct"] <= 40:
                item["top_values"] = [{"value": _jsonable(value), "count": int(count)} for value, count in nonempty.astype(str).value_counts().head(12).items()]
            else:
                item["values_withheld"] = True
            output.append(item)
        return {"profiles": output, "scope_rows": len(frame)}

    def sample_masked_rows(self, fields: list[str], source_sheet: str | None = None, limit: int = 5) -> dict[str, Any]:
        fields = self._validate_fields(fields, 12)
        frame = self._scope(fields, source_sheet)
        limit = max(1, min(int(limit), 5))
        if frame.empty:
            return {"rows": [], "note": "no rows"}
        indexes = sorted(set(int(round(i)) for i in pd.Series(range(limit)).map(lambda i: i * (len(frame) - 1) / max(limit - 1, 1))))
        sample = frame.iloc[indexes][fields]
        return {"rows": [{field: self._mask(field, row[field], representative_row=True) for field in fields} for _, row in sample.iterrows()], "masking": "identifiers and sensitive values are stable aliases; row-level numbers and dates are generalized"}

    def group_aggregate(self, group_by: list[str], metrics: list[dict[str, Any]], filters: list[dict[str, Any]] | None = None, source_sheet: str | None = None, sort_by: str | None = None, limit: int = 20) -> dict[str, Any]:
        group_by = self._validate_fields(group_by, 3)
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 8:
            raise ValueError("metrics must contain 1-8 items")
        metric_fields = [item.get("field") for item in metrics if item.get("field")]
        fields = list(dict.fromkeys([*group_by, *metric_fields, *(item.get("field") for item in filters or [])]))
        frame = self._filters(self._scope(fields, source_sheet), filters or []).copy()
        grouped = frame.groupby(group_by, dropna=False, sort=False)
        result = grouped.size().rename("__cell_rows__").reset_index()
        for metric in metrics:
            aggregation = metric.get("aggregation")
            field = metric.get("field")
            name = str(metric.get("name") or f"{aggregation}_{field or 'rows'}")[:50]
            if aggregation == "count_rows":
                values = grouped.size()
            elif aggregation in {"count_nonempty", "count_distinct"}:
                if field not in self.column_set:
                    raise ValueError(f"{aggregation} requires a real field")
                normalized = frame[field].where(frame[field].notna() & frame[field].astype(str).str.strip().ne(""))
                temp = frame.assign(__measure__=normalized).groupby(group_by, dropna=False, sort=False)["__measure__"]
                values = temp.count() if aggregation == "count_nonempty" else temp.nunique(dropna=True)
            elif aggregation in {"sum", "average", "min", "max"}:
                if field not in self.column_set:
                    raise ValueError(f"{aggregation} requires a real field")
                temp = frame.assign(__measure__=self._series_number(field, frame)).groupby(group_by, dropna=False, sort=False)["__measure__"]
                values = {"sum": temp.sum, "average": temp.mean, "min": temp.min, "max": temp.max}[aggregation]()
            else: raise ValueError(f"unsupported aggregation: {aggregation}")
            result = result.merge(values.rename(name).reset_index(), on=group_by, how="left")
        suppressed = int((result["__cell_rows__"] < MIN_CELL_SIZE).sum())
        result = result[result["__cell_rows__"] >= MIN_CELL_SIZE].drop(columns="__cell_rows__")
        if sort_by and sort_by in result.columns:
            result = result.sort_values(sort_by, ascending=False)
        limit = max(1, min(int(limit), MAX_RESULT_ROWS))
        rows = []
        for _, row in result.head(limit).iterrows():
            rows.append({column: self._mask(column, row[column]) if column in group_by else _jsonable(round(float(row[column]), 2) if isinstance(row[column], float) else row[column]) for column in result.columns})
        return {"rows": rows, "groups_returned": len(rows), "groups_suppressed": suppressed, "source_rows": len(frame), "full_data_computation": True}

    def time_aggregate(self, date_field: str, measure_field: str, aggregation: str = "sum", grain: str = "month", source_sheet: str | None = None) -> dict[str, Any]:
        self._validate_fields([date_field, measure_field], 2)
        if grain not in {"day", "month", "quarter"} or aggregation not in {"sum", "average", "count"}:
            raise ValueError("unsupported time aggregation")
        frame = self._scope([date_field, measure_field], source_sheet).copy()
        dates = pd.to_datetime(frame[date_field], errors="coerce")
        frame = frame[dates.notna()].copy(); dates = dates[dates.notna()]
        frame["__period__"] = dates.dt.strftime("%Y-%m-%d") if grain == "day" else dates.dt.to_period("M" if grain == "month" else "Q").astype(str)
        measure = self._series_number(measure_field, frame)
        grouped = frame.assign(__measure__=measure).groupby("__period__")["__measure__"]
        values = {"sum": grouped.sum, "average": grouped.mean, "count": grouped.count}[aggregation]().sort_index()
        return {"periods": [{"period": period, "value": _jsonable(round(float(value), 2))} for period, value in values.head(MAX_RESULT_ROWS).items()], "period_count": len(values), "full_data_computation": True}

    def check_relationship(self, left_field: str, right_field: str, source_sheet: str | None = None) -> dict[str, Any]:
        self._validate_fields([left_field, right_field], 2)
        if left_field == right_field:
            raise ValueError("relationship fields must be different")
        frame = self._scope([left_field, right_field], source_sheet)[[left_field, right_field]].dropna().drop_duplicates()
        if frame.empty:
            return {"pairs": 0, "relationship": "unknown"}
        left_degree = frame.groupby(left_field)[right_field].nunique(); right_degree = frame.groupby(right_field)[left_field].nunique()
        one_left = float((left_degree == 1).mean()); one_right = float((right_degree == 1).mean())
        relationship = "one_to_one" if one_left == one_right == 1 else "many_to_one" if one_left == 1 else "one_to_many" if one_right == 1 else "many_to_many"
        return {"relationship": relationship, "distinct_pairs": len(frame), "left_distinct": int(frame[left_field].nunique()), "right_distinct": int(frame[right_field].nunique()), "left_single_mapping_rate": round(one_left, 4), "right_single_mapping_rate": round(one_right, 4), "values_returned": False}

    def check_key_uniqueness(self, source_sheet: str, key_fields: list[str]) -> dict[str, Any]:
        fields = self._validate_fields(key_fields, 4)
        if source_sheet not in self.sheet_columns:
            raise ValueError(f"unknown source sheet: {source_sheet}")
        if not set(fields) <= self.sheet_columns[source_sheet]:
            raise ValueError(f"key fields do not coexist on source sheet: {source_sheet}")
        part = self._load()
        part = part[part["__source_sheet__"] == source_sheet][fields]
        complete_mask = part.notna().all(axis=1)
        for field in fields:
            complete_mask &= part[field].astype(str).str.strip().ne("")
        complete = part[complete_mask].astype(str)
        distinct_keys = len(complete.drop_duplicates())
        return {
            "source_sheet": source_sheet,
            "key_fields": fields,
            "source_rows": len(part),
            "complete_key_rows": len(complete),
            "blank_key_rows": len(part) - len(complete),
            "distinct_keys": distinct_keys,
            "duplicate_key_rows": len(complete) - distinct_keys,
            "unique_on_keys": len(complete) == distinct_keys,
            "key_values_returned": False,
            "full_data_computation": True,
        }

    def check_join_keys(self, left_sheet: str, right_sheet: str, key_fields: list[str]) -> dict[str, Any]:
        fields = self._validate_fields(key_fields, 4)
        if left_sheet == right_sheet:
            raise ValueError("join sheets must be different")
        for sheet in (left_sheet, right_sheet):
            if sheet not in self.sheet_columns:
                raise ValueError(f"unknown source sheet: {sheet}")
            if not set(fields) <= self.sheet_columns[sheet]:
                raise ValueError(f"join keys do not coexist on source sheet: {sheet}")
        frame = self._load()

        def key_stats(sheet: str) -> tuple[dict[str, Any], set[tuple[str, ...]]]:
            part = frame[frame["__source_sheet__"] == sheet][fields]
            complete_mask = part.notna().all(axis=1)
            for field in fields:
                complete_mask &= part[field].astype(str).str.strip().ne("")
            complete = part[complete_mask].astype(str)
            keys = set(map(tuple, complete.itertuples(index=False, name=None)))
            return {
                "sheet": sheet,
                "source_rows": len(part),
                "complete_key_rows": len(complete),
                "blank_key_rows": len(part) - len(complete),
                "distinct_keys": len(keys),
                "duplicate_key_rows": len(complete) - len(keys),
                "unique_on_keys": len(complete) == len(keys),
            }, keys

        left_stats, left_keys = key_stats(left_sheet)
        right_stats, right_keys = key_stats(right_sheet)
        matched = len(left_keys & right_keys)
        if left_stats["unique_on_keys"] and right_stats["unique_on_keys"]:
            relationship = "one_to_one"
        elif right_stats["unique_on_keys"]:
            relationship = "many_to_one"
        elif left_stats["unique_on_keys"]:
            relationship = "one_to_many"
        else:
            relationship = "many_to_many"
        return {
            "key_fields": fields,
            "left": left_stats,
            "right": right_stats,
            "relationship_at_raw_grain": relationship,
            "matched_distinct_keys": matched,
            "left_key_match_rate": round(matched / max(len(left_keys), 1), 4),
            "right_key_match_rate": round(matched / max(len(right_keys), 1), 4),
            "safe_raw_join": relationship != "many_to_many" and not left_stats["blank_key_rows"] and not right_stats["blank_key_rows"],
            "key_values_returned": False,
        }

    def top_concentration(self, entity_field: str, measure_field: str, top_n: int = 10, source_sheet: str | None = None) -> dict[str, Any]:
        self._validate_fields([entity_field, measure_field], 2)
        frame = self._scope([entity_field, measure_field], source_sheet).copy()
        frame["__measure__"] = self._series_number(measure_field, frame).abs()
        values = frame.dropna(subset=[entity_field]).groupby(entity_field)["__measure__"].sum().sort_values(ascending=False)
        total = float(values.sum()); top_n = max(1, min(int(top_n), 50))
        share = float(values.head(top_n).sum()) / total if total else 0
        return {"distinct_entities": len(values), "top_n": top_n, "top_n_share": round(share, 4), "top1_share": round(float(values.head(1).sum()) / total, 4) if total else 0, "entity_values_returned": False, "full_data_computation": True}

    def check_sheet_overlap(self, identifier_fields: list[str]) -> dict[str, Any]:
        fields = self._validate_fields(identifier_fields, 3)
        supporting = [name for name, columns in self.sheet_columns.items() if set(fields) <= columns]
        if len(supporting) < 2:
            raise ValueError("fewer than two sheets contain all identifier fields")
        frame = self._load()
        keys = {}
        for sheet in supporting:
            part = frame[frame["__source_sheet__"] == sheet][fields].dropna().astype(str).drop_duplicates()
            keys[sheet] = set(map(tuple, part.itertuples(index=False, name=None)))
        pairs = []
        for index, left in enumerate(supporting):
            for right in supporting[index + 1:]:
                overlap = len(keys[left] & keys[right])
                pairs.append({"left": left, "right": right, "overlap": overlap,
                              "left_overlap_rate": round(overlap / max(len(keys[left]), 1), 4),
                              "right_overlap_rate": round(overlap / max(len(keys[right]), 1), 4)})
        signatures = {str(item.get("signature") or "") for item in self.dataset.get("sheets", []) if item.get("sheet") in supporting}
        max_overlap_rate = max((max(item["left_overlap_rate"], item["right_overlap_rate"]) for item in pairs), default=0)
        same_schema = len(signatures) == 1
        return {"identifier_fields": fields, "sheets": [{"sheet": name, "distinct_keys": len(keys[name])} for name in supporting],
                "pairwise_overlap": pairs, "same_schema": same_schema, "max_overlap_rate": max_overlap_rate,
                "likely_row_partitions": bool(same_schema and max_overlap_rate <= .01),
                "scope_hint": "aggregate all supporting sheets together unless the user requests separate partitions" if same_schema and max_overlap_rate <= .01 else "keep sheet scope explicit until semantics are confirmed",
                "identifier_values_returned": False}

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in TOOL_CATALOG:
            raise ValueError(f"unknown tool: {tool}")
        started = time.perf_counter()
        result = getattr(self, tool)(**arguments)
        self.compute_seconds += time.perf_counter() - started
        return result


class MemoryStore:
    @staticmethod
    def retrieve(dataset: dict[str, Any]) -> list[dict[str, Any]]:
        if not CONFIRMED_MEMORY_PATH.exists():
            return []
        records = json.loads(CONFIRMED_MEMORY_PATH.read_text(encoding="utf-8"))
        signature = _schema_signature(dataset)
        fields = set(dataset.get("schema", {}).get("columns", []))
        matches = []
        for record in records:
            stored = set(record.get("schema_fields", []))
            similarity = len(fields & stored) / max(len(fields | stored), 1)
            if record.get("schema_signature") == signature or similarity >= .75:
                matches.append({"memory_id": record.get("memory_id"), "similarity": round(similarity, 3), "semantic_roles": record.get("semantic_roles", []), "confirmed_decisions": record.get("confirmed_decisions", []), "workbook_package_titles": record.get("workbook_package_titles", [])})
        return sorted(matches, key=lambda item: item["similarity"], reverse=True)[:3]

    @staticmethod
    def propose(path: Path, dataset: dict[str, Any], goal: str, final: dict[str, Any]) -> None:
        proposal = {
            "status": "pending_user_confirmation", "memory_id": uuid.uuid4().hex[:12],
            "schema_signature": _schema_signature(dataset), "schema_fields": dataset.get("schema", {}).get("columns", []),
            "user_goal": goal, "semantic_roles": final.get("dataset_understanding", {}).get("semantic_roles", []),
            "confirmed_decisions": [], "workbook_package_titles": [item.get("title") for item in final.get("workbook_packages", [])],
            "workbook_blueprint": final, "raw_rows_stored": False, "model_hypotheses_promoted": False,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def confirm(proposal_path: Path) -> dict[str, Any]:
        resolved = proposal_path.resolve()
        if OUTPUT_ROOT.resolve() not in resolved.parents or resolved.name != "long_term_memory_proposal.json":
            raise ValueError("only an Agent-generated memory proposal can be confirmed")
        proposal = json.loads(resolved.read_text(encoding="utf-8"))
        if proposal.get("status") != "pending_user_confirmation" or proposal.get("raw_rows_stored") is not False:
            raise ValueError("memory proposal is invalid or already processed")
        MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
        records = json.loads(CONFIRMED_MEMORY_PATH.read_text(encoding="utf-8")) if CONFIRMED_MEMORY_PATH.exists() else []
        proposal["status"] = "confirmed"
        proposal["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        records = [item for item in records if item.get("memory_id") != proposal.get("memory_id")]
        records.append(proposal)
        temp = CONFIRMED_MEMORY_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(CONFIRMED_MEMORY_PATH)
        return {"memory_id": proposal["memory_id"], "status": "confirmed", "path": str(CONFIRMED_MEMORY_PATH)}


def _evidence_fields(item: dict[str, Any], real_fields: set[str]) -> set[str]:
    found: set[str] = set()
    def visit(value: Any) -> None:
        if isinstance(value, str) and value in real_fields:
            found.add(value)
        elif isinstance(value, dict):
            for nested in value.values(): visit(nested)
        elif isinstance(value, list):
            for nested in value: visit(nested)
    visit(item.get("arguments", {}))
    return found


DIMENSION_KINDS = {"field", "source_sheet", "time_bucket", "numeric_bucket"}
METRIC_AGGREGATIONS = {
    "sum", "average", "min", "max", "count_rows", "count_nonempty",
    "count_distinct", "value_after_uniqueness_check", "derived_after_confirmation",
}
PRE_AGGREGATION_METHODS = METRIC_AGGREGATIONS - {"derived_after_confirmation"}
JOIN_STRATEGIES = {"append_aligned_sheets", "aggregate_then_join", "lookup_join"}
JOIN_TYPES = {"full", "left", "inner"}
CLAIM_EVIDENCE_PATTERNS = (
    (re.compile(r"集中度(?:较高|很高)|高度集中|明显集中|头部.{0,8}占比(?:较高|很高)"), "top_concentration"),
    (re.compile(r"(?:显著|明显|大幅)(?:增长|下降|上升|减少)|波动(?:较大|明显|剧烈)|异常波动"), "time_aggregate"),
)
ENTITY_COUNT_PATTERN = re.compile(r"人数|人员数|员工数|客户数|用户数|会员数|订单数|商品数|产品数|项目数|设备数|资产数")


def _normalize_question(value: Any) -> str:
    return re.sub(r"[\s？?。！!，,；;：:]", "", str(value or "")).lower()


def _normalize_final(final: dict[str, Any], evidence: list[dict[str, Any]], dataset: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(final)
    verified = {item["evidence_id"]: item for item in evidence if item.get("status") == "verified"}
    blockers = [str(item) for item in normalized.get("blocking_questions", []) if str(item).strip()]
    sheet_columns = {item.get("sheet"): set(item.get("columns", [])) for item in dataset.get("sheets", [])}
    named_metric_definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for package in normalized.get("workbook_packages", []):
        for sheet in package.get("sheets", []):
            for metric in sheet.get("metrics", []):
                if isinstance(metric, dict) and str(metric.get("name") or "").strip():
                    named_metric_definitions[str(metric["name"])].append(metric)

    def add_question(package: dict[str, Any], question: str) -> None:
        package.setdefault("clarifications", [])
        if _normalize_question(question) not in {_normalize_question(item) for item in package["clarifications"]}:
            package["clarifications"].append(question)
        if _normalize_question(question) not in {_normalize_question(item) for item in blockers}:
            blockers.append(question)

    understanding = normalized.get("dataset_understanding") or {}
    for role in understanding.get("semantic_roles", []):
        if isinstance(role, dict):
            role["basis"] = [item for item in role.get("basis", []) if item in verified]

    for package in normalized.get("workbook_packages", []):
        for key in ("purpose", "why_valuable"):
            package[key] = str(package.get(key) or "").replace("可以安全", "可以").replace("可安全", "可")
        package["evidence_ids"] = [item for item in package.get("evidence_ids", []) if item in verified]
        for question in package.get("clarifications", []):
            if _normalize_question(question) not in {_normalize_question(item) for item in blockers}:
                blockers.append(question)
        for sheet in package.get("sheets", []):
            sheet["evidence_ids"] = [item for item in sheet.get("evidence_ids", []) if item in verified]
            scope = sheet.get("source_scope", [])
            plan = sheet.get("join_plan")
            if len(scope) > 1 and isinstance(plan, dict):
                sheet.setdefault("evidence_ids", [])
                strategy = plan.get("strategy")
                if strategy == "append_aligned_sheets":
                    candidates = []
                    for evidence_id, item in verified.items():
                        if item.get("tool") != "check_sheet_overlap":
                            continue
                        checked_sheets = {entry.get("sheet") for entry in item.get("result", {}).get("sheets", [])}
                        if set(scope) <= checked_sheets:
                            candidates.append(evidence_id)
                    if candidates and not any(item in sheet["evidence_ids"] for item in candidates):
                        sheet["evidence_ids"].append(candidates[0])
                    dimensions = sheet.setdefault("dimensions", [])
                    if not any(isinstance(item, dict) and item.get("kind") == "source_sheet" for item in dimensions):
                        dimensions.insert(0, {"name": "来源工作表", "kind": "source_sheet", "source_fields": [], "parameters": {}, "requires_confirmation": False})
                else:
                    plan["join_type"] = plan.get("join_type") if plan.get("join_type") in JOIN_TYPES else "full"
                    plan["base_source"] = plan.get("base_source") if plan["join_type"] == "left" else None
                    plan["field_aliases"] = []
                    keys = set(plan.get("join_keys", []))
                    for evidence_id, item in verified.items():
                        if item.get("tool") != "check_join_keys" or set(item.get("arguments", {}).get("key_fields", [])) != keys:
                            continue
                        pair = {item.get("arguments", {}).get("left_sheet"), item.get("arguments", {}).get("right_sheet")}
                        if pair <= set(scope) and evidence_id not in sheet["evidence_ids"]:
                            sheet["evidence_ids"].append(evidence_id)

                    if strategy == "aggregate_then_join":
                        steps = plan.get("pre_aggregation", [])
                        raw_dimension_fields = {
                            field
                            for dimension in sheet.get("dimensions", []) if isinstance(dimension, dict)
                            for field in dimension.get("source_fields", [])
                        }
                        final_at_join_grain = bool(keys) and raw_dimension_fields <= keys and all(
                            isinstance(step, dict) and set(step.get("grain", [])) == keys for step in steps
                        )
                        for step in steps if isinstance(steps, list) else []:
                            if not isinstance(step, dict):
                                continue
                            source = step.get("source_sheet")
                            source_fields = sheet_columns.get(source, set())
                            grain = set(step.get("grain", []))
                            upgraded_metrics: list[dict[str, Any]] = []
                            for value in step.get("metrics", []) if isinstance(step.get("metrics", []), list) else []:
                                if isinstance(value, dict):
                                    if value.get("aggregation") == "count_rows":
                                        value["source_fields"] = []
                                    upgraded_metrics.append(value)
                                    continue
                                metric_name = str(value or "").strip()
                                if not metric_name:
                                    continue
                                if metric_name in source_fields:
                                    matching = [
                                        metric for metric in sheet.get("metrics", [])
                                        if isinstance(metric, dict) and metric.get("source_fields") == [metric_name]
                                        and metric.get("aggregation") in PRE_AGGREGATION_METHODS
                                    ]
                                    if final_at_join_grain and metric_name not in grain and matching:
                                        for metric in matching:
                                            output_name = str(metric.get("name") or metric_name)
                                            upgraded_metrics.append({
                                                "name": output_name,
                                                "aggregation": metric["aggregation"],
                                                "source_fields": [metric_name],
                                            })
                                            metric["aggregation"] = "value_after_uniqueness_check"
                                            metric["source_fields"] = [output_name]
                                    elif metric_name not in grain:
                                        upgraded_metrics.append({
                                            "name": metric_name,
                                            "aggregation": "value_after_uniqueness_check",
                                            "source_fields": [metric_name],
                                        })
                                    continue
                                candidates = [
                                    metric for metric in named_metric_definitions.get(metric_name, [])
                                    if metric.get("aggregation") in PRE_AGGREGATION_METHODS
                                    and isinstance(metric.get("source_fields"), list)
                                    and set(metric.get("source_fields", [])) <= source_fields
                                ]
                                if len(candidates) == 1:
                                    definition = candidates[0]
                                    upgraded_metrics.append({
                                        "name": metric_name,
                                        "aggregation": definition["aggregation"],
                                        "source_fields": list(definition["source_fields"]),
                                    })
                            step["metrics"] = upgraded_metrics

                        grain_fields = {
                            field
                            for step in steps if isinstance(step, dict)
                            for field in step.get("grain", [])
                        }
                        intermediate_metrics = [
                            metric
                            for step in steps if isinstance(step, dict)
                            for metric in step.get("metrics", []) if isinstance(metric, dict)
                        ]
                        intermediate_names = {
                            str(metric.get("name") or "") for metric in intermediate_metrics
                            if str(metric.get("name") or "").strip()
                        }
                        post_join_fields = grain_fields | intermediate_names
                        output_includes_join_keys = bool(keys) and keys <= raw_dimension_fields
                        for metric in sheet.get("metrics", []):
                            if not isinstance(metric, dict) or not isinstance(metric.get("source_fields"), list):
                                continue
                            current_inputs = set(metric["source_fields"])
                            if current_inputs and current_inputs <= intermediate_names:
                                continue
                            candidates = [
                                candidate for candidate in intermediate_metrics
                                if candidate.get("aggregation") == metric.get("aggregation")
                                and candidate.get("source_fields") == metric.get("source_fields")
                            ]
                            final_name = str(metric.get("name") or "")
                            exact_name_matches = [
                                candidate for candidate in candidates
                                if str(candidate.get("name") or "") == final_name
                            ]
                            name_matches = [
                                candidate for candidate in candidates
                                if final_name and (
                                    final_name in str(candidate.get("name") or "")
                                    or str(candidate.get("name") or "") in final_name
                                )
                            ]
                            selected = (
                                exact_name_matches[0] if len(exact_name_matches) == 1
                                else name_matches[0] if len(name_matches) == 1
                                else candidates[0] if len(candidates) == 1
                                else None
                            )
                            if selected:
                                if output_includes_join_keys:
                                    metric["source_fields"] = [selected["name"]]
                                    metric["aggregation"] = "value_after_uniqueness_check"
                                elif selected.get("aggregation") in {"sum", "count_rows", "count_nonempty"}:
                                    metric["source_fields"] = [selected["name"]]
                                    metric["aggregation"] = "sum"
                                elif selected.get("aggregation") == "count_distinct" and set(selected.get("source_fields", [])) <= keys:
                                    metric["source_fields"] = [selected["name"]]
                                    metric["aggregation"] = "sum"
                                elif selected.get("aggregation") in {"min", "max"}:
                                    metric["source_fields"] = [selected["name"]]
                                    metric["aggregation"] = selected["aggregation"]

                aliases = plan.get("field_aliases", [])
                for alias in aliases if isinstance(aliases, list) else []:
                    fields = sorted({item.get("field") for item in alias.get("bindings", []) if isinstance(item, dict) and item.get("field")})
                    if len(fields) > 1:
                        plan["requires_confirmation"] = True
                        add_question(package, f"字段「{'、'.join(fields)}」是否表示同一业务指标，并允许跨工作表合并计算？")

            for dimension in sheet.get("dimensions", []):
                if isinstance(dimension, dict) and dimension.get("requires_confirmation"):
                    add_question(package, f"请确认维度「{dimension.get('name')}」的分组阈值或转换口径？")
            for metric in sheet.get("metrics", []):
                if isinstance(metric, dict) and metric.get("aggregation") == "count_rows":
                    metric["source_fields"] = []
                if metric.get("aggregation") == "derived_after_confirmation":
                    add_question(package, f"请确认指标「{metric.get('name')}」按以下口径计算：{metric.get('calculation_intent')}？")

    normalized["blocking_questions"] = blockers
    normalized["execution_status"] = "needs_clarification" if blockers else "ready"
    return normalized


def _validate_final(final: dict[str, Any], evidence: list[dict[str, Any]], dataset: dict[str, Any], precise_goal: bool) -> None:
    verified = {item["evidence_id"]: item for item in evidence if item.get("status") == "verified"}
    evidence_ids = set(verified)
    packages = final.get("workbook_packages")
    expected = (0, 1) if precise_goal else (0, 3)
    if not isinstance(packages, list) or not expected[0] <= len(packages) <= expected[1]:
        raise ValueError(f"expected {expected[0]}-{expected[1]} workbook packages")
    fields = set(dataset.get("schema", {}).get("columns", []))
    sheet_columns = {item.get("sheet"): set(item.get("columns", [])) for item in dataset.get("sheets", [])}
    sheets = set(sheet_columns)
    errors = []

    def has_unique_key_evidence(source_sheet: str) -> bool:
        for item in verified.values():
            if item.get("tool") == "check_key_uniqueness":
                if item.get("arguments", {}).get("source_sheet") == source_sheet and item.get("result", {}).get("unique_on_keys") is True:
                    return True
            if item.get("tool") == "check_join_keys":
                result = item.get("result", {})
                for side in ("left", "right"):
                    if result.get(side, {}).get("sheet") == source_sheet and result.get(side, {}).get("unique_on_keys") is True:
                        return True
        return False

    blockers = final.get("blocking_questions", [])
    if not isinstance(blockers, list) or any(not str(item).strip() for item in blockers):
        errors.append("blocking_questions must be a list of non-empty questions")
        blockers = []
    normalized_blockers = {_normalize_question(item) for item in blockers}
    has_pending_derived_metric = any(
        isinstance(metric, dict) and metric.get("aggregation") == "derived_after_confirmation"
        for package in packages or []
        for sheet in package.get("sheets", [])
        for metric in sheet.get("metrics", [])
    )
    if not has_pending_derived_metric:
        for question in blockers:
            if re.search(r"(?:是否需要|计算是否需要).{0,30}(?:新增|加入|包含|展示|计算|分析|判定|分类)", str(question)):
                errors.append(f"optional addition must not block the current package: {question}")
    expected_status = "needs_clarification" if blockers else "ready"
    if final.get("execution_status") != expected_status:
        errors.append(f"execution_status must be {expected_status}")
    package_titles = {str(item.get("title") or "") for item in packages or []}
    if packages and final.get("recommended_package_title") not in package_titles:
        errors.append("recommended_package_title must identify a returned package")
    if not packages and final.get("recommended_package_title") not in (None, ""):
        errors.append("recommended_package_title must be null when no package is returned")
    if not packages and not blockers:
        errors.append("zero-package result must ask a blocking question")

    understanding = final.get("dataset_understanding") or {}
    for role in understanding.get("semantic_roles", []):
        role_fields = set(role.get("fields", []))
        role_sheets = set(role.get("source_sheets", []))
        role_evidence = set(role.get("basis", []))
        if role_fields - fields or role_sheets - sheets:
            errors.append(f"semantic role {role.get('role')} references unknown fields or source sheets")
        if not role_fields and not role_sheets:
            errors.append(f"semantic role {role.get('role')} has no field or source-sheet binding")
        if not role_evidence or not role_evidence <= evidence_ids:
            errors.append(f"semantic role {role.get('role')} has invalid evidence references")

    for package in packages:
        package_evidence = set(package.get("evidence_ids", []))
        if not package_evidence or not package_evidence <= evidence_ids:
            errors.append(f"package {package.get('title')} has invalid evidence references")
        else:
            claim_text = " ".join(str(package.get(key) or "") for key in ("purpose", "why_valuable"))
            package_tools = {verified[item]["tool"] for item in package_evidence}
            if "待验证" not in claim_text:
                for pattern, required_tool in CLAIM_EVIDENCE_PATTERNS:
                    if pattern.search(claim_text) and required_tool not in package_tools:
                        errors.append(f"package {package.get('title')} makes a qualitative claim without {required_tool} evidence")
        clarifications = package.get("clarifications", [])
        if not isinstance(clarifications, list):
            errors.append(f"package {package.get('title')} clarifications must be a list")
            clarifications = []
        for question in clarifications:
            if _normalize_question(question) not in normalized_blockers:
                errors.append(f"package {package.get('title')} clarification is missing from blocking_questions: {question}")
        package_text = " ".join(str(package.get(key) or "") for key in ("title", "purpose", "why_valuable"))
        for clause in re.split(r"[。；;]", package_text):
            if "唯一" not in clause:
                continue
            for source_sheet in sheets:
                if source_sheet in clause and not has_unique_key_evidence(source_sheet):
                    errors.append(f"package {package.get('title')} claims {source_sheet} uniqueness without verified key evidence")
        metric_text = " ".join(
            " ".join(str(metric.get(key) or "") for key in ("name", "calculation_intent"))
            for sheet in package.get("sheets", [])
            for metric in sheet.get("metrics", []) if isinstance(metric, dict)
        )
        if re.search(r"成本|费用", package_text) and not re.search(r"成本|费用|工资|薪资|金额|单价|价格", metric_text):
            errors.append(f"package {package.get('title')} claims cost value without a supporting cost/amount metric")
        if not precise_goal and re.search(r"数据质量|完整性检查|数据清洗|缺失值检查", package_text):
            errors.append(f"package {package.get('title')} is a data-quality task, not a target workbook direction")
        if clarifications and re.search(r"可安全|可以安全|可直接", package_text) and "确认后" not in package_text:
            errors.append(f"package {package.get('title')} claims safety before its blocking semantics are confirmed")
        package_sheets = package.get("sheets", [])
        if not 2 <= len(package_sheets) <= 5:
            errors.append(f"package {package.get('title')} must contain 2-5 sheets")
        signatures = set()
        for sheet in package_sheets:
            sheet_evidence = set(sheet.get("evidence_ids", []))
            if not sheet_evidence <= evidence_ids or not sheet_evidence:
                errors.append(f"sheet {sheet.get('title')} has invalid evidence references")
            scope = sheet.get("source_scope", [])
            bad_sheets = sorted(set(scope) - sheets) if isinstance(scope, list) else [str(scope)]
            if not isinstance(scope, list) or not scope:
                errors.append(f"sheet {sheet.get('title')} must declare source_scope")
                continue
            if bad_sheets:
                errors.append(f"sheet {sheet.get('title')} has unknown source_sheets={bad_sheets}")
                continue

            used: set[str] = set()
            join_plan = sheet.get("join_plan")
            virtual_fields: dict[str, set[str]] = {}
            post_join_fields: set[str] = set()
            if len(scope) > 1 and isinstance(join_plan, dict) and join_plan.get("strategy") == "aggregate_then_join":
                join_keys = join_plan.get("join_keys", [])
                steps = join_plan.get("pre_aggregation", [])
                if not isinstance(steps, list):
                    errors.append(f"sheet {sheet.get('title')} pre_aggregation must be a list")
                    steps = []
                virtual_names: set[str] = set()
                for step in steps:
                    if not isinstance(step, dict):
                        errors.append(f"sheet {sheet.get('title')} has an unstructured pre-aggregation step")
                        continue
                    source = step.get("source_sheet")
                    grain = step.get("grain", [])
                    step_metrics = step.get("metrics", [])
                    if source not in sheet_columns or not isinstance(grain, list) or not set(join_keys) <= set(grain) or not set(grain) <= sheet_columns.get(source, set()):
                        errors.append(f"sheet {sheet.get('title')} has an invalid pre-aggregation grain for {source}")
                    used.update(grain if isinstance(grain, list) else [])
                    post_join_fields.update(grain if isinstance(grain, list) else [])
                    if not isinstance(step_metrics, list):
                        errors.append(f"sheet {sheet.get('title')} has invalid pre-aggregation metrics for {source}")
                        continue
                    for metric in step_metrics:
                        if not isinstance(metric, dict):
                            errors.append(f"sheet {sheet.get('title')} has an unstructured pre-aggregation metric for {source}")
                            continue
                        name = str(metric.get("name") or "").strip()
                        aggregation = metric.get("aggregation")
                        source_fields = metric.get("source_fields", [])
                        if not name or name in virtual_names:
                            errors.append(f"sheet {sheet.get('title')} has a blank or duplicate pre-aggregation metric {name}")
                        virtual_names.add(name)
                        if aggregation not in PRE_AGGREGATION_METHODS:
                            errors.append(f"sheet {sheet.get('title')} pre-aggregation metric {name} has unsupported aggregation={aggregation}")
                        if not isinstance(source_fields, list) or not set(source_fields) <= sheet_columns.get(source, set()):
                            errors.append(f"sheet {sheet.get('title')} pre-aggregation metric {name} must bind real fields on {source}")
                            continue
                        if aggregation == "count_rows" and source_fields:
                            errors.append(f"sheet {sheet.get('title')} pre-aggregation count_rows metric {name} must not bind fields")
                        if ENTITY_COUNT_PATTERN.search(name) and aggregation != "count_distinct":
                            errors.append(f"sheet {sheet.get('title')} pre-aggregation entity-count metric {name} must use count_distinct")
                        if aggregation != "count_rows" and not source_fields:
                            errors.append(f"sheet {sheet.get('title')} pre-aggregation metric {name} must bind a field")
                        if aggregation in {"count_distinct", "value_after_uniqueness_check"} and len(source_fields) != 1:
                            errors.append(f"sheet {sheet.get('title')} pre-aggregation metric {name} must bind exactly one field")
                        used.update(source_fields)
                        virtual_fields[name] = set(source_fields)
                        post_join_fields.add(name)

            dimension_names: set[str] = set()
            output_dimension_fields: set[str] = set()
            dimensions = sheet.get("dimensions", [])
            if not isinstance(dimensions, list):
                errors.append(f"sheet {sheet.get('title')} dimensions must be structured objects")
                dimensions = []
            for dimension in dimensions:
                if not isinstance(dimension, dict):
                    errors.append(f"sheet {sheet.get('title')} has an unstructured dimension")
                    continue
                name = str(dimension.get("name") or "").strip()
                kind = dimension.get("kind")
                source_fields = dimension.get("source_fields", [])
                parameters = dimension.get("parameters") or {}
                if not name or name in dimension_names:
                    errors.append(f"sheet {sheet.get('title')} has a blank or duplicate dimension name")
                dimension_names.add(name)
                if kind not in DIMENSION_KINDS:
                    errors.append(f"sheet {sheet.get('title')} dimension {name} has unsupported kind={kind}")
                    continue
                if not isinstance(source_fields, list):
                    errors.append(f"sheet {sheet.get('title')} dimension {name} has invalid source_fields")
                    continue
                used.update(source_fields)
                output_dimension_fields.update(source_fields)
                if kind == "source_sheet" and source_fields:
                    errors.append(f"sheet {sheet.get('title')} source_sheet dimension {name} must not bind fields")
                if kind in {"field", "time_bucket", "numeric_bucket"} and len(source_fields) != 1:
                    errors.append(f"sheet {sheet.get('title')} dimension {name} must bind exactly one field")
                if kind == "time_bucket" and parameters.get("grain") not in {"day", "month", "quarter", "year"}:
                    errors.append(f"sheet {sheet.get('title')} time dimension {name} has invalid grain")
                if kind == "numeric_bucket" and not dimension.get("requires_confirmation"):
                    boundaries = parameters.get("boundaries")
                    if not isinstance(boundaries, list) or not boundaries or any(not isinstance(value, (int, float)) for value in boundaries) or boundaries != sorted(set(boundaries)):
                        errors.append(f"sheet {sheet.get('title')} numeric bucket {name} needs confirmed increasing boundaries")
                if dimension.get("requires_confirmation") and not clarifications:
                    errors.append(f"sheet {sheet.get('title')} dimension {name} requires a blocking clarification")

            metrics = sheet.get("metrics", [])
            if not isinstance(metrics, list) or not metrics:
                errors.append(f"sheet {sheet.get('title')} must contain metrics")
                metrics = []
            for metric in metrics:
                aggregation = metric.get("aggregation")
                source_fields = metric.get("source_fields", [])
                if aggregation not in METRIC_AGGREGATIONS:
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} has unsupported aggregation={aggregation}")
                    continue
                if not isinstance(source_fields, list):
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} has invalid source_fields")
                    continue
                unknown_inputs = set(source_fields) - fields - set(virtual_fields)
                if unknown_inputs:
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} has unknown inputs={sorted(unknown_inputs)}")
                if virtual_fields and aggregation != "count_rows" and set(source_fields) - set(virtual_fields):
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} must consume named pre-aggregation outputs")
                if virtual_fields and set(source_fields) - post_join_fields:
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} references fields unavailable after pre-aggregation={sorted(set(source_fields) - post_join_fields)}")
                resolved_source_fields = set().union(*(virtual_fields.get(field, {field}) for field in source_fields)) if source_fields else set()
                used.update(resolved_source_fields)
                if aggregation == "count_rows" and source_fields:
                    errors.append(f"sheet {sheet.get('title')} count_rows metric must not bind a field")
                if ENTITY_COUNT_PATTERN.search(str(metric.get("name") or "")) and aggregation == "count_rows":
                    errors.append(f"sheet {sheet.get('title')} entity-count metric {metric.get('name')} must not use count_rows")
                if virtual_fields and aggregation == "count_rows":
                    errors.append(f"sheet {sheet.get('title')} final count_rows metric {metric.get('name')} must consume a named pre-aggregation count")
                if aggregation in {"sum", "average", "min", "max", "count_nonempty", "count_distinct", "value_after_uniqueness_check"} and not source_fields:
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} must bind at least one field")
                if aggregation in {"count_distinct", "value_after_uniqueness_check"} and len(source_fields) != 1:
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} must bind exactly one field")
                if aggregation in {"sum", "average", "min", "max", "count_nonempty"} and len(source_fields) > 1 and len(scope) == 1:
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} can bind aliases only across appended sources")
                if aggregation == "derived_after_confirmation" and (not source_fields or not clarifications):
                    errors.append(f"sheet {sheet.get('title')} derived metric {metric.get('name')} requires fields and a blocking clarification")
                if "客单价" in str(metric.get("name") or "") and not any(re.search(r"订单|交易|客户数|购买人数|成交笔", field) for field in resolved_source_fields):
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} lacks an order/customer-count denominator")
                if (
                    virtual_fields
                    and isinstance(join_plan, dict)
                    and join_plan.get("strategy") == "aggregate_then_join"
                    and not set(join_plan.get("join_keys", [])) <= output_dimension_fields
                    and aggregation == "value_after_uniqueness_check"
                ):
                    errors.append(f"sheet {sheet.get('title')} metric {metric.get('name')} must aggregate intermediate values at its broader final grain")

            intent_text = " ".join(str(sheet.get(key) or "") for key in ("title", "purpose"))
            metric_names = " ".join(str(metric.get("name") or "") for metric in metrics)
            selection_logic = str(sheet.get("selection_logic") or "none")
            if "趋势" in intent_text and not any(isinstance(item, dict) and item.get("kind") == "time_bucket" for item in dimensions):
                errors.append(f"sheet {sheet.get('title')} promises a trend without a time_bucket dimension")
            if "分层" in intent_text and not any(isinstance(item, dict) and item.get("kind") == "numeric_bucket" for item in dimensions):
                errors.append(f"sheet {sheet.get('title')} promises segmentation without an executable bucket dimension")
            if "集中度" in intent_text and not re.search(r"占比|集中度|累计", metric_names):
                errors.append(f"sheet {sheet.get('title')} promises concentration without a share/concentration metric")
            if re.search(r"排名|排行|Top\s*\d*", intent_text, re.IGNORECASE) and selection_logic.strip().lower() == "none":
                errors.append(f"sheet {sheet.get('title')} promises ranking without executable selection/sort logic")
            if "识别" in intent_text and selection_logic.strip().lower() == "none":
                errors.append(f"sheet {sheet.get('title')} promises identification without executable selection/sort logic")
            if re.search(r"异常|风险清单", intent_text) and not re.search(r"阈值|大于|小于|低于|高于|偏离|标准差|IQR|分位|Top\s*\d*|前\s*\d+", selection_logic, re.IGNORECASE):
                errors.append(f"sheet {sheet.get('title')} promises anomaly selection without an executable threshold rule")

            if len(scope) > 1:
                if not isinstance(join_plan, dict) or join_plan.get("strategy") not in JOIN_STRATEGIES:
                    errors.append(f"sheet {sheet.get('title')} needs a valid multi-source join_plan")
                else:
                    strategy = join_plan.get("strategy")
                    join_keys = join_plan.get("join_keys", [])
                    if not isinstance(join_keys, list):
                        join_keys = []
                        errors.append(f"sheet {sheet.get('title')} has invalid join_keys")
                    used.update(join_keys)
                    cited_tools = {verified[item]["tool"] for item in sheet_evidence}
                    if strategy == "append_aligned_sheets":
                        if "check_sheet_overlap" not in cited_tools:
                            errors.append(f"sheet {sheet.get('title')} append plan lacks check_sheet_overlap evidence")
                        if not any(isinstance(item, dict) and item.get("kind") == "source_sheet" for item in dimensions):
                            errors.append(f"sheet {sheet.get('title')} append plan must preserve a source_sheet dimension")
                    else:
                        join_type = join_plan.get("join_type")
                        base_source = join_plan.get("base_source")
                        if join_type not in JOIN_TYPES:
                            errors.append(f"sheet {sheet.get('title')} has invalid join_type={join_type}")
                        if join_type == "left" and base_source not in scope:
                            errors.append(f"sheet {sheet.get('title')} left join must declare a real base_source")
                        if join_type != "left" and base_source not in (None, ""):
                            errors.append(f"sheet {sheet.get('title')} base_source is only valid for left join")
                        if not join_keys or not set(join_keys) <= set.intersection(*(sheet_columns[name] for name in scope if name in sheet_columns)):
                            errors.append(f"sheet {sheet.get('title')} join keys must exist on every source sheet")
                        join_evidence = [verified[item] for item in sheet_evidence if verified[item]["tool"] == "check_join_keys"]
                        checked_pairs = {
                            frozenset((item["arguments"].get("left_sheet"), item["arguments"].get("right_sheet")))
                            for item in join_evidence
                            if set(item["arguments"].get("key_fields", [])) == set(join_keys)
                        }
                        required_pairs = {
                            frozenset((scope[left], scope[right]))
                            for left in range(len(scope)) for right in range(left + 1, len(scope))
                        }
                        if "check_join_keys" not in cited_tools or not required_pairs <= checked_pairs:
                            errors.append(f"sheet {sheet.get('title')} join plan lacks check_join_keys evidence")
                        if strategy == "lookup_join" and any(not item.get("result", {}).get("safe_raw_join") for item in join_evidence):
                            errors.append(f"sheet {sheet.get('title')} lookup_join is unsafe at raw grain")
                    if strategy == "aggregate_then_join":
                        steps = join_plan.get("pre_aggregation", [])
                        covered_sources = {item.get("source_sheet") for item in steps if isinstance(item, dict)}
                        if covered_sources != set(scope):
                            errors.append(f"sheet {sheet.get('title')} must pre-aggregate every source before joining")
                    aliases = join_plan.get("field_aliases", [])
                    alias_bindings: list[dict[str, Any]] = []
                    for alias in aliases if isinstance(aliases, list) else []:
                        bindings = alias.get("bindings", []) if isinstance(alias, dict) else []
                        alias_bindings.extend(item for item in bindings if isinstance(item, dict))
                        bound_fields = {item.get("field") for item in bindings if isinstance(item, dict)}
                        bound_sheets = {item.get("source_sheet") for item in bindings if isinstance(item, dict)}
                        if len(bound_fields) > 1 and (not join_plan.get("requires_confirmation") or not clarifications):
                            errors.append(f"sheet {sheet.get('title')} field aliases require a blocking semantic confirmation")
                        for binding in bindings:
                            if binding.get("source_sheet") not in sheet_columns or binding.get("field") not in sheet_columns.get(binding.get("source_sheet"), set()):
                                errors.append(f"sheet {sheet.get('title')} has an invalid field alias binding")
                    if strategy == "append_aligned_sheets":
                        alias_field_sets = [
                            {item.get("field") for item in alias.get("bindings", []) if isinstance(item, dict)}
                            for alias in aliases if isinstance(alias, dict)
                        ] if isinstance(aliases, list) else []
                        for metric in metrics:
                            metric_fields = set(metric.get("source_fields", []))
                            alias_aggregation = metric.get("aggregation") in {"sum", "average", "min", "max", "count_nonempty"}
                            if alias_aggregation and len(metric_fields) > 1 and not any(metric_fields <= alias_fields for alias_fields in alias_field_sets):
                                errors.append(f"sheet {sheet.get('title')} multi-field metric {metric.get('name')} lacks one confirmed alias group")
                        for field in used:
                            supporting = {name for name in scope if field in sheet_columns.get(name, set())}
                            if supporting and supporting != set(scope):
                                covered = {item.get("source_sheet") for item in alias_bindings if item.get("field") == field}
                                if not covered:
                                    errors.append(f"sheet {sheet.get('title')} field {field} is missing on part of source_scope and has no alias plan")
                    if join_plan.get("requires_confirmation") and not clarifications:
                        errors.append(f"sheet {sheet.get('title')} join plan requires a blocking clarification")
            elif join_plan not in (None, {}):
                errors.append(f"sheet {sheet.get('title')} must not declare a join_plan for one source")

            scope_fields = set().union(*(sheet_columns.get(name, set()) for name in scope))
            bad_fields = sorted(used - fields)
            missing_from_scope = sorted(used - scope_fields)
            if bad_fields:
                errors.append(f"sheet {sheet.get('title')} has unknown fields={bad_fields}")
                continue
            if missing_from_scope:
                errors.append(f"sheet {sheet.get('title')} fields are outside source_scope={missing_from_scope}")
                continue
            covered_fields = set().union(*(_evidence_fields(verified[evidence_id], fields) for evidence_id in sheet_evidence))
            if used and not (used & covered_fields):
                errors.append(f"sheet {sheet.get('title')} cites evidence unrelated to its fields={sorted(used)}")
            signature = json.dumps({"grain": sheet.get("grain"), "source": scope, "dimensions": dimensions, "metrics": sorted((m.get("aggregation"), tuple(sorted(m.get("source_fields", [])))) for m in metrics), "join": join_plan, "selection": sheet.get("selection_logic")}, ensure_ascii=False, sort_keys=True)
            if signature in signatures:
                errors.append(f"package {package.get('title')} contains structurally duplicate sheet {sheet.get('title')}")
            signatures.add(signature)
    if errors:
        raise ValueError("; ".join(errors[:12]))


def _ensure_final_evidence(final: dict[str, Any], state: dict[str, Any], tools: SafeDataTools, turn: int) -> list[str]:
    existing = {
        json.dumps({"tool": item.get("tool"), "arguments": item.get("arguments", {})}, ensure_ascii=False, sort_keys=True)
        for item in state.get("evidence", [])
    }
    added: list[str] = []
    for package in final.get("workbook_packages", []):
        for sheet in package.get("sheets", []):
            scope = sheet.get("source_scope", [])
            plan = sheet.get("join_plan")
            if not isinstance(scope, list) or len(scope) < 2 or not isinstance(plan, dict) or plan.get("strategy") == "append_aligned_sheets":
                continue
            keys = plan.get("join_keys", [])
            if not isinstance(keys, list) or not keys:
                continue
            for left in range(len(scope)):
                for right in range(left + 1, len(scope)):
                    arguments = {"left_sheet": scope[left], "right_sheet": scope[right], "key_fields": keys}
                    signature = json.dumps({"tool": "check_join_keys", "arguments": arguments}, ensure_ascii=False, sort_keys=True)
                    reverse_arguments = {"left_sheet": scope[right], "right_sheet": scope[left], "key_fields": keys}
                    reverse_signature = json.dumps({"tool": "check_join_keys", "arguments": reverse_arguments}, ensure_ascii=False, sort_keys=True)
                    if signature in existing or reverse_signature in existing or len(state["evidence"]) >= MAX_TOOL_CALLS:
                        continue
                    try:
                        result = tools.check_join_keys(**arguments)
                    except Exception as exc:
                        result = {"error": str(exc)[:300]}
                    evidence_id = f"E{len(state['evidence']) + 1:03d}"
                    item = {
                        "evidence_id": evidence_id,
                        "status": "error" if "error" in result else "verified",
                        "tool": "check_join_keys",
                        "arguments": arguments,
                        "result": result,
                    }
                    state["evidence"].append(item)
                    existing.add(signature)
                    added.append(evidence_id)
                    state["event_log"].append({
                        "turn": turn,
                        "type": "validator_tool",
                        "sheet": sheet.get("title"),
                        "evidence_id": evidence_id,
                        "tool": "check_join_keys",
                    })
    return added


def _compact_context(state: dict[str, Any]) -> dict[str, Any]:
    catalog = [{"evidence_id": item["evidence_id"], "tool": item["tool"], "arguments": item["arguments"], "result_keys": list(item["result"].keys())} for item in state["evidence"]]
    phase = "finalize_or_repair" if state.get("turn", 0) >= 10 or len(state["evidence"]) >= 20 else "explore"
    context = {
        "user_goal": state["user_goal"], "precise_goal": state["precise_goal"],
        "phase": phase,
        "dataset_card": state["dataset_card"], "confirmed_long_term_memories": state["retrieved_memories"],
        "working_memory": state["working_memory"], "evidence_catalog": catalog,
        "recent_evidence": state["evidence"][-RECENT_EVIDENCE_WINDOW:],
        "last_validation_feedback": state.get("last_validation_feedback"),
        "last_tool_feedback": state.get("last_tool_feedback"),
        "tool_catalog": TOOL_CATALOG,
        "budgets": {"remaining_model_turns": MAX_MODEL_TURNS - state["turn"], "remaining_tool_calls": MAX_TOOL_CALLS - len(state["evidence"])},
    }
    if state.get("last_rejected_final") is not None:
        context["last_rejected_final"] = state["last_rejected_final"]
    return context


def run_agent(sid: str, dataset: dict[str, Any], goal: str, precise_goal: bool, api_key: str, model: str, output: Path, resume: bool = False) -> dict[str, Any]:
    agent_started = time.perf_counter()
    tools = SafeDataTools(sid, dataset)
    checkpoint = output / "short_term_checkpoint.json"
    resumed_final = None
    if resume and checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        state["dataset_card"] = tools.dataset_card()
        state.setdefault("last_rejected_final", None)
        latest_turn = int(state.get("turn", 0))
        attempts: list[tuple[int, Path]] = []
        for path in output.glob("turn_*_model.json"):
            match = re.search(r"turn_(\d+)_model", path.name)
            if match:
                attempt_turn = int(match.group(1))
                latest_turn = max(latest_turn, attempt_turn)
                attempts.append((attempt_turn, path))
        for attempt_turn, attempt_path in sorted(attempts, reverse=True):
            attempted = json.loads(attempt_path.read_text(encoding="utf-8")).get("final")
            if attempted:
                candidate = _normalize_final(attempted, state["evidence"], dataset)
                _ensure_final_evidence(candidate, state, tools, attempt_turn)
                candidate = _normalize_final(candidate, state["evidence"], dataset)
                try:
                    _validate_final(candidate, state["evidence"], dataset, precise_goal)
                except ValueError as exc:
                    if attempt_turn == latest_turn:
                        state["last_validation_feedback"] = str(exc)
                else:
                    resumed_final = candidate
                    (output / f"turn_{attempt_turn:02d}_normalized_final.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
                    break
        start_turn = latest_turn + 1
    else:
        state = {
            "session_id": sid, "user_goal": goal, "precise_goal": precise_goal,
            "dataset_card": tools.dataset_card(), "retrieved_memories": MemoryStore.retrieve(dataset),
            "working_memory": {"hypotheses": [], "open_questions": [], "design_notes": []},
            "evidence": [], "event_log": [], "turn": 0, "last_validation_feedback": None,
            "last_tool_feedback": None, "last_rejected_final": None,
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds"),
        }
        start_turn = 1
    seen_calls = {
        json.dumps({"tool": item["tool"], "arguments": item["arguments"]}, ensure_ascii=False, sort_keys=True): item
        for item in state["evidence"]
    }
    model_seconds = 0.0; final = resumed_final
    for turn in range(start_turn, MAX_MODEL_TURNS + 1):
        if final is not None:
            break
        state["turn"] = turn
        _emit_progress("modeling", f"Agent 正在进行第 {turn} 轮业务建模", agent_started)
        started = time.perf_counter()
        response, error = compose_ai._call_json({"api_key": api_key, "model": model, "base_url": "https://api.deepseek.com"}, AGENT_PROMPT, _compact_context(state))
        model_seconds += time.perf_counter() - started
        if not response:
            raise RuntimeError(error or "agent model returned no JSON")
        (output / f"turn_{turn:02d}_model.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        update = response.get("memory_update") or {}
        for key in ("hypotheses", "open_questions", "design_notes"):
            if isinstance(update.get(key), list): state["working_memory"][key] = [str(value)[:240] for value in update[key]][:10]
        if response.get("final") is not None:
            if len(state["evidence"]) < 2:
                raise RuntimeError("agent attempted to finish without sufficient tool evidence")
            candidate = _normalize_final(response["final"], state["evidence"], dataset)
            added_evidence = _ensure_final_evidence(candidate, state, tools, turn)
            if added_evidence:
                _emit_progress("verifying", "本地校验器正在补齐跨表关联安全证据", agent_started)
                for item in state["evidence"]:
                    if item["evidence_id"] in added_evidence:
                        signature = json.dumps({"tool": item["tool"], "arguments": item["arguments"]}, ensure_ascii=False, sort_keys=True)
                        seen_calls[signature] = item
                candidate = _normalize_final(candidate, state["evidence"], dataset)
            (output / f"turn_{turn:02d}_normalized_final.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                _validate_final(candidate, state["evidence"], dataset, precise_goal)
            except ValueError as exc:
                state["last_validation_feedback"] = str(exc)
                state["last_rejected_final"] = candidate
                state["event_log"].append({"turn": turn, "type": "final_rejected", "reason": str(exc)})
                _emit_progress("validating", "方案未通过安全校验，Agent 正在修正", agent_started)
                checkpoint.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
            final = candidate
            state["last_rejected_final"] = None
            state["event_log"].append({"turn": turn, "type": "final", "analysis_summary": response.get("analysis_summary", "")})
            _emit_progress("completed", "制表蓝图已通过结构和安全校验", agent_started)
            break
        calls = response.get("tool_calls") or []
        if not isinstance(calls, list) or not calls:
            raise RuntimeError("agent returned neither tools nor final")
        batch_evidence_ids = []
        tool_feedback = []
        for call in calls[:MAX_CALLS_PER_TURN]:
            if len(state["evidence"]) >= MAX_TOOL_CALLS:
                break
            tool = call.get("tool"); arguments = call.get("arguments") or {}
            signature = json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True)
            if signature in seen_calls:
                existing = seen_calls[signature]
                tool_feedback.append({
                    "tool": tool,
                    "status": "rejected",
                    "reason": "duplicate tool call; reuse the existing evidence below",
                    "existing_evidence_id": existing["evidence_id"],
                    "existing_status": existing["status"],
                    "existing_result": existing["result"],
                })
                continue
            _emit_progress("verifying", f"正在本地验证：{tool}", agent_started)
            try: result = tools.call(tool, arguments)
            except Exception as exc: result = {"error": str(exc)[:300]}
            evidence_id = f"E{len(state['evidence']) + 1:03d}"
            evidence_item = {"evidence_id": evidence_id, "status": "error" if "error" in result else "verified", "tool": tool, "arguments": arguments, "result": result}
            state["evidence"].append(evidence_item)
            seen_calls[signature] = evidence_item
            batch_evidence_ids.append(evidence_id)
            if "error" in result:
                tool_feedback.append({"tool": tool, "status": "error", "reason": result["error"]})
        state["last_tool_feedback"] = tool_feedback or None
        state["event_log"].append({"turn": turn, "type": "tool_batch", "analysis_summary": response.get("analysis_summary", ""), "evidence_ids": batch_evidence_ids, "tool_feedback": tool_feedback})
        (output / "short_term_checkpoint.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if final is None:
        raise RuntimeError("agent exhausted its turn budget without a valid blueprint")
    long_term_status = "not_applicable"
    if final.get("workbook_packages"):
        MemoryStore.propose(output / "long_term_memory_proposal.json", dataset, goal, final)
        long_term_status = "pending_user_confirmation"
    report = {"final": final, "evidence": state["evidence"], "working_memory": state["working_memory"], "event_log": state["event_log"], "memory": {"short_term_checkpoint": "short_term_checkpoint.json", "long_term_status": long_term_status, "retrieved_confirmed_memories": state["retrieved_memories"]}, "timing": {"model_seconds": round(model_seconds, 2), "frame_load_seconds": round(tools.load_seconds, 2), "tool_compute_seconds": round(tools.compute_seconds, 2)}}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", type=Path)
    source.add_argument("--session")
    parser.add_argument("--confirm-memory", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--goal", default="This table is messy. Create a valuable summary workbook.")
    parser.add_argument("--precise-goal", action="store_true")
    parser.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.confirm_memory:
        print(json.dumps(MemoryStore.confirm(args.confirm_memory), ensure_ascii=False, indent=2))
        return
    if not args.source and not args.session:
        parser.error("one of --source, --session or --confirm-memory is required")
    started = time.perf_counter()
    import_started = time.perf_counter()
    sid = legacy_demo.profile_source(args.source.resolve()) if args.source else args.session
    import_seconds = time.perf_counter() - import_started
    dataset = compose.read_manifest(sid)["dataset"]
    raw_name = args.name or (args.source.stem if args.source else sid)
    name = re.sub(r"[^0-9A-Za-z_-]+", "-", raw_name).strip("-") or "case"
    output = OUTPUT_ROOT / name; output.mkdir(parents=True, exist_ok=True)
    report = run_agent(sid, dataset, args.goal, args.precise_goal, args.api_key, args.model, output, args.resume)
    report["timing"]["import_seconds"] = round(import_seconds, 2)
    report["timing"]["total_seconds"] = round(time.perf_counter() - started, 2)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"session_id": sid, "packages": [item["title"] for item in report["final"]["workbook_packages"]], "tool_calls": len(report["evidence"]), "timing": report["timing"], "output": str(output / "report.json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

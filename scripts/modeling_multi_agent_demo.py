"""End-to-end harness for the isolated modeling IR architecture.

The model never receives the workbook or unrestricted rows. It explores a
local session through the existing bounded SafeDataTools, proposes strict IR,
and may repair compiler diagnostics. No production Compose endpoint imports
this module.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import modeling_agent_demo as legacy_agent
from scripts import modeling_arch_demo as legacy_demo
from scripts.modeling_multi_agent import (
    CompilationResult,
    CompilationStatus,
    DecisionLedger,
    Diagnostic,
    EvidenceStore,
    ModelingContext,
    MultiAgentOrchestrator,
    Operation,
    PlanCompiler,
    RiskLevel,
    Severity,
    ValueReview,
    WorkbookIntentIR,
    canonicalize_workbook_intent,
    parse_workbook_intent,
    workbook_intent_json_schema,
    workbook_intent_to_dict,
)
from scripts.modeling_multi_agent.executor import ExecutionError, PandasPlanExecutor
from scripts.modeling_multi_agent.result_audit import FullDataResultAuditor
from swiftgrid.backend.services import compose, compose_ai


OUTPUT_ROOT = ROOT / "artifacts" / "modeling-multi-agent-demo"
MAX_EXPLORATION_TURNS = 3
MAX_TOOL_CALLS = 20
MAX_CALLS_PER_TURN = 4


EVIDENCE_PROMPT = r"""
You are the evidence-planning stage of a general smart-table modeling agent.
You never receive the raw workbook. Inspect the supplied local dataset card
and verified tool results, then request only bounded local tools needed to
design a valuable, executable summary workbook.

Do not design the final workbook in this stage. Verify likely entity keys,
every plausible cross-sheet join, important numeric/date distributions, and
whether a proposed trend has more than one meaningful period. Prefer full-data
aggregate tools over masked row samples. Do not repeat a tool call.

Return JSON only:
{
  "summary": "what is understood and what remains uncertain",
  "tool_calls": [{"tool": "exact tool name", "arguments": {}}],
  "done": false
}
Set done=true with an empty tool_calls array only when the evidence is enough.
"""


MODELING_PROMPT = r"""
You are the ModelingAgent for a general smart-table product. Create a strict
WorkbookIntentIR from the local schema and verified evidence. Return the IR
root object only, with no markdown or wrapper.

Business objective:
- Recommend only workbooks/sheets that are materially useful; do not force a
  fixed count and do not duplicate the same grain and metrics.
- Use the important available source tables when they can be combined safely.
  Explain omissions in workbook metadata rather than inventing a join.
- A time trend is useful only when evidence proves multiple meaningful periods.
- Never invent external benchmarks, causes, targets, rates, formulas, business
  thresholds, or claims not computable from the source.

Deterministic safety contract:
- All source sheets and fields must exist. All evidence_refs must be verified
  evidence IDs from the context.
- A field marked values_withheld is still available to the local deterministic
  executor. You may bind a sensitive descriptive field such as a name to a
  local result dimension; the model never needs or receives its cell values.
- Every canonical dimension has explicit per-source bindings. Use date_bucket
  for day/week/month/quarter/year. Numeric and categorical business buckets
  must include a blocking clarification and required_decision_ids.
- Every required_decision_ids entry must be the exact id of a blocking
  clarification whose affected_node_ids contains the dependent node. Do not
  invent a separate DECIDE id for a CLARIFY node.
- COUNT_ROWS counts source records only. Business entities such as employees,
  users, customers, orders or assets require COUNT_DISTINCT with an entity_key
  retained in source_grain.
- VALUE requires verified uniqueness at source_grain and cannot be rolled to a
  broader/transformed grain. SUM/COUNT partials may roll up; averages must use
  weighted-average composition; distinct counts retain their key set.
- Do not raw-join fact tables. For actual/target/cost facts, bind each source to
  the same canonical dimensions, aggregate each metric independently, then let
  the compiler merge metric branches at the conformed grain.
- Joins are only for evidence-backed many-to-one/one-to-one enrichment.
- DERIVED metrics inherit the sheet grain, have no source fields/bindings, and
  use existing metric IDs as operands. Allowed operators are divide, subtract,
  add, multiply, add_constant, subtract_constant, multiply_constant and
  divide_constant. Constants must be explicit in expression.parameters.
- RATIO_OF_SUMS is a non-additive divide of exactly two additive SUM_PARTIALS
  metrics. Never average row-level ratios to obtain an aggregate ratio.
- Questions are blocking only when the answer changes a calculation currently
  included in the IR. Optional ideas must not block an executable core plan.
- Every sheet must provide order_by and limit explicitly. If its title or
  purpose promises a rank, top/bottom list, priority list or first N entities,
  encode that promise with deterministic order_by column IDs and limit. A sort
  column may be an output dimension ID or metric ID. Do not
  claim selection that the IR does not execute.
- Every metric requires a precise business_definition, unit and risk_level.
  Count metrics use unit=count. Derived metrics cannot be low risk and require
  explicit units for every operand and result.
- Treat time dimensions semantically. A trend requires the metric to occur on
  the selected event/measurement date. Grouping current cumulative or snapshot
  values by registration, onboarding or other cohort-entry dates is a cohort
  comparison, not change over time; name and describe it accordingly.
- Self-review the plan for grain, composability, source coverage and business
  value before returning it.
"""


REPAIR_PROMPT = r"""
You are the conditional RepairAgent. Revise the supplied WorkbookIntentIR only
to resolve deterministic compiler diagnostics while preserving valid business
intent. Do not override local evidence, remove a question that is genuinely
needed, or invent new evidence IDs. You may redesign or remove an unsafe metric
or sheet. Return the complete corrected IR root object only.

Apply every diagnostic, not only the first one. Mechanical contracts:
- count_rows: source_fields=[] and entity_key=[]; it still uses source_grain.
- count_distinct: source_fields=[]; entity_key is non-empty and retained in
  source_grain.
- average: semantic=non_additive_measure, additivity=non_additive and
  composition=weighted_average.
- derived: no source sheet/fields/grain/bindings/entity key; operands exactly
  equal dependencies and only supported operators may be used.
- A rank/top/bottom/priority claim requires explicit order_by and, when a fixed
  count is claimed, limit. Remove the claim if it cannot be represented.
- Repair business_definition, unit and risk_level diagnostics without changing
  the verified calculation. Never hide a dimensional mismatch by renaming a
  unit; redesign or remove the invalid derived metric.
- MISSING_BLOCKING_QUESTION means required_decision_ids must be relinked to the
  exact blocking clarification id that names the affected node.
- Never change a verified fact. If a design conflicts with evidence, redesign
  or remove it and let the deterministic compiler re-check the complete IR.
"""


VALUE_REVIEW_PROMPT = r"""
You are the independent SemanticAndValueAuditAgent for a general smart-table
product. Audit the proposed IR against the user goal, source structure and
verified local evidence. Do not change the plan and do not trust titles or
purposes as proof that a calculation exists.

Semantic hard gate:
- Inspect every derived metric. Translate its operator and operands literally,
  then decide whether that formula matches the metric name and stated purpose.
  A formula that is executable but means something else is rejected. If the
  desired formula needs multiple operations, require explicit intermediate
  derived metrics instead of silently accepting an approximation.
- Inspect every sheet purpose. Claims such as top/bottom N, ranking, priority,
  filtering, segmentation, exception detection or privacy suppression must be
  represented by explicit executable IR fields. Text alone is not evidence.
- Audit temporal claims. A real trend groups measures by the date on which those
  measures occurred. Current cumulative/snapshot measures grouped by a customer's
  registration date describe cohorts, not business change over time. Require a
  cohort/distribution label or reject the trend claim. Do not reject genuine
  event trends such as purchase value grouped by purchase date.
- Reject unresolved assumptions that change a currently included calculation.
  They require a blocking clarification and a regenerated plan after the user
  answers. Optional future ideas may remain non-blocking.
- Evidence exploration may hide values or suppress small groups from the model.
  That does not suppress the final local workbook. Never claim final-output
  privacy filtering unless an executable IR operation actually implements it.

Value gate:
Reject repetitive sheets, unsupported trends, unjustified omission of important
usable sources, mere field copying, opaque detail lists when a safe descriptive
lookup is available, and raw source-row volume presented as a business KPI.

Score goal fit, data use, sheet complementarity, usability, decision support
and scope restraint from 0 to 2. Accept only when every score is at least 1,
the total is at least 10, usability and decision support are both 2, and the
  semantic hard gate passes. Cover every derived or high-risk metric ID and every sheet ID
exactly once in the structured checks.

Return JSON only:
{
  "accepted": false,
  "scores": {
    "goal_fit": 2,
    "data_use": 2,
    "complementarity": 1,
    "usability": 1,
    "decision_support": 1,
    "scope_restraint": 2
  },
  "metric_checks": [
    {
      "metric_id": "exact derived or high-risk metric id",
      "formula_as_written": "literal symbolic formula",
      "name_alignment": "why it does or does not match",
      "supported": false
    }
  ],
  "sheet_checks": [
    {
      "sheet_id": "exact sheet id",
      "claim": "strongest business claim in title/purpose",
      "executable_support": "specific dimensions, metrics, order and limit",
      "supported": false
    }
  ],
  "findings": ["specific correction tied to an exact node id"]
}
"""


VALUE_REVISION_PROMPT = r"""
You are the ModelingAgent's conditional value-revision pass. Improve the
complete WorkbookIntentIR using the review findings while preserving every
compiler-valid safety property. Return the complete revised IR root only.

Improve usability and decision value with existing verified fields and safe
many-to-one lookups. Do not invent thresholds, formulas, joins or evidence. If
a high-value extension requires unknown business cutoffs, add a concise
non-blocking clarification without including an unconfirmed calculation in the
current executable plan. Do not add sheets merely to increase the count.
Fields whose values are withheld from the model remain usable by the local
executor as dimensions; never ask for or expose their cell values.
Resolve every semantic audit finding. Use explicit intermediate derived metrics
for multi-step formulas. Encode ranking and Top-N claims with order_by and limit,
or remove the unsupported claim. Return all sheet order_by and limit fields.
"""


DECISION_RESOLUTION_PROMPT = r"""
You are the DecisionResolutionAgent. Apply the user's confirmed clarification
answers to the complete WorkbookIntentIR and return the complete revised IR root
only. This is not permission to reinterpret the answer.

Rules:
- Implement each selected_option literally. If it confirms the current
  calculation, preserve it. If it requests removal or a different grain/rule,
  modify or remove the affected nodes accordingly.
- Preserve answered clarification nodes as an audit trail. Any remaining
  required_decision_ids must exactly reference their clarification ids.
- Do not invent evidence, formulas, joins, thresholds or user decisions.
- A business weighting choice must be represented by an explicit executable
  weight field and weighted operation. composition=weighted_average only
  preserves an ordinary average through sum/count rollup; it does not encode a
  user-selected business weight. If the current IR cannot represent the exact
  choice, do not claim that metadata or business_definition implements it.
- For a confirmed minimum-group privacy choice, set both
  sheet.minimum_group_size and sheet.group_count_metric_id. Notes, purposes and
  required_decision_ids do not hide rows by themselves.
- Preserve all valid metric contracts, units, risk levels and deterministic
  execution fields. Return every order_by and limit field.
"""


DECISION_COMPLIANCE_PROMPT = r"""
You are an independent DecisionComplianceAuditAgent. Compare the exact
confirmed user options with the intent before and after decision resolution.
Do not judge whether an option is desirable. Verify whether it was implemented
literally.

Mark implemented=false when an option was omitted, approximated, replaced by a
different option, or cannot be represented by the revised executable IR. A safe
fallback is still non-compliant unless it is the selected option. Cover every
confirmed decision id exactly once.

Return JSON only:
{
  "checks": [
    {
      "decision_id": "exact id",
      "selected_option": "exact option",
      "implementation": "specific revised nodes or explicit unsupported gap",
      "implemented": false
    }
  ]
}
"""


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _emit(stage: str, message: str, started: float) -> None:
    print(json.dumps({
        "stage": stage,
        "message": message,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }, ensure_ascii=False), flush=True)


class ModelGateway:
    def __init__(self, api_key: str, model: str, output: Path) -> None:
        self.config = {
            "api_key": api_key,
            "model": model,
            "base_url": "https://api.deepseek.com",
        }
        self.model = model
        self.output = output
        self.calls = 0
        self.seconds = 0.0

    def call(self, stage: str, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        started = time.perf_counter()
        response, error = compose_ai._call_json(self.config, system, payload)
        self.seconds += time.perf_counter() - started
        record = {
            "stage": stage,
            "payload": payload,
            "response": response,
            "error": error,
        }
        _write_json(self.output / f"model_{self.calls:02d}_{stage}.json", record)
        if not response:
            raise RuntimeError(error or f"{stage} returned no JSON")
        return response


class EvidenceExplorer:
    def __init__(
        self,
        gateway: ModelGateway,
        tools: legacy_agent.SafeDataTools,
        dataset_card: dict[str, Any],
        output: Path,
        started: float,
    ) -> None:
        self.gateway = gateway
        self.tools = tools
        self.dataset_card = dataset_card
        self.output = output
        self.started = started
        self.evidence: list[dict[str, Any]] = []
        self.seen: dict[str, dict[str, Any]] = {}

    def collect(self) -> list[dict[str, Any]]:
        self._add_verified("dataset_card", {}, self.dataset_card)
        for sheet in self.dataset_card.get("sheets", []):
            columns = list(sheet.get("columns", []))
            for offset in range(0, len(columns), 8):
                self._run_tool("profile_columns", {
                    "source_sheet": sheet["name"],
                    "fields": columns[offset:offset + 8],
                })

        for turn in range(1, MAX_EXPLORATION_TURNS + 1):
            _emit("evidence", f"证据规划第 {turn} 轮", self.started)
            response = self.gateway.call("evidence", EVIDENCE_PROMPT, {
                "dataset_card": self.dataset_card,
                "tool_catalog": legacy_agent.TOOL_CATALOG,
                "verified_evidence": self.evidence,
                "limits": {
                    "remaining_calls": MAX_TOOL_CALLS - len(self.evidence) + 1,
                    "max_calls_this_turn": MAX_CALLS_PER_TURN,
                },
            })
            calls = response.get("tool_calls") or []
            if not isinstance(calls, list):
                raise RuntimeError("evidence planner tool_calls must be an array")
            added = 0
            for call in calls[:MAX_CALLS_PER_TURN]:
                if len(self.evidence) - 1 >= MAX_TOOL_CALLS:
                    break
                tool = str(call.get("tool") or "")
                arguments = call.get("arguments") or {}
                if self._run_tool(tool, arguments):
                    added += 1
            if response.get("done") is True and not calls:
                break
            if not calls or not added:
                break
        _write_json(self.output / "evidence.json", self.evidence)
        return self.evidence

    def _signature(self, tool: str, arguments: dict[str, Any]) -> str:
        return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True)

    def _add_verified(self, tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> bool:
        signature = self._signature(tool, arguments)
        if signature in self.seen:
            return False
        item = {
            "evidence_id": f"E{len(self.evidence):03d}",
            "status": "verified",
            "tool": tool,
            "arguments": arguments,
            "result": result,
        }
        self.evidence.append(item)
        self.seen[signature] = item
        return True

    def _run_tool(self, tool: str, arguments: dict[str, Any]) -> bool:
        signature = self._signature(tool, arguments)
        if signature in self.seen:
            return False
        _emit("evidence", f"本地验证：{tool}", self.started)
        try:
            result = self.tools.call(tool, arguments)
            status = "error" if "error" in result else "verified"
        except Exception as exc:
            result = {"error": str(exc)[:300]}
            status = "error"
        item = {
            "evidence_id": f"E{len(self.evidence):03d}",
            "status": status,
            "tool": tool,
            "arguments": arguments,
            "result": result,
        }
        self.evidence.append(item)
        self.seen[signature] = item
        return True


class DeepSeekModelingAgent:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def propose(self, context: ModelingContext) -> WorkbookIntentIR:
        payload = {
            "user_goal": context.user_goal,
            "dataset_card": context.dataset_card,
            "verified_evidence": list(context.evidence_catalog),
            "confirmed_decisions": context.confirmed_decisions,
            "recalled_memory": list(context.recalled_memory),
            "json_schema": workbook_intent_json_schema(),
        }
        response = self.gateway.call("modeling", MODELING_PROMPT, payload)
        return self._parse_or_retry(response, payload)

    def _parse_or_retry(
        self,
        response: dict[str, Any],
        original_payload: dict[str, Any],
    ) -> WorkbookIntentIR:
        candidate = response.get("workbook_intent") or response.get("workbook") or response
        try:
            return parse_workbook_intent(candidate)
        except Exception as exc:
            retry = self.gateway.call("modeling_format_repair", REPAIR_PROMPT, {
                **original_payload,
                "invalid_response": candidate,
                "parser_error": str(exc),
                "instruction": "Repair JSON shape only; return the complete IR.",
            })
            retry_candidate = retry.get("workbook_intent") or retry.get("workbook") or retry
            return parse_workbook_intent(retry_candidate)


class DeepSeekRepairAgent:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def repair(
        self,
        intent: WorkbookIntentIR,
        diagnostics: tuple[Diagnostic, ...],
        context: ModelingContext,
    ) -> WorkbookIntentIR:
        response = self.gateway.call("repair", REPAIR_PROMPT, {
            "user_goal": context.user_goal,
            "dataset_card": context.dataset_card,
            "evidence_catalog": _repair_evidence(context.evidence_catalog),
            "current_intent": workbook_intent_to_dict(intent),
            "compiler_diagnostics": [asdict(item) for item in diagnostics],
            "json_schema": workbook_intent_json_schema(),
        })
        candidate = response.get("workbook_intent") or response.get("workbook") or response
        return parse_workbook_intent(candidate)


class DeepSeekValueReviewAgent:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def review(self, intent: WorkbookIntentIR, context: ModelingContext) -> ValueReview:
        response = self.gateway.call("value_review", VALUE_REVIEW_PROMPT, {
            "user_goal": context.user_goal,
            "dataset_card": context.dataset_card,
            "verified_evidence": list(context.evidence_catalog),
            "workbook_intent": workbook_intent_to_dict(intent),
        })
        findings = response.get("findings") or []
        if not isinstance(findings, list):
            findings = [str(findings)]
        expected = {
            "goal_fit", "data_use", "complementarity",
            "usability", "decision_support", "scope_restraint",
        }
        raw_scores = response.get("scores") or {}
        scores = {
            key: int(value)
            for key, value in raw_scores.items()
            if key in expected and isinstance(value, (int, float)) and 0 <= value <= 2
        }
        score_gate = (
            set(scores) == expected
            and all(value >= 1 for value in scores.values())
            and sum(scores.values()) >= 10
            and scores["usability"] == 2
            and scores["decision_support"] == 2
        )
        audited_metric_ids = {
            metric.id
            for sheet in intent.sheets
            for metric in sheet.metrics
            if metric.operation == Operation.DERIVED or metric.risk_level == RiskLevel.HIGH
        }
        sheet_ids = {sheet.id for sheet in intent.sheets}
        metric_checks = self._normalize_checks(
            response.get("metric_checks"), "metric_id", audited_metric_ids
        )
        sheet_checks = self._normalize_checks(
            response.get("sheet_checks"), "sheet_id", sheet_ids
        )
        semantic_gate = (
            {item["metric_id"] for item in metric_checks} == audited_metric_ids
            and {item["sheet_id"] for item in sheet_checks} == sheet_ids
            and all(item.get("supported") is True for item in metric_checks)
            and all(item.get("supported") is True for item in sheet_checks)
        )
        for item in metric_checks:
            if item.get("supported") is not True:
                findings.append(
                    f"指标 {item.get('metric_id')} 语义审查未通过：{item.get('name_alignment', '')}"
                )
        for item in sheet_checks:
            if item.get("supported") is not True:
                findings.append(
                    f"工作表 {item.get('sheet_id')} 承诺未被执行计划支持：{item.get('executable_support', '')}"
                )
        if not score_gate:
            findings.append("价值评分未达到接受门槛：总分需至少10，且可用性和决策支持均须为2。")
        if not semantic_gate:
            findings.append("结构化语义审查未覆盖全部派生指标和工作表，或存在未通过项。")
        return ValueReview(
            accepted=response.get("accepted") is True and score_gate and semantic_gate,
            findings=tuple(str(item)[:500] for item in findings[:12]),
            scores=scores,
            audit={
                "metric_checks": metric_checks,
                "sheet_checks": sheet_checks,
                "semantic_gate": semantic_gate,
            },
        )

    @staticmethod
    def _normalize_checks(raw: Any, id_field: str, expected_ids: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        checks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            node_id = item.get(id_field)
            if not isinstance(node_id, str) or node_id not in expected_ids or node_id in seen:
                continue
            seen.add(node_id)
            checks.append(dict(item))
        return checks


class DeepSeekValueRevisionAgent:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def revise(
        self,
        intent: WorkbookIntentIR,
        review: ValueReview,
        context: ModelingContext,
    ) -> WorkbookIntentIR:
        response = self.gateway.call("value_revision", VALUE_REVISION_PROMPT, {
            "user_goal": context.user_goal,
            "dataset_card": context.dataset_card,
            "verified_evidence": list(context.evidence_catalog),
            "current_intent": workbook_intent_to_dict(intent),
            "review_findings": list(review.findings),
            "review_audit": review.audit,
            "json_schema": workbook_intent_json_schema(),
        })
        candidate = response.get("workbook_intent") or response.get("workbook") or response
        return parse_workbook_intent(candidate)


class DeepSeekDecisionResolutionAgent:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def resolve(
        self,
        intent: WorkbookIntentIR,
        context: ModelingContext,
    ) -> WorkbookIntentIR:
        response = self.gateway.call("decision_resolution", DECISION_RESOLUTION_PROMPT, {
            "user_goal": context.user_goal,
            "dataset_card": context.dataset_card,
            "verified_evidence": list(context.evidence_catalog),
            "current_intent": workbook_intent_to_dict(intent),
            "confirmed_decisions": context.confirmed_decisions,
            "json_schema": workbook_intent_json_schema(),
        })
        candidate = response.get("workbook_intent") or response.get("workbook") or response
        return parse_workbook_intent(candidate)


def _decision_execution_findings(
    before: WorkbookIntentIR,
    after: WorkbookIntentIR,
    context: ModelingContext,
) -> list[dict[str, str]]:
    """Reject decision claims that the current executable IR cannot represent."""
    questions = {item.id: item for item in before.clarifications}
    metrics = {
        metric.id: metric
        for sheet in after.sheets
        for metric in sheet.metrics
    }
    findings: list[dict[str, str]] = []
    for decision_id, value in context.confirmed_decisions.items():
        selected = value.get("selected_option") if isinstance(value, dict) else value
        selected_text = str(selected or "").strip()
        question = questions.get(decision_id)
        if question and question.options and selected_text not in question.options:
            findings.append({
                "decision_id": decision_id,
                "code": "OPTION_NOT_OFFERED",
                "message": "confirmed option is not an exact option from the clarification",
            })
            continue

        # Composition.WEIGHTED_AVERAGE is an internal sum/count rollup contract,
        # not a business weighting instruction. The current IR has no explicit
        # weight-field contract, so a weighted user choice must fail closed.
        if re.search(r"加权|weighted", selected_text, flags=re.IGNORECASE):
            affected_metrics = [
                metrics[node_id]
                for node_id in (question.affected_node_ids if question else ())
                if node_id in metrics
            ]
            if not affected_metrics or any(
                getattr(metric, "weight_field", None) is None
                or metric.operation.value != "weighted_average"
                for metric in affected_metrics
            ):
                findings.append({
                    "decision_id": decision_id,
                    "code": "WEIGHT_CONTRACT_NOT_EXECUTABLE",
                    "message": (
                        "selected weighted calculation lacks a first-class executable "
                        "weight field and weighted operation"
                    ),
                })
        if re.search(r"不显示|隐藏|隐私抑制|hide|suppress", selected_text, flags=re.IGNORECASE):
            affected_ids = set(question.affected_node_ids if question else ())
            affected_sheets = [
                sheet for sheet in after.sheets
                if sheet.id in affected_ids
                or any(metric.id in affected_ids for metric in sheet.metrics)
                or any(dimension.id in affected_ids for dimension in sheet.dimensions)
            ]
            if not affected_sheets or any(
                sheet.minimum_group_size is None or not sheet.group_count_metric_id
                for sheet in affected_sheets
            ):
                findings.append({
                    "decision_id": decision_id,
                    "code": "ROW_SUPPRESSION_NOT_EXECUTABLE",
                    "message": (
                        "selected hidden/suppressed output lacks executable "
                        "minimum_group_size and group_count_metric_id"
                    ),
                })
    return findings


class DeepSeekDecisionComplianceAgent:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def review(
        self,
        before: WorkbookIntentIR,
        after: WorkbookIntentIR,
        context: ModelingContext,
    ) -> dict[str, Any]:
        response = self.gateway.call("decision_compliance", DECISION_COMPLIANCE_PROMPT, {
            "user_goal": context.user_goal,
            "confirmed_decisions": context.confirmed_decisions,
            "intent_before": workbook_intent_to_dict(before),
            "intent_after": workbook_intent_to_dict(after),
        })
        raw = response.get("checks")
        expected = set(context.confirmed_decisions)
        expected_options = {
            decision_id: (
                value.get("selected_option") if isinstance(value, dict) else value
            )
            for decision_id, value in context.confirmed_decisions.items()
        }
        checks: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                decision_id = item.get("decision_id")
                if decision_id not in expected or decision_id in seen:
                    continue
                seen.add(decision_id)
                normalized = dict(item)
                if normalized.get("selected_option") != expected_options[decision_id]:
                    normalized["implemented"] = False
                    normalized["implementation"] = (
                        str(normalized.get("implementation") or "")
                        + "; compliance response did not echo the exact confirmed option"
                    ).lstrip("; ")
                checks.append(normalized)
        deterministic_findings = _decision_execution_findings(before, after, context)
        for finding in deterministic_findings:
            for item in checks:
                if item.get("decision_id") == finding["decision_id"]:
                    item["implemented"] = False
                    item["implementation"] = (
                        str(item.get("implementation") or "")
                        + "; " + finding["message"]
                    ).lstrip("; ")
        passed = (
            seen == expected
            and not deterministic_findings
            and all(item.get("implemented") is True for item in checks)
        )
        return {
            "passed": passed,
            "checks": checks,
            "deterministic_findings": deterministic_findings,
        }


class ReusedModelingAgent:
    def __init__(self, intent: WorkbookIntentIR) -> None:
        self.intent = intent

    def propose(self, _context: ModelingContext) -> WorkbookIntentIR:
        return self.intent


def _repair_evidence(records) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in records:
        result = item.get("result") or {}
        if item.get("tool") in {"check_key_uniqueness", "check_join_keys"}:
            summary = result
        else:
            summary = {"result_keys": sorted(result)} if isinstance(result, dict) else {}
        compact.append({
            "evidence_id": item.get("evidence_id"),
            "tool": item.get("tool"),
            "status": item.get("status"),
            "arguments": item.get("arguments"),
            "summary": summary,
        })
    return compact


def _plan_to_dict(result) -> dict[str, Any] | None:
    if not result.plan:
        return None
    return {
        "workbook_id": result.plan.workbook_id,
        "nodes": [asdict(item) for item in result.plan.nodes],
        "sheet_outputs": result.plan.sheet_outputs,
    }


def _source_frames(tools: legacy_agent.SafeDataTools, dataset_card: dict[str, Any]) -> dict[str, pd.DataFrame]:
    combined = tools._load()
    frames: dict[str, pd.DataFrame] = {}
    for sheet in dataset_card.get("sheets", []):
        name = str(sheet["name"])
        columns = [str(item) for item in sheet.get("columns", [])]
        frames[name] = combined.loc[
            combined["__source_sheet__"].eq(name),
            columns,
        ].copy(deep=True).reset_index(drop=True)
    return frames


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy(deep=True)
    numeric = output.select_dtypes(include="number").columns
    if len(numeric):
        output.loc[:, numeric] = output.loc[:, numeric].round(2)
    return output


def _excel_sheet_name(title: str, used: set[str]) -> str:
    base = re.sub(r'[<>:"/\\|?*]+', "_", title).strip()[:31] or "结果"
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = f"{base[:31-len(suffix)]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def _execute_and_write(
    plan,
    intent: WorkbookIntentIR,
    tools: legacy_agent.SafeDataTools,
    dataset_card: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    sources = _source_frames(tools, dataset_card)
    execution = PandasPlanExecutor().execute(plan, sources)
    result_audit = FullDataResultAuditor().audit(plan, intent, execution, sources)
    if not result_audit.passed:
        return {
            "status": "failed",
            "error": {
                "code": "RESULT_AUDIT_FAILED",
                "message": "full-data reverse calculation failed",
            },
            "result_audit": asdict(result_audit),
        }
    titles = {item.id: item.title for item in intent.sheets}
    previews: dict[str, Any] = {}
    used: set[str] = set()
    workbook_path = output / "result.xlsx"
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        for sheet_id, frame in execution.sheets.items():
            display = _display_frame(frame)
            title = titles.get(sheet_id, sheet_id)
            display.to_excel(writer, sheet_name=_excel_sheet_name(title, used), index=False)
            previews[sheet_id] = {
                "title": title,
                "rows": len(display),
                "columns": [str(item) for item in display.columns],
                "preview": json.loads(display.head(20).to_json(
                    orient="records",
                    date_format="iso",
                    force_ascii=False,
                )),
                "provenance": execution.provenance[sheet_id],
            }
    return {
        "status": "completed",
        "workbook": str(workbook_path.resolve()),
        "sheets": previews,
        "audit": [asdict(item) for item in execution.audit],
        "result_audit": asdict(result_audit),
    }


def _resolve_comparison_questions(
    intent: WorkbookIntentIR,
    compilation,
    context: ModelingContext,
    *,
    enabled: bool,
    store: EvidenceStore,
    ledger: DecisionLedger,
    simulated_decisions: dict[str, Any],
    decision_agent: DeepSeekDecisionResolutionAgent,
    compliance_agent: DeepSeekDecisionComplianceAgent,
    compliance_history: list[dict[str, Any]],
    repair_agent: DeepSeekRepairAgent,
    max_repairs: int,
    stages: list[str],
) -> tuple[WorkbookIntentIR, Any, ModelingContext, int]:
    repairs = 0
    rounds = 0
    active_context = context
    while enabled and compilation.status == CompilationStatus.BLOCKED and rounds < 2:
        unanswered = [
            item for item in intent.clarifications
            if item.blocking and not ledger.is_confirmed(item.id)
        ]
        if not unanswered:
            break
        for question in unanswered:
            selected = _select_conservative_option(question)
            value = {
                "selected_option": selected,
                "simulated": True,
                "selection_policy": "conservative_lowest_assumption",
            }
            ledger.confirm(question.id, value, source="comparison_harness")
            simulated_decisions[question.id] = value
        active_context = ModelingContext(
            user_goal=context.user_goal,
            dataset_card=context.dataset_card,
            evidence_catalog=context.evidence_catalog,
            confirmed_decisions=dict(simulated_decisions),
            short_term_summary=context.short_term_summary,
            recalled_memory=context.recalled_memory,
        )
        before_resolution = intent
        intent = canonicalize_workbook_intent(decision_agent.resolve(intent, active_context))
        stages.append("decision_resolution")
        compliance = compliance_agent.review(before_resolution, intent, active_context)
        compliance_history.append(compliance)
        stages.append("decision_compliance_review")
        if not compliance["passed"]:
            compilation = CompilationResult(
                CompilationStatus.BLOCKED,
                None,
                (
                    Diagnostic(
                        "DECISION_NOT_IMPLEMENTED",
                        "confirmed user option was not implemented literally; execution is blocked",
                        "decision_compliance",
                        Severity.BLOCKING,
                        False,
                    ),
                ),
            )
            break
        compilation = PlanCompiler(store, ledger).compile(intent)
        while compilation.status == CompilationStatus.INVALID and repairs < max_repairs:
            intent = canonicalize_workbook_intent(
                repair_agent.repair(intent, compilation.diagnostics, active_context)
            )
            repairs += 1
            compilation = PlanCompiler(store, ledger).compile(intent)
            stages.extend(("repairing_decision_resolution", "recompiling_decision_resolution"))
        rounds += 1
    return intent, compilation, active_context, repairs


def _select_conservative_option(question) -> str:
    if not question.options:
        return "confirmed"
    low_assumption_terms = (
        "保持", "维持", "简单", "移除", "不纳入", "暂不", "仅使用",
        "不细分", "不显示", "接受", "原口径", "as is", "simple",
        "remove", "exclude", "keep",
    )
    assumption_terms = (
        "加权", "分摊", "放宽", "结合", "新增", "推算", "估算",
        "weighted", "allocate", "estimate", "combine",
    )

    def score(option: str) -> int:
        lowered = option.lower()
        return (
            3 * sum(term.lower() in lowered for term in low_assumption_terms)
            - 4 * sum(term.lower() in lowered for term in assumption_terms)
        )

    return max(question.options, key=score)


def run_case(
    source: Path | None,
    name: str,
    goal: str,
    api_key: str,
    model: str,
    max_repairs: int,
    confirm_all: bool,
    resume_report: Path | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    output = OUTPUT_ROOT / name
    output.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] | None = None
    initial_intent: WorkbookIntentIR | None = None
    if resume_report:
        previous = json.loads(resume_report.read_text(encoding="utf-8"))
        source = Path(previous["source"])
        sid = str(previous["session_id"])
        evidence = list(previous["evidence"])
        initial_intent = parse_workbook_intent(previous["intent"])
        _emit("resuming", "复用上一轮本地证据和 IR，仅重跑诊断修复", started)
    else:
        if source is None:
            raise ValueError("source is required for a new run")
        _emit("importing", "正在本地读取并画像源表", started)
        sid = legacy_demo.profile_source(source.resolve())
    manifest = compose.read_manifest(sid)
    dataset = manifest["dataset"]
    tools = legacy_agent.SafeDataTools(sid, dataset)
    dataset_card = tools.dataset_card()
    gateway = ModelGateway(api_key, model, output)
    if previous is None:
        explorer = EvidenceExplorer(gateway, tools, dataset_card, output, started)
        evidence = explorer.collect()
    else:
        _write_json(output / "evidence.json", evidence)
    store = EvidenceStore.from_legacy_safe_tools(dataset_card, evidence)
    context = ModelingContext(
        user_goal=goal,
        dataset_card=dataset_card,
        evidence_catalog=tuple(evidence),
        confirmed_decisions={},
        short_term_summary={},
        recalled_memory=(),
    )
    _emit("modeling", "ModelingAgent 正在生成结构化制表意图", started)
    modeling_agent = ReusedModelingAgent(initial_intent) if initial_intent else DeepSeekModelingAgent(gateway)
    repair_agent = DeepSeekRepairAgent(gateway)
    review_agent = DeepSeekValueReviewAgent(gateway)
    revision_agent = DeepSeekValueRevisionAgent(gateway)
    decision_agent = DeepSeekDecisionResolutionAgent(gateway)
    compliance_agent = DeepSeekDecisionComplianceAgent(gateway)
    orchestrator = MultiAgentOrchestrator(
        modeling_agent,
        PlanCompiler(store),
        repair_agent=repair_agent,
        value_review_agent=review_agent,
        should_review_value=lambda _intent, _context: True,
        max_repairs=max_repairs,
    )
    result = orchestrator.run(context)
    intent = result.intent
    compilation = result.compilation
    review = result.value_review
    stages = list(result.stages)
    total_repairs = result.repair_attempts
    simulated_decisions: dict[str, Any] = {}
    compliance_history: list[dict[str, Any]] = []
    ledger = DecisionLedger()
    if compilation.status == CompilationStatus.BLOCKED and confirm_all:
        intent, compilation, context, decision_repairs = _resolve_comparison_questions(
            intent,
            compilation,
            context,
            enabled=confirm_all,
            store=store,
            ledger=ledger,
            simulated_decisions=simulated_decisions,
            decision_agent=decision_agent,
            compliance_agent=compliance_agent,
            compliance_history=compliance_history,
            repair_agent=repair_agent,
            max_repairs=max_repairs,
            stages=stages,
        )
        total_repairs += decision_repairs
        if compilation.status == CompilationStatus.READY:
            review = review_agent.review(intent, context)
            stages.append("conditional_value_review")
    if compilation.status == CompilationStatus.READY and review and not review.accepted:
        _emit("value_revision", "价值复核未通过，ModelingAgent 正在定向改进方案", started)
        intent = canonicalize_workbook_intent(
            revision_agent.revise(intent, review, context)
        )
        stages.append("conditional_value_revision")
        compilation = PlanCompiler(store, ledger).compile(intent)
        revision_repairs = 0
        while compilation.status == CompilationStatus.INVALID and revision_repairs < max_repairs:
            intent = canonicalize_workbook_intent(
                repair_agent.repair(intent, compilation.diagnostics, context)
            )
            revision_repairs += 1
            compilation = PlanCompiler(store, ledger).compile(intent)
            stages.extend(("repairing_value_revision", "recompiling_value_revision"))
        total_repairs += revision_repairs
        if compilation.status == CompilationStatus.BLOCKED:
            intent, compilation, context, decision_repairs = _resolve_comparison_questions(
                intent,
                compilation,
                context,
                enabled=confirm_all,
                store=store,
                ledger=ledger,
                simulated_decisions=simulated_decisions,
                decision_agent=decision_agent,
                compliance_agent=compliance_agent,
                compliance_history=compliance_history,
                repair_agent=repair_agent,
                max_repairs=max_repairs,
                stages=stages,
            )
            total_repairs += decision_repairs
        if compilation.status == CompilationStatus.READY:
            review = review_agent.review(intent, context)
            stages.append("conditional_value_rereview")
    if (
        compilation.status == CompilationStatus.READY
        and review
        and review.audit.get("semantic_gate") is not True
    ):
        _emit(
            "semantic_revision",
            "语义硬门未通过，ModelingAgent 正在进行最后一次安全修订",
            started,
        )
        intent = canonicalize_workbook_intent(
            revision_agent.revise(intent, review, context)
        )
        stages.append("bounded_semantic_revision")
        compilation = PlanCompiler(store, ledger).compile(intent)
        semantic_repairs = 0
        while compilation.status == CompilationStatus.INVALID and semantic_repairs < max_repairs:
            intent = canonicalize_workbook_intent(
                repair_agent.repair(intent, compilation.diagnostics, context)
            )
            semantic_repairs += 1
            compilation = PlanCompiler(store, ledger).compile(intent)
            stages.extend(("repairing_semantic_revision", "recompiling_semantic_revision"))
        total_repairs += semantic_repairs
        if compilation.status == CompilationStatus.BLOCKED:
            intent, compilation, context, decision_repairs = _resolve_comparison_questions(
                intent,
                compilation,
                context,
                enabled=confirm_all,
                store=store,
                ledger=ledger,
                simulated_decisions=simulated_decisions,
                decision_agent=decision_agent,
                compliance_agent=compliance_agent,
                compliance_history=compliance_history,
                repair_agent=repair_agent,
                max_repairs=max_repairs,
                stages=stages,
            )
            total_repairs += decision_repairs
        if compilation.status == CompilationStatus.READY:
            review = review_agent.review(intent, context)
            stages.append("bounded_semantic_rereview")
    _emit("compiled", f"编译状态：{compilation.status.value}", started)
    execution_report: dict[str, Any] | None = None
    semantic_passed = review is None or review.audit.get("semantic_gate") is True
    if compilation.status == CompilationStatus.READY and compilation.plan and semantic_passed:
        _emit("executing", "正在对源表全量数据执行确定性计划", started)
        try:
            execution_report = _execute_and_write(
                compilation.plan,
                intent,
                tools,
                dataset_card,
                output,
            )
        except ExecutionError as exc:
            execution_report = {
                "status": "failed",
                "error": {"code": exc.code, "message": str(exc), "node_id": exc.node_id},
            }
        _write_json(output / "execution.json", execution_report)
    elif compilation.status == CompilationStatus.READY and not semantic_passed:
        stages.append("semantic_value_gate_rejected")
        execution_report = {
            "status": "not_executed",
            "reason": "semantic_value_gate_rejected",
        }
        _write_json(output / "execution.json", execution_report)
    report = {
        "architecture": "modeling_ir_conditional_multi_agent",
        "source": str(source.resolve()),
        "resumed_from": str(resume_report.resolve()) if resume_report else None,
        "session_id": sid,
        "model": model,
        "user_goal": goal,
        "intent": workbook_intent_to_dict(intent),
        "compilation": {
            "status": compilation.status.value,
            "diagnostics": [asdict(item) for item in compilation.diagnostics],
            "plan": _plan_to_dict(compilation),
        },
        "orchestration": {
            "stages": stages,
            "repair_attempts": total_repairs,
            "value_review": asdict(review) if review else None,
            "simulated_decisions": simulated_decisions,
            "decision_compliance": compliance_history,
        },
        "execution": execution_report,
        "evidence": evidence,
        "timing": {
            "model_calls": gateway.calls,
            "model_seconds": round(gateway.seconds, 2),
            "frame_load_seconds": round(tools.load_seconds, 2),
            "tool_compute_seconds": round(tools.compute_seconds, 2),
            "total_seconds": round(time.perf_counter() - started, 2),
        },
    }
    _write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--goal", default="这个表太乱了，请帮我制作有业务价值的归纳报表。")
    parser.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY", ""))
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument(
        "--confirm-all",
        action="store_true",
        help="comparison-only: simulate accepting every blocking clarification before execution",
    )
    parser.add_argument("--schema-only", action="store_true")
    args = parser.parse_args()
    if args.schema_only:
        print(json.dumps(workbook_intent_json_schema(), ensure_ascii=False, indent=2))
        return
    if not args.source and not args.resume_report:
        parser.error("--source or --resume-report is required unless --schema-only is used")
    if args.source and args.resume_report:
        parser.error("--source and --resume-report are mutually exclusive")
    if not args.api_key:
        parser.error("DeepSeek API key is required")
    raw_name = args.name or (args.source.stem if args.source else f"{args.resume_report.parent.name}-resume")
    name = re.sub(r"[^0-9A-Za-z_-]+", "-", raw_name).strip("-") or "case"
    report = run_case(
        args.source,
        name,
        args.goal,
        args.api_key,
        args.model,
        max(0, args.max_repairs),
        args.confirm_all,
        args.resume_report,
    )
    print(json.dumps({
        "status": report["compilation"]["status"],
        "title": report["intent"]["title"],
        "sheets": [item["title"] for item in report["intent"]["sheets"]],
        "repairs": report["orchestration"]["repair_attempts"],
        "value_review": report["orchestration"]["value_review"],
        "execution": report["execution"]["status"] if report["execution"] else None,
        "timing": report["timing"],
        "output": str((OUTPUT_ROOT / name / "report.json").resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

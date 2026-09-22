"""Minimal proof for profile -> candidate IR -> deterministic preview."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swiftgrid.backend.services import compose, compose_ai, compose_plan
from swiftgrid.backend.services.orchestrator import Job
from swiftgrid.backend.services.session import new_session


OUTPUT_DIR = ROOT / "artifacts" / "modeling-arch-demo"
SOURCE_SHEET_FIELD = "__source_sheet__"
ALLOWED_FILTERS = {
    "equals", "not_equals", "in", "not_in", "greater_than", "greater_or_equal",
    "less_than", "less_or_equal", "is_empty", "not_empty",
}
ALLOWED_AGGREGATIONS = {"sum", "average", "min", "max", "count", "count_distinct"}


def compact_context(dataset: dict[str, Any]) -> dict[str, Any]:
    sheets = [
        {
            "sheet": item.get("sheet"),
            "rows": item.get("rows"),
            "columns": item.get("columns", []),
        }
        for item in dataset.get("sheets", [])
    ]
    shared_fields = []
    for left_index, left in enumerate(sheets):
        for right in sheets[left_index + 1:]:
            common = sorted(set(left["columns"]) & set(right["columns"]))
            if common:
                shared_fields.append({
                    "left_sheet": left["sheet"],
                    "right_sheet": right["sheet"],
                    "common_fields": common,
                })
    fields = []
    for field in dataset.get("fields", []):
        values = field.get("top_values", [])[:8] if int(field.get("unique_count", 0)) <= 40 else []
        fields.append({
            "name": field.get("name"),
            "type": field.get("data_type"),
            "suggested_role": field.get("role"),
            "rows": field.get("nonempty_count"),
            "null_rate": field.get("null_rate"),
            "unique_count": field.get("unique_count"),
            "positive_count": field.get("positive_count"),
            "negative_count": field.get("negative_count"),
            "zero_count": field.get("zero_count"),
            "top_values": values,
        })
    return {
        "total_rows": dataset.get("total_rows"),
        "sheet_count": dataset.get("sheet_count"),
        "sheets": sheets,
        "shared_fields_between_sheets": shared_fields,
        "columns": dataset.get("schema", {}).get("columns", []),
        "fields": fields,
        "transaction_evidence": dataset.get("evidence", {}).get("transaction", {}),
        "field_relationships": dataset.get("evidence", {}).get("fields", {}),
        "quality_findings": dataset.get("quality_findings", []),
    }


SYSTEM_PROMPT = """
你是目标表建模方向规划器。用户尚未提供目标样例表，请根据数据画像提出 5 个有业务用途、彼此有实质差异的候选目标表。
你只决定目标表用途、基础指标和计算意图，不输出公式树、SQL、Python、图表、趋势结论或经营建议。程序将把意图编译为确定性计划并用真实数据执行。

只输出 JSON：{"candidates": [...]}。每个候选必须包含：
- title：具体目标表名称；purpose：该表解决什么工作问题。
- archetype：entity_summary/time_summary/responsibility_summary/exception_detail/normalized_detail/distribution 之一。
- kind：summary 或 detail。
- group_by：summary 使用的真实字段数组；detail 为空数组。
- filters：数组，每项为 field、operator、value。operator 仅限 equals/not_equals/in/not_in/greater_than/greater_or_equal/less_than/less_or_equal/is_empty/not_empty。
- metrics：summary 使用；每项为 name、aggregation、field，可选 condition 和 source_sheet。aggregation 仅限 sum/average/min/max/count/count_distinct；count 可不填 field。condition 为 field、operator、value，用于条件汇总。同名字段存在于多个工作表时必须用 source_sheet 指明指标来源。
- metric_bundle：仅可为 transaction_amount 或 null。transaction_evidence 充分且方向需要销售/退货口径时优先选择 transaction_amount，由程序生成销售额、退货额、净额和笔数，metrics 留空。
- select_fields：detail 使用的真实字段数组；可用候选级 source_sheet 指明明细来源工作表。
- calculations：最多 4 项，每项包含 name、stage、operation、refs、scale、terms、requires_confirmation、confirmation_question。
  - stage 仅为 row 或 aggregate。detail 使用 row，summary 使用 aggregate。
  - operation 仅为 difference、ratio、product、residual_allocation、linear_combination。
  - difference 的 refs=[被减数,减数]；ratio 的 refs=[分子,分母]，百分比 scale=100；product 的 refs 为相乘项。
  - residual_allocation 的 refs=[原始价值,残值率,分摊年数]，月度结果 scale=1/12。
  - linear_combination 使用 terms=[{"refs":[...],"coefficient":数值},...]，用于多个乘积项求和。
  - row 的引用只能是源字段；aggregate 的引用只能是 metrics.name 或前序 calculations.name。
  - 只声明计算意图，不展开表达式树。业务倍率、折旧方法、截止日期等源表没有的口径必须 requires_confirmation=true 并提出具体问题。
- evidence：支撑该方向的真实字段数组；assumptions：仍需用户确认的口径数组。

约束：
1. 不能只把同一套指标换一个分组字段；5 个候选应覆盖不同工作用途。
2. 金额本身含正负号时，可用 sum 直接形成净额，不要自行编造销售额减退货额公式。
3. exception_detail 必须有真实筛选条件；summary 必须有 group_by 和至少一个 metric；detail 必须有 select_fields。
4. 只引用画像中的真实字段和值。不要输出概率置信度。
5. 不要只相信字段标题，必须核对 suggested_role、unique_count 和 top_values；entity_summary 应优先使用 entity_id/entity_name，不能把权限、状态、类别误当经营主体。
6. 产物是目标表，不写趋势、监控、异常发现、原因分析、预测或经营建议；时间方向命名为按日/月/季度汇总表。
7. 多工作表可以通过共同字段形成汇总，但不能假装源表已有拼接结果。跨表汇总时不要用全局筛选误删提供名称、部门、费率或目标值的辅助表行。
8. 当字段组合明显支持达成率、差额、费用、折旧等派生目标表时，至少提出一个含 calculations 的高价值方向，不要全部退化成简单求和或原样明细。
9. 若类别字段代表不同计算费率或口径（例如平时/周末、销售/退货），必须先用 condition 生成分开的基础指标，不能先混合汇总再套一个统一系数。
10. requires_confirmation=true 时不得声称“暂按默认值计算”；只能保留基础指标并提出具体确认问题。
11. 跨工作表汇总只能把各参与表都真实包含的共同键放入 group_by。若姓名、部门等属性只存在于辅助表，应以 max/min 基础指标带出，不能和共同键一起分组；不要输出依赖尚不存在联表的部门汇总。
12. 当低基数类别字段与数值字段在同一工作表出现、且类别代表不同费率时，应按 top_values 为每个类别建立带 condition 的独立指标，再由 calculations 组合。
13. 同名字段出现在多个工作表时不能混算。例如“日期”同时存在于打卡和加班表，指标必须填写 source_sheet；程序会按真实工作表来源隔离行。
""".strip()


def _filters(raw: Any, columns: set[str]) -> list[dict[str, Any]]:
    result = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("field") not in columns or item.get("operator") not in ALLOWED_FILTERS:
            continue
        condition = {"field": item["field"], "operator": item["operator"]}
        if item["operator"] not in {"is_empty", "not_empty"}:
            condition["value"] = item.get("value")
        result.append(condition)
    return result[:8]


def _sheet_columns(dataset: dict[str, Any]) -> list[set[str]]:
    return [
        set(item.get("columns", []))
        for item in dataset.get("sheets", [])
        if item.get("columns")
    ]


def _fields_cooccur(fields: set[str], sheets: list[set[str]]) -> bool:
    return not fields or any(fields <= columns for columns in sheets)


def _time_minutes(value: Any) -> int | None:
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def augment_time_fields(dataset: dict[str, Any], samples: list[list[Any]]) -> None:
    columns = list(dataset.get("schema", {}).get("columns", []))
    time_fields: dict[str, str] = {}
    for index, name in enumerate(columns):
        if name == SOURCE_SHEET_FIELD:
            continue
        values = [
            row[index] for row in samples
            if index < len(row) and str(row[index] if row[index] is not None else "").strip()
        ]
        if len(values) < 3:
            continue
        parsed = [_time_minutes(value) for value in values]
        if sum(value is not None for value in parsed) / len(values) < 0.95:
            continue
        derived = f"__time_minutes__{name}"
        time_fields[name] = derived
        dataset["schema"]["columns"].append(derived)
        for sheet in dataset.get("sheets", []):
            if name in sheet.get("columns", []):
                sheet["columns"].append(derived)
    for row in samples:
        row.extend(_time_minutes(row[columns.index(name)]) for name in time_fields)
    dataset["_time_fields"] = time_fields


def _rewrite_time_condition(
    condition: dict[str, Any],
    dataset: dict[str, Any],
) -> dict[str, Any]:
    if condition.get("operator") not in {
        "greater_than", "greater_or_equal", "less_than", "less_or_equal",
    }:
        return condition
    derived = dataset.get("_time_fields", {}).get(condition.get("field"))
    expected = _time_minutes(condition.get("value"))
    if not derived or expected is None:
        return condition
    return {**condition, "field": derived, "value": expected}


def _condition(field: str, values: list[Any]) -> dict[str, Any]:
    return {
        "field": field,
        "operator": "equals" if len(values) == 1 else "in",
        "value": values[0] if len(values) == 1 else values,
    }


def transaction_metrics(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = dataset.get("evidence", {}).get("transaction", {})
    transaction_field = evidence.get("transaction_field")
    amount_field = evidence.get("amount_field")
    sales_values = list(evidence.get("sales_values", []))
    return_values = list(evidence.get("return_values", []))
    if (
        not evidence.get("supports_amount_sign")
        or float(evidence.get("agreement_rate", 0)) < 0.98
        or not transaction_field
        or not amount_field
        or not sales_values
        or not return_values
    ):
        raise ValueError("transaction_amount 缺少可靠的交易类型与金额正负号证据")
    field = {"op": "field", "field": amount_field}
    metrics = [
        {
            "name": "销售金额", "aggregation": "sum",
            "expression": {
                "op": "if", "condition": _condition(transaction_field, sales_values),
                "then": field, "else": {"op": "literal", "value": 0},
            },
        },
        {
            "name": "退货金额", "aggregation": "sum",
            "expression": {
                "op": "if", "condition": _condition(transaction_field, return_values),
                "then": {"op": "abs", "args": [field]}, "else": {"op": "literal", "value": 0},
            },
        },
        {"name": "净额", "aggregation": "sum", "expression": field},
        {"name": "记录数", "aggregation": "count"},
    ]
    document_field = next((
        item.get("name") for item in dataset.get("fields", []) if item.get("role") == "document_id"
    ), None)
    if document_field:
        metrics.append({
            "name": "单据数", "aggregation": "count_distinct",
            "expression": {"op": "field", "field": document_field},
        })
    return metrics


def _number(value: Any, default: float = 1.0) -> float | int:
    if value is None:
        value = default
    if isinstance(value, bool):
        raise ValueError("计算常量不能是布尔值")
    try:
        text = str(value).strip()
        fraction = re.fullmatch(r"(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)", text)
        if fraction:
            denominator = float(fraction.group(2))
            if denominator == 0:
                raise ValueError("计算常量的分母不能为零")
            number = float(fraction.group(1)) / denominator
        else:
            number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("计算常量不是有效数字") from exc
    if not math.isfinite(number) or abs(number) > 1_000_000:
        raise ValueError("计算常量超出安全范围")
    return int(number) if number.is_integer() else number


def _multiply(expressions: list[dict[str, Any]]) -> dict[str, Any]:
    if len(expressions) == 1:
        return expressions[0]
    return {"op": "multiply", "args": expressions}


def _scaled(expression: dict[str, Any], scale: float | int) -> dict[str, Any]:
    if scale == 1:
        return expression
    return _multiply([expression, {"op": "literal", "value": scale}])


def _intent_refs(item: dict[str, Any]) -> list[str]:
    if item.get("operation") != "linear_combination":
        return [str(value).strip() for value in item.get("refs", []) if str(value).strip()]
    return [
        str(value).strip()
        for term in item.get("terms", []) if isinstance(term, dict)
        for value in term.get("refs", []) if str(value).strip()
    ]


def _calculation_text(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "派生指标")
    operation = item.get("operation")
    refs = [str(value) for value in item.get("refs", [])]
    scale = _number(item.get("scale"), 1)
    if operation == "difference" and len(refs) == 2:
        formula = f"{refs[0]} - {refs[1]}"
    elif operation == "ratio" and len(refs) == 2:
        formula = f"{refs[0]} / {refs[1]}"
    elif operation == "product":
        formula = " × ".join(refs)
    elif operation == "residual_allocation" and len(refs) == 3:
        formula = f"{refs[0]} × (1 - {refs[1]}) / {refs[2]}"
    else:
        terms = []
        for term in item.get("terms", []) if isinstance(item.get("terms"), list) else []:
            term_refs = " × ".join(str(value) for value in term.get("refs", []))
            coefficient = _number(term.get("coefficient"), 1)
            terms.append(f"({term_refs} × {coefficient:g})")
        formula = " + ".join(terms)
    if scale != 1:
        formula = f"({formula}) × {scale:g}"
    return f"{name} = {formula}"


def compile_calculations(
    raw: Any,
    *,
    expected_stage: str,
    base_refs: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed_operations = {
        "difference", "ratio", "product", "residual_allocation", "linear_combination",
    }
    known_refs = set(base_refs)
    expressions = {name: {"op": "field", "field": name} for name in base_refs}
    compiled = []
    pending = []
    for item in (raw if isinstance(raw, list) else [])[:4]:
        if not isinstance(item, dict):
            raise ValueError("计算意图必须是对象")
        name = str(item.get("name") or "").strip()[:50]
        operation = item.get("operation")
        stage = item.get("stage")
        if not name or name in known_refs:
            raise ValueError(f"计算指标名为空或重复：{name or '未指定'}")
        if operation not in allowed_operations or stage != expected_stage:
            raise ValueError(f"{name} 的计算类型或阶段无效")
        refs = _intent_refs(item)
        if not refs or any(ref not in known_refs for ref in refs):
            missing = [ref for ref in refs if ref not in known_refs]
            raise ValueError(f"{name} 引用了不存在或顺序错误的字段：{missing or refs}")

        scale = _number(item.get("scale"), 1)
        unresolved_dependency = any(ref not in expressions for ref in refs)
        expression = None
        if not unresolved_dependency:
            resolved = [copy.deepcopy(expressions[ref]) for ref in refs]
            if operation == "difference":
                if len(resolved) != 2:
                    raise ValueError(f"{name} 的差额计算必须有两个引用")
                expression = {"op": "subtract", "args": resolved}
            elif operation == "ratio":
                if len(resolved) != 2:
                    raise ValueError(f"{name} 的比率计算必须有两个引用")
                expression = {
                    "op": "if",
                    "condition": {
                        "field": refs[1],
                        "operator": "greater_than",
                        "value": 0,
                    },
                    "then": {"op": "divide", "args": resolved},
                    "else": {"op": "literal", "value": None},
                }
            elif operation == "product":
                if len(resolved) > 5:
                    raise ValueError(f"{name} 的乘积引用过多")
                expression = _multiply(resolved)
            elif operation == "residual_allocation":
                if len(resolved) != 3:
                    raise ValueError(f"{name} 的剩余价值分摊必须有三个引用")
                residual_part = {"op": "subtract", "args": [
                    {"op": "literal", "value": 1}, resolved[1],
                ]}
                expression = {
                    "op": "divide",
                    "args": [_multiply([resolved[0], residual_part]), resolved[2]],
                }
            else:
                terms = item.get("terms", [])
                if not isinstance(terms, list) or not terms or len(terms) > 6:
                    raise ValueError(f"{name} 的线性组合项无效")
                term_expressions = []
                for term in terms:
                    term_refs = [str(value).strip() for value in term.get("refs", []) if str(value).strip()]
                    coefficient = _number(term.get("coefficient"), 1)
                    factors = [copy.deepcopy(expressions[ref]) for ref in term_refs]
                    if coefficient != 1:
                        factors.append({"op": "literal", "value": coefficient})
                    term_expressions.append(_multiply(factors))
                expression = term_expressions[0] if len(term_expressions) == 1 else {
                    "op": "add", "args": term_expressions,
                }
            expression = _scaled(expression, scale)

        local_risk = operation == "residual_allocation"
        if operation == "product" and scale != 1:
            local_risk = True
        if operation == "linear_combination":
            local_risk = local_risk or any(
                _number(term.get("coefficient"), 1) not in {1, -1}
                for term in item.get("terms", [])
            )
        requires_confirmation = bool(item.get("requires_confirmation")) or local_risk or unresolved_dependency
        metadata = {
            "name": name,
            "operation": operation,
            "description": _calculation_text(item),
        }
        if requires_confirmation:
            question = str(item.get("confirmation_question") or "").strip()
            if not question:
                question = f"请确认“{name}”的业务公式和参数后再执行全量计算。"
            metadata["confirmation_question"] = question[:200]
            pending.append(metadata)
        else:
            if expression is None:
                raise ValueError(f"{name} 无法编译为确定性公式")
            expressions[name] = expression
            compiled.append({**metadata, "expression": expression})
        known_refs.add(name)
    return compiled, pending


def normalize_purpose(
    raw: dict[str, Any],
    table: dict[str, Any],
    purpose: str,
    compiled: list[dict[str, Any]],
    pending: list[dict[str, Any]],
) -> tuple[str, str]:
    if table.get("mode") == "aggregate":
        groups = "、".join(item.get("name") or item.get("field") for item in table.get("group_by", []))
        metrics = "、".join(item.get("name", "") for item in table.get("aggregations", []))
        normalized = f"每个{groups}一行，汇总{metrics}。"
    else:
        filters = "；".join(
            f"{item.get('field')}{item.get('operator')}{item.get('value', '')}"
            for item in table.get("filters", [])
        ) or "保留全部记录"
        fields = "、".join(item.get("name", "") for item in table.get("columns", [])[:6])
        normalized = f"按“{filters}”提取记录，输出{fields}等字段。"
    if compiled:
        normalized += f" 本地确定性计算：{'、'.join(item['name'] for item in compiled)}。"
    if pending:
        normalized += f" 待确认口径后计算：{'、'.join(item['name'] for item in pending)}。"
    return normalized, "用途说明已根据最终可执行计划生成，未直接采用模型扩写。"


def compile_candidate(raw: dict[str, Any], dataset: dict[str, Any], index: int) -> dict[str, Any]:
    columns = list(dataset.get("schema", {}).get("columns", []))
    column_set = set(columns)
    sheet_map = {
        str(item.get("sheet")): set(item.get("columns", []))
        for item in dataset.get("sheets", [])
        if item.get("sheet") and item.get("columns")
    }
    sheet_signatures = {
        str(item.get("sheet")): str(item.get("signature") or "")
        for item in dataset.get("sheets", [])
        if item.get("sheet")
    }
    source_sheets = list(sheet_map.values())
    title = str(raw.get("title") or "").strip()[:40]
    purpose = str(raw.get("purpose") or "").strip()[:240]
    archetype = str(raw.get("archetype") or "").strip()
    kind = raw.get("kind")
    if not title or not purpose or kind not in {"summary", "detail"}:
        raise ValueError("缺少标题、用途或有效 kind")
    filters = [
        _rewrite_time_condition(condition, dataset)
        for condition in _filters(raw.get("filters"), column_set)
    ]
    output_name = title[:31]
    compiled_calculations: list[dict[str, Any]] = []
    pending_calculations: list[dict[str, Any]] = []
    if kind == "summary":
        group_by = [name for name in raw.get("group_by", []) if name in column_set][:4]
        field_profiles = {field.get("name"): field for field in dataset.get("fields", [])}
        if archetype == "entity_summary":
            total_rows = max(1, int(dataset.get("total_rows", 0)))
            candidate_keys = {
                name
                for candidate in dataset.get("modeling_evidence", {}).get("candidate_keys", [])
                for name in candidate.get("fields", [])
            }
            entity_tokens = (
                "客户", "供应商", "商品", "产品", "物料", "员工", "人员", "资产", "设备", "门店", "往来单位",
                "工号", "编号", "编码", "ID",
            )
            has_id_name_pair = (
                len(group_by) >= 2
                and any(any(token in name for token in ("编号", "编码", "ID", "Id", "id", "工号")) for name in group_by)
                and any(any(token in name for token in ("名称", "姓名", "名字")) for name in group_by)
            )
            has_entity_evidence = has_id_name_pair or any(
                field_profiles.get(name, {}).get("role") in {"entity_id", "entity_name"}
                or name in candidate_keys
                or any(token in name for token in entity_tokens)
                or (
                    bool(field_profiles.get(name, {}).get("unique_count_exact"))
                    and int(field_profiles.get(name, {}).get("unique_count", 0)) / total_rows >= 0.8
                )
                for name in group_by
            )
            suspicious_values = False
            for name in group_by:
                top_values = field_profiles.get(name, {}).get("top_values", [])
                top_total = sum(int(item.get("count", 0)) for item in top_values)
                permission_total = sum(
                    int(item.get("count", 0)) for item in top_values if "权限" in str(item.get("value", ""))
                )
                suspicious_values = suspicious_values or bool(top_total and permission_total / top_total >= 0.5)
            if suspicious_values:
                raise ValueError("主体汇总字段的代表值主要是权限类别，不是稳定业务实体")
            if not has_entity_evidence:
                archetype = "distribution"
        metrics = []
        used_names = set(group_by)
        if raw.get("metric_bundle") == "transaction_amount":
            metrics = transaction_metrics(dataset)
            used_names.update(item["name"] for item in metrics)
        raw_metrics = [] if metrics else raw.get("metrics", [])
        for item in raw_metrics if isinstance(raw_metrics, list) else []:
            if not isinstance(item, dict) or item.get("aggregation") not in ALLOWED_AGGREGATIONS:
                continue
            aggregation = item["aggregation"]
            field = item.get("field")
            if aggregation != "count" and field not in column_set:
                continue
            if aggregation == "count" and field is not None and field not in column_set:
                continue
            name = str(item.get("name") or ("记录数" if aggregation == "count" else f"{field}{aggregation}")).strip()[:50]
            if not name or name in used_names:
                continue
            used_names.add(name)
            metric = {"name": name, "aggregation": aggregation}
            metric["_requested_source_sheet"] = item.get("source_sheet")
            raw_condition = item.get("condition")
            conditions = _filters([raw_condition], column_set) if isinstance(raw_condition, dict) else []
            if isinstance(raw_condition, dict) and not conditions:
                raise ValueError(f"{name} 的条件汇总引用无效")
            condition = _rewrite_time_condition(conditions[0], dataset) if conditions else None
            if aggregation == "count" and (condition or field):
                count_condition = condition or {"field": field, "operator": "not_empty"}
                metric = {
                    "name": name,
                    "aggregation": "sum",
                    "expression": {
                        "op": "if",
                        "condition": count_condition,
                        "then": {"op": "literal", "value": 1},
                        "else": {"op": "literal", "value": 0},
                    },
                }
            elif aggregation != "count":
                expression: dict[str, Any] = {"op": "field", "field": field}
                if condition:
                    expression = {
                        "op": "if",
                        "condition": condition,
                        "then": expression,
                        "else": {"op": "literal", "value": 0 if aggregation == "sum" else None},
                    }
                metric["expression"] = expression
            metric["_requested_source_sheet"] = item.get("source_sheet")
            metrics.append(metric)
        if not group_by or not metrics:
            raise ValueError("汇总候选缺少有效分组或指标")
        if not _fields_cooccur(set(group_by), source_sheets):
            raise ValueError("分组字段没有在同一源工作表中共同出现，当前计划不能假装已经联表")
        metric_scopes = []
        for metric in metrics:
            requested_scope = metric.pop("_requested_source_sheet", None)
            metric_refs = compose_plan.referenced_fields({
                "filters": [], "group_by": [], "aggregations": [metric], "columns": [],
            })
            supporting_sheets = [
                sheet_name for sheet_name, sheet_columns in sheet_map.items()
                if set(group_by) | metric_refs <= sheet_columns
            ]
            if not supporting_sheets:
                raise ValueError(f"指标“{metric['name']}”与分组字段不在同一源工作表，缺少可靠关联")
            if requested_scope:
                if requested_scope not in supporting_sheets:
                    raise ValueError(f"指标“{metric['name']}”指定的 source_sheet 不包含所需字段")
                scopes = [str(requested_scope)]
            elif len(supporting_sheets) == 1:
                scopes = supporting_sheets
            elif len({sheet_signatures.get(name) for name in supporting_sheets}) == 1:
                scopes = supporting_sheets
            else:
                raise ValueError(
                    f"指标“{metric['name']}”在多个工作表中含义不明确，必须指定 source_sheet"
                )
            expression = metric.get("expression") or {"op": "literal", "value": 1}
            aggregation = metric["aggregation"]
            if aggregation == "count":
                metric["aggregation"] = "sum"
            metric["expression"] = {
                "op": "if",
                "condition": {
                    "field": SOURCE_SHEET_FIELD,
                    "operator": "equals" if len(scopes) == 1 else "in",
                    "value": scopes[0] if len(scopes) == 1 else scopes,
                },
                "then": expression,
                "else": {"op": "literal", "value": 0 if metric["aggregation"] == "sum" else None},
            }
            metric_scopes.extend(scopes)
        active_scopes = list(dict.fromkeys(metric_scopes))
        for condition in filters:
            if any(condition["field"] not in sheet_map[scope] for scope in active_scopes):
                raise ValueError("全局筛选字段不在所有参与工作表中，会误删跨表辅助数据")
        scope_filter = {
            "field": SOURCE_SHEET_FIELD,
            "operator": "equals" if len(active_scopes) == 1 else "in",
            "value": active_scopes[0] if len(active_scopes) == 1 else active_scopes,
        }
        scoped_filters = [scope_filter, *filters]
        compiled_calculations, pending_calculations = compile_calculations(
            raw.get("calculations"),
            expected_stage="aggregate",
            base_refs={item["name"] for item in metrics},
        )
        table = {
            "mode": "aggregate", "output_name": output_name, "filters": scoped_filters,
            "group_by": [{"field": name, "name": name} for name in group_by],
            "aggregations": metrics, "sort": [{"field": metrics[0]["name"], "direction": "desc"}], "limit": None,
        }
        if compiled_calculations:
            table["columns"] = [
                *[{"name": name, "expression": {"op": "field", "field": name}} for name in group_by],
                *[{"name": item["name"], "expression": {"op": "field", "field": item["name"]}} for item in metrics],
                *[{"name": item["name"], "expression": item["expression"]} for item in compiled_calculations],
            ]
            table["sort"] = [{"field": compiled_calculations[0]["name"], "direction": "desc"}]
    else:
        select_fields = [name for name in raw.get("select_fields", []) if name in column_set]
        select_fields = list(dict.fromkeys(select_fields))[:30]
        if not select_fields:
            raise ValueError("明细候选缺少有效输出字段")
        if archetype == "exception_detail" and not filters:
            raise ValueError("异常明细缺少真实筛选条件")
        table = {
            "mode": "detail", "output_name": output_name, "filters": filters,
            "columns": [{"name": name, "expression": {"op": "field", "field": name}} for name in select_fields],
            "sort": [], "limit": None,
        }
        compiled_calculations, pending_calculations = compile_calculations(
            raw.get("calculations"),
            expected_stage="row",
            base_refs=column_set,
        )
        table["columns"].extend(
            {"name": item["name"], "expression": item["expression"]}
            for item in compiled_calculations
        )
        detail_refs = compose_plan.referenced_fields(table)
        supporting_sheets = [
            sheet_name for sheet_name, sheet_columns in sheet_map.items()
            if detail_refs <= sheet_columns
        ]
        if not supporting_sheets:
            raise ValueError("明细输出字段跨越多个源工作表，当前计划缺少真实联表步骤")
        requested_scope = raw.get("source_sheet")
        if requested_scope:
            if requested_scope not in supporting_sheets:
                raise ValueError("明细指定的 source_sheet 不包含全部输出与筛选字段")
            detail_scopes = [str(requested_scope)]
        elif len(supporting_sheets) == 1:
            detail_scopes = supporting_sheets
        elif len({sheet_signatures.get(name) for name in supporting_sheets}) == 1:
            detail_scopes = supporting_sheets
        else:
            raise ValueError("明细字段存在于多个工作表，必须指定 source_sheet")
        table["filters"] = [
            {
                "field": SOURCE_SHEET_FIELD,
                "operator": "equals" if len(detail_scopes) == 1 else "in",
                "value": detail_scopes[0] if len(detail_scopes) == 1 else detail_scopes,
            },
            *filters,
        ]
    compose_plan.validate_table_spec(table, columns)
    purpose, purpose_note = normalize_purpose(
        raw, table, purpose, compiled_calculations, pending_calculations,
    )
    evidence = [name for name in raw.get("evidence", []) if name in column_set][:8]
    assumptions = [str(value).strip()[:160] for value in raw.get("assumptions", []) if str(value).strip()][:4]
    signature = hashlib.sha256(json.dumps(table, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]
    return {
        "id": f"candidate_{index}_{signature}", "title": title, "purpose": purpose,
        "model_purpose": str(raw.get("purpose") or "").strip()[:240],
        "archetype": archetype, "kind": kind, "evidence": evidence,
        "assumptions": assumptions, "table": table, "signature": signature,
        "compiled_calculations": [
            {key: value for key, value in item.items() if key != "expression"}
            for item in compiled_calculations
        ],
        "pending_calculations": pending_calculations,
        "normalization_notes": [purpose_note] if purpose_note else [],
    }


def preview_candidate(candidate: dict[str, Any], dataset: dict[str, Any], samples: list[list[Any]]) -> dict[str, Any]:
    rows = (("source.xlsx", "sample", index + 2, row) for index, row in enumerate(samples))
    result = compose_plan.execute_table_plan(
        candidate["table"], dataset["schema"]["columns"], rows,
        planned_rows=len(samples), skipped_rows=0,
        source_fingerprint=dataset.get("source_fingerprint", "demo"),
    )
    if not result["quality"]["can_complete"]:
        raise ValueError("真实样本执行未通过")
    sheet = result["sheets"][0]
    if not sheet["rows"]:
        raise ValueError("真实样本结果为空")
    rounded_rows = [
        [_round_preview_value(value) for value in row]
        for row in sheet["rows"][:6]
    ]
    return {"columns": sheet["columns"], "rows": rounded_rows, "quality": result["quality"]}


def _round_preview_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return value
    try:
        rounded = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return value
    return format(rounded, "f").rstrip("0").rstrip(".") or "0"


def choose_top_three(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    selected = [candidates[0]]
    first_other_kind = next((item for item in candidates[1:] if item["kind"] != candidates[0]["kind"]), None)
    if first_other_kind:
        selected.append(first_other_kind)
    archetypes = {item["archetype"] for item in selected}
    for candidate in candidates:
        if candidate in selected or candidate["archetype"] in archetypes:
            continue
        selected.append(candidate)
        archetypes.add(candidate["archetype"])
        if len(selected) == 3:
            return selected
    for candidate in candidates:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) == 3:
            return selected
    return selected


def render_report(
    dataset: dict[str, Any], selected: list[dict[str, Any]], rejected: list[str], elapsed: float, model_calls: int,
) -> str:
    cards = []
    for candidate in selected:
        preview = candidate["preview"]
        head = "".join(f"<th>{html.escape(str(value))}</th>" for value in preview["columns"])
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(value if value is not None else ''))}</td>" for value in row) + "</tr>"
            for row in preview["rows"]
        )
        evidence = "、".join(candidate["evidence"]) or "真实字段与样本执行"
        assumptions = "".join(f"<li>{html.escape(value)}</li>" for value in candidate["assumptions"]) or "<li>无额外假设</li>"
        compiled = "".join(
            f"<li>{html.escape(item['description'])}</li>"
            for item in candidate.get("compiled_calculations", [])
        ) or "<li>无派生计算</li>"
        pending = "".join(
            f"<li>{html.escape(item['description'])}<br><b>待确认：</b>{html.escape(item['confirmation_question'])}</li>"
            for item in candidate.get("pending_calculations", [])
        )
        pending_block = f"<div class='pending'><b>高风险口径门禁</b><ul>{pending}</ul></div>" if pending else ""
        cards.append(f"""
        <article class="card">
          <div class="tag">{html.escape(candidate['archetype'])}</div>
          <h2>{html.escape(candidate['title'])}</h2>
          <p>{html.escape(candidate['purpose'])}</p>
          <div class="meta"><b>证据：</b>{html.escape(evidence)}</div>
          <ul>{assumptions}</ul>
          <div class="formula"><b>本地确定性公式</b><ul>{compiled}</ul></div>
          {pending_block}
          <div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
          <div class="passed">真实样本 {preview['quality']['processed_rows']:,} 行执行通过，0 个执行错误</div>
        </article>""")
    rejected_html = "".join(f"<li>{html.escape(value)}</li>" for value in rejected) or "<li>无</li>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>智能制表新架构轻量验证</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f5f7f6;color:#17211c;font-family:Arial,'Microsoft YaHei',sans-serif}}header{{padding:24px 32px;background:#fff;border-bottom:1px solid #dce5df}}h1{{margin:0 0 8px;font-size:24px}}header p{{margin:0;color:#607068}}.summary{{display:flex;gap:24px;padding:14px 32px;background:#edf7f1;border-bottom:1px solid #d6eadf;font-size:13px}}main{{padding:22px 32px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.card{{min-width:0;background:#fff;border:1px solid #dce5df;padding:16px;border-radius:8px}}.tag{{display:inline-block;padding:3px 7px;background:#e7f5ec;color:#087a43;font-size:11px;font-weight:bold}}h2{{font-size:16px;margin:12px 0 7px}}p,.meta,li{{font-size:12px;line-height:1.6;color:#52635b}}ul{{padding-left:18px}}.formula,.pending{{margin-top:10px;padding:9px;background:#f3f7f5;font-size:12px}}.formula ul,.pending ul{{margin-bottom:0}}.pending{{background:#fff7e6;border-left:3px solid #d78a00}}.table-wrap{{overflow:auto;max-height:260px;border:1px solid #dce5df;margin-top:12px}}table{{border-collapse:collapse;font-size:10px;white-space:nowrap}}th,td{{padding:7px 9px;border-right:1px solid #e5ebe7;border-bottom:1px solid #e5ebe7;text-align:left}}th{{position:sticky;top:0;background:#f0f4f2}}.passed{{margin-top:10px;color:#087a43;font-size:11px;font-weight:bold}}details{{margin-top:18px;background:#fff;border:1px solid #dce5df;padding:12px}}@media(max-width:1000px){{.grid{{grid-template-columns:1fr}}main,header{{padding-left:16px;padding-right:16px}}.summary{{padding-left:16px;flex-wrap:wrap}}}}
    </style></head><body><header><h1>智能制表新架构轻量验证</h1><p>全量画像 → 单次模型候选与计算意图 → 本地编译/风险门禁 → 真实样本执行 → Top 3</p></header>
    <div class="summary"><b>源数据 {int(dataset.get('total_rows',0)):,} 行</b><span>{int(dataset.get('sheet_count',0))} 个工作表</span><span>模型调用 {model_calls} 次</span><span>耗时 {elapsed:.1f} 秒</span></div>
    <main><div class="grid">{''.join(cards)}</div><details><summary>被编译器拒绝的候选（{len(rejected)}）</summary><ul>{rejected_html}</ul></details></main></body></html>"""


def profile_source(path: Path) -> str:
    sid = new_session({"kind": "modeling_arch_demo"})
    compose.create_session(sid)
    with path.open("rb") as handle:
        compose.add_uploaded_file(sid, path.name, iter(lambda: handle.read(1024 * 1024), b""))
    compose.run_import(sid, Job(), model_config=None)
    manifest = compose.read_manifest(sid)
    if manifest.get("status") != "READY":
        raise RuntimeError(f"源文件画像失败：{manifest.get('status')}")
    return sid


def read_scoped_samples(
    sid: str,
    dataset: dict[str, Any],
    max_per_sheet: int = 600,
) -> list[list[Any]]:
    source_columns = list(dataset.get("schema", {}).get("columns", []))
    conn = compose._connect(sid)
    counts: dict[str, int] = {}
    rows = []
    try:
        cursor = conn.execute(
            "SELECT sheet_name, values_json FROM source_rows ORDER BY row_id"
        )
        for sheet_name, raw_json in cursor:
            count = counts.get(sheet_name, 0)
            if count >= max_per_sheet:
                continue
            counts[sheet_name] = count + 1
            values = json.loads(raw_json)
            normalized = [
                values[index] if index < len(values) else None
                for index in range(len(source_columns))
            ]
            rows.append([*normalized, sheet_name])
    finally:
        conn.close()
    return rows


def write_catalog() -> None:
    links = []
    for directory in sorted(OUTPUT_DIR.iterdir() if OUTPUT_DIR.exists() else []):
        if directory.is_dir() and (directory / "index.html").exists():
            links.append(f'<a href="./{html.escape(directory.name)}/index.html">{html.escape(directory.name)}</a>')
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>建模架构验证集</title>"
        "<style>body{font-family:Arial,'Microsoft YaHei';padding:32px;background:#f5f7f6;color:#17211c}"
        "a{display:block;margin:10px 0;padding:14px;background:#fff;border:1px solid #dce5df;color:#087a43;text-decoration:none;max-width:520px}</style>"
        "<h1>智能制表新架构验证集</h1><p>每个报告均为一次模型调用后，由程序编译并执行真实样本。</p>" + "".join(links),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session")
    source.add_argument("--source", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--reuse-model", action="store_true")
    parser.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY", ""))
    args = parser.parse_args()
    sid = profile_source(args.source.resolve()) if args.source else args.session
    manifest = compose.read_manifest(sid)
    model_dataset = manifest["dataset"]
    dataset = copy.deepcopy(model_dataset)
    dataset["schema"] = copy.deepcopy(model_dataset["schema"])
    dataset["schema"]["columns"] = [*model_dataset["schema"]["columns"], SOURCE_SHEET_FIELD]
    samples = read_scoped_samples(sid, model_dataset)
    augment_time_fields(dataset, samples)
    raw_name = args.name or (args.source.stem if args.source else sid)
    case_name = re.sub(r"[^0-9A-Za-z_-]+", "-", raw_name).strip("-") or "case"
    case_dir = OUTPUT_DIR / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    model_path = case_dir / "model_response.json"
    started = time.perf_counter()
    if args.reuse_model and model_path.exists():
        response = json.loads(model_path.read_text(encoding="utf-8"))
        error = ""
        model_calls = 0
    else:
        response, error = compose_ai._call_json(
            {"api_key": args.api_key, "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com"},
            SYSTEM_PROMPT,
            {"dataset_profile": compact_context(model_dataset)},
        )
        model_calls = 1
        if response:
            model_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    if not response:
        raise RuntimeError(error or "模型没有返回候选")
    valid = []
    rejected = []
    signatures = set()
    for index, raw in enumerate(response.get("candidates", [])):
        try:
            candidate = compile_candidate(raw, dataset, index)
            if candidate["signature"] in signatures:
                raise ValueError("执行计划与已有候选完全重复")
            candidate["preview"] = preview_candidate(candidate, dataset, samples)
            signatures.add(candidate["signature"])
            valid.append(candidate)
        except Exception as exc:  # noqa: BLE001
            title = raw.get("title") if isinstance(raw, dict) else f"候选 {index + 1}"
            rejected.append(f"{title}：{exc}")
    selected = choose_top_three(valid)
    if len(selected) < 3:
        raise RuntimeError(f"只有 {len(selected)} 个候选通过，拒绝原因：{rejected}")
    elapsed = time.perf_counter() - started
    (case_dir / "report.json").write_text(json.dumps({
        "elapsed_seconds": round(elapsed, 1), "selected": selected, "rejected": rejected,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (case_dir / "index.html").write_text(
        render_report(dataset, selected, rejected, elapsed, model_calls), encoding="utf-8",
    )
    write_catalog()
    print(json.dumps({
        "elapsed_seconds": round(elapsed, 1),
        "selected": [item["title"] for item in selected],
        "rejected": rejected,
        "output": str(case_dir / "index.html"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

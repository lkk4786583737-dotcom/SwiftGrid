"""Domain-neutral, deterministic table-plan compiler and executor.

The model may propose this JSON plan, but this module owns validation and all
row-level calculations.  It deliberately exposes a small expression language
instead of executing model-generated Python or SQL.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable


MAX_EXPRESSION_DEPTH = 12
MAX_EXPRESSION_NODES = 120
MAX_PLAN_COLUMNS = 200
MAX_FILTERS = 32
MAX_SORTS = 8
MAX_OUTPUT_ROWS = 1_048_575

ARITHMETIC_OPS = {"add", "subtract", "multiply", "divide"}
UNARY_OPS = {"abs", "negate"}
VALUE_OPS = {"field", "literal", "coalesce", "concat", "if"}
ALLOWED_EXPRESSION_OPS = ARITHMETIC_OPS | UNARY_OPS | VALUE_OPS
ALLOWED_FILTER_OPERATORS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
    "is_empty",
    "not_empty",
}
ALLOWED_AGGREGATIONS = {"sum", "average", "min", "max", "count", "count_distinct"}


class TablePlanError(ValueError):
    def __init__(self, message: str, *, path: str = "table"):
        super().__init__(message)
        self.message = message
        self.path = path


class EvaluationError(ValueError):
    pass


@dataclass
class _EvaluationStats:
    invalid_cells: int = 0
    missing_cells: int = 0


def _normalize_condition(condition: Any) -> Any:
    if not isinstance(condition, dict):
        return condition
    if condition.get("field") and condition.get("operator"):
        return copy.deepcopy(condition)

    operator_aliases = {
        "eq": "equals",
        "equal": "equals",
        "equals": "equals",
        "ne": "not_equals",
        "not_equal": "not_equals",
        "not_equals": "not_equals",
        "gt": "greater_than",
        "greater_than": "greater_than",
        "gte": "greater_or_equal",
        "greater_or_equal": "greater_or_equal",
        "lt": "less_than",
        "less_than": "less_than",
        "lte": "less_or_equal",
        "less_or_equal": "less_or_equal",
    }
    operator = operator_aliases.get(condition.get("op") or condition.get("operator"))
    left = condition.get("left")
    right = condition.get("right")
    if not operator or not isinstance(left, dict) or not isinstance(right, dict):
        return copy.deepcopy(condition)
    if left.get("op") == "field" and right.get("op") == "literal" and left.get("field"):
        return {"field": left["field"], "operator": operator, "value": right.get("value")}
    if right.get("op") == "field" and left.get("op") == "literal" and right.get("field"):
        reverse = {
            "greater_than": "less_than",
            "greater_or_equal": "less_or_equal",
            "less_than": "greater_than",
            "less_or_equal": "greater_or_equal",
        }
        return {
            "field": right["field"],
            "operator": reverse.get(operator, operator),
            "value": left.get("value"),
        }
    return copy.deepcopy(condition)


def _normalize_expression(expression: Any) -> Any:
    if not isinstance(expression, dict):
        return expression
    normalized = copy.deepcopy(expression)
    if normalized.get("op") == "if":
        normalized["condition"] = _normalize_condition(normalized.get("condition"))
        normalized["then"] = _normalize_expression(normalized.get("then"))
        normalized["else"] = _normalize_expression(normalized.get("else"))
    if isinstance(normalized.get("args"), list):
        normalized["args"] = [_normalize_expression(item) for item in normalized["args"]]
    return normalized


def normalize_model_table_spec(
    spec: dict[str, Any],
    source_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize common model aliases without widening the execution language."""
    normalized = copy.deepcopy(spec) if isinstance(spec, dict) else spec
    if not isinstance(normalized, dict):
        return normalized
    normalized["filters"] = [
        _normalize_condition(item)
        for item in normalized.get("filters", [])
    ] if isinstance(normalized.get("filters"), list) else normalized.get("filters")
    for collection in ("columns", "aggregations"):
        items = normalized.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and "expression" in item:
                item["expression"] = _normalize_expression(item.get("expression"))
    group_by = []
    for item in normalized.get("group_by", []) if isinstance(normalized.get("group_by"), list) else []:
        if isinstance(item, str):
            group_by.append({"field": item, "name": item})
        else:
            group_by.append(item)
    normalized["group_by"] = group_by
    if normalized.get("mode") == "aggregate" and source_columns:
        source_fields = set(source_columns)
        has_final_columns = bool(normalized.get("columns"))
        remaining_columns = []
        for item in normalized.get("columns", []) if isinstance(normalized.get("columns"), list) else []:
            expression = item.get("expression") if isinstance(item, dict) else None
            field = expression.get("field") if isinstance(expression, dict) and expression.get("op") == "field" else None
            if field not in source_fields:
                remaining_columns.append(item)
                continue
            existing = next((group for group in group_by if isinstance(group, dict) and group.get("field") == field), None)
            if existing is not None:
                existing["name"] = item.get("name") or existing.get("name") or field
            else:
                group_by.append({"field": field, "name": item.get("name") or field})
        if has_final_columns:
            final_names = {
                item.get("name") for item in remaining_columns if isinstance(item, dict) and item.get("name")
            }
            group_columns = []
            for group in group_by:
                if not isinstance(group, dict):
                    continue
                name = group.get("name") or group.get("field")
                if not name or name in final_names:
                    continue
                group_columns.append({"name": name, "expression": {"op": "field", "field": name}})
            normalized["columns"] = [*group_columns, *remaining_columns]
        else:
            normalized["columns"] = remaining_columns
    sorts = []
    for item in normalized.get("sort", []) if isinstance(normalized.get("sort"), list) else []:
        if not isinstance(item, dict):
            sorts.append(item)
            continue
        direction = item.get("direction") or item.get("order")
        if direction not in {"asc", "desc"} and isinstance(item.get("asc"), bool):
            direction = "asc" if item["asc"] else "desc"
        sorts.append({"field": item.get("field"), "direction": direction})
    normalized["sort"] = sorts
    return normalized


def referenced_fields(spec: dict[str, Any]) -> set[str]:
    fields: set[str] = set()

    def visit_expression(expression: Any) -> None:
        if not isinstance(expression, dict):
            return
        if expression.get("op") == "field" and expression.get("field"):
            fields.add(expression["field"])
        condition = expression.get("condition")
        if isinstance(condition, dict) and condition.get("field"):
            fields.add(condition["field"])
        for item in expression.get("args", []) if isinstance(expression.get("args"), list) else []:
            visit_expression(item)
        visit_expression(expression.get("then"))
        visit_expression(expression.get("else"))

    for condition in spec.get("filters", []):
        if isinstance(condition, dict) and condition.get("field"):
            fields.add(condition["field"])
    for item in spec.get("group_by", []):
        if isinstance(item, dict) and item.get("field"):
            fields.add(item["field"])
    for collection in ("columns", "aggregations"):
        for item in spec.get(collection, []):
            if isinstance(item, dict):
                visit_expression(item.get("expression"))
    return fields


def _display(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    text = _display(value).replace(",", "").replace("，", "")
    if not text:
        return None
    if text.startswith(("￥", "¥")):
        text = text[1:]
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _number_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _safe_name(value: Any, fallback: str) -> str:
    name = _display(value).replace("/", "_").replace("\\", "_")[:80]
    return name or fallback


def _literal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return value


def validate_condition(condition: dict[str, Any], fields: set[str], path: str) -> None:
    if not isinstance(condition, dict):
        raise TablePlanError("筛选条件必须是对象。", path=path)
    field = condition.get("field")
    operator = condition.get("operator")
    if field not in fields:
        raise TablePlanError(f"筛选字段不存在：{field or '未指定'}。", path=f"{path}.field")
    if operator not in ALLOWED_FILTER_OPERATORS:
        raise TablePlanError(f"不支持的筛选操作：{operator or '未指定'}。", path=f"{path}.operator")
    if operator in {"is_empty", "not_empty"}:
        return
    value = condition.get("value")
    if operator in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise TablePlanError("in/not_in 筛选必须提供非空值列表。", path=f"{path}.value")
    elif value is None:
        raise TablePlanError("筛选条件缺少比较值。", path=f"{path}.value")


def validate_expression(
    expression: dict[str, Any],
    fields: set[str],
    path: str,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_EXPRESSION_NODES:
        raise TablePlanError("单个公式过于复杂，请拆分计算步骤。", path=path)
    if depth > MAX_EXPRESSION_DEPTH:
        raise TablePlanError("公式嵌套层级过深。", path=path)
    if not isinstance(expression, dict):
        raise TablePlanError("公式必须使用结构化表达式。", path=path)
    op = expression.get("op")
    if op not in ALLOWED_EXPRESSION_OPS:
        raise TablePlanError(f"不支持的公式操作：{op or '未指定'}。", path=f"{path}.op")
    if op == "field":
        field = expression.get("field")
        if field not in fields:
            raise TablePlanError(f"公式引用的字段不存在：{field or '未指定'}。", path=f"{path}.field")
        return
    if op == "literal":
        if "value" not in expression:
            raise TablePlanError("常量表达式缺少 value。", path=f"{path}.value")
        return
    if op == "if":
        validate_condition(expression.get("condition"), fields, f"{path}.condition")
        validate_expression(expression.get("then"), fields, f"{path}.then", depth=depth + 1, counter=counter)
        validate_expression(expression.get("else"), fields, f"{path}.else", depth=depth + 1, counter=counter)
        return
    args = expression.get("args")
    if not isinstance(args, list) or not args:
        raise TablePlanError(f"{op} 公式缺少参数。", path=f"{path}.args")
    if op in {"subtract", "divide"} and len(args) != 2:
        raise TablePlanError(f"{op} 公式必须有两个参数。", path=f"{path}.args")
    if op in UNARY_OPS and len(args) != 1:
        raise TablePlanError(f"{op} 公式必须有一个参数。", path=f"{path}.args")
    if len(args) > 16:
        raise TablePlanError("单个公式参数过多。", path=f"{path}.args")
    for index, item in enumerate(args):
        validate_expression(item, fields, f"{path}.args[{index}]", depth=depth + 1, counter=counter)


def validate_table_spec(spec: dict[str, Any], source_columns: list[str]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise TablePlanError("通用制表方案缺少 table 对象。")
    mode = spec.get("mode")
    if mode not in {"detail", "aggregate"}:
        raise TablePlanError("table.mode 必须是 detail 或 aggregate。", path="table.mode")
    source_fields = set(source_columns)
    filters = spec.get("filters", [])
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        raise TablePlanError(f"筛选条件不能超过 {MAX_FILTERS} 项。", path="table.filters")
    for index, condition in enumerate(filters):
        validate_condition(condition, source_fields, f"table.filters[{index}]")

    output_fields: set[str] = set()
    if mode == "detail":
        columns = spec.get("columns")
        if not isinstance(columns, list) or not columns:
            raise TablePlanError("明细方案必须至少定义一个输出字段。", path="table.columns")
        if len(columns) > MAX_PLAN_COLUMNS:
            raise TablePlanError(f"输出字段不能超过 {MAX_PLAN_COLUMNS} 个。", path="table.columns")
        for index, item in enumerate(columns):
            if not isinstance(item, dict):
                raise TablePlanError("输出字段必须是对象。", path=f"table.columns[{index}]")
            name = _safe_name(item.get("name"), "")
            if not name or name in output_fields:
                raise TablePlanError(f"输出字段名为空或重复：{name or '未指定'}。", path=f"table.columns[{index}].name")
            output_fields.add(name)
            validate_expression(item.get("expression"), source_fields, f"table.columns[{index}].expression")
    else:
        group_by = spec.get("group_by", [])
        aggregations = spec.get("aggregations", [])
        if not isinstance(group_by, list) or not isinstance(aggregations, list):
            raise TablePlanError("分组和聚合定义必须是数组。")
        if not group_by and not aggregations:
            raise TablePlanError("汇总方案必须包含分组字段或聚合指标。")
        for index, item in enumerate(group_by):
            if not isinstance(item, dict) or item.get("field") not in source_fields:
                raise TablePlanError("分组字段不存在。", path=f"table.group_by[{index}]")
            name = _safe_name(item.get("name"), str(item.get("field") or ""))
            if name in output_fields:
                raise TablePlanError(f"输出字段名重复：{name}。", path=f"table.group_by[{index}].name")
            output_fields.add(name)
        for index, item in enumerate(aggregations):
            if not isinstance(item, dict) or item.get("aggregation") not in ALLOWED_AGGREGATIONS:
                raise TablePlanError("聚合方式无效。", path=f"table.aggregations[{index}]")
            name = _safe_name(item.get("name"), "")
            if not name or name in output_fields:
                raise TablePlanError(f"聚合指标名为空或重复：{name or '未指定'}。", path=f"table.aggregations[{index}].name")
            output_fields.add(name)
            if item.get("aggregation") != "count":
                validate_expression(item.get("expression"), source_fields, f"table.aggregations[{index}].expression")
        columns = spec.get("columns", [])
        if columns:
            if not isinstance(columns, list) or len(columns) > MAX_PLAN_COLUMNS:
                raise TablePlanError("汇总后的输出字段定义无效。", path="table.columns")
            final_names: set[str] = set()
            for index, item in enumerate(columns):
                name = _safe_name(item.get("name") if isinstance(item, dict) else "", "")
                if not name or name in final_names:
                    raise TablePlanError(f"输出字段名为空或重复：{name or '未指定'}。", path=f"table.columns[{index}].name")
                final_names.add(name)
                validate_expression(item.get("expression"), output_fields, f"table.columns[{index}].expression")
            output_fields = final_names

    sorts = spec.get("sort", [])
    if not isinstance(sorts, list) or len(sorts) > MAX_SORTS:
        raise TablePlanError(f"排序条件不能超过 {MAX_SORTS} 项。", path="table.sort")
    for index, item in enumerate(sorts):
        if not isinstance(item, dict) or item.get("field") not in output_fields or item.get("direction") not in {"asc", "desc"}:
            raise TablePlanError("排序字段或方向无效。", path=f"table.sort[{index}]")
    limit = spec.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1 or limit > MAX_OUTPUT_ROWS):
        raise TablePlanError("输出行数上限无效。", path="table.limit")
    return spec


def _condition_matches(condition: dict[str, Any], row: dict[str, Any]) -> bool:
    actual = row.get(condition["field"])
    actual_text = _display(actual)
    operator = condition["operator"]
    expected = condition.get("value")
    if operator == "is_empty":
        return actual_text == ""
    if operator == "not_empty":
        return actual_text != ""
    if operator in {"in", "not_in"}:
        values = {_display(value).casefold() for value in expected}
        matched = actual_text.casefold() in values
        return matched if operator == "in" else not matched
    expected_text = _display(expected)
    if operator == "equals":
        return actual_text.casefold() == expected_text.casefold()
    if operator == "not_equals":
        return actual_text.casefold() != expected_text.casefold()
    if operator == "contains":
        return expected_text.casefold() in actual_text.casefold()
    if operator == "not_contains":
        return expected_text.casefold() not in actual_text.casefold()
    actual_number = _decimal(actual)
    expected_number = _decimal(expected)
    if actual_number is None or expected_number is None:
        return False
    if operator == "greater_than":
        return actual_number > expected_number
    if operator == "greater_or_equal":
        return actual_number >= expected_number
    if operator == "less_than":
        return actual_number < expected_number
    return actual_number <= expected_number


def _evaluate(expression: dict[str, Any], row: dict[str, Any], stats: _EvaluationStats) -> Any:
    op = expression["op"]
    if op == "field":
        return row.get(expression["field"])
    if op == "literal":
        return _literal(expression.get("value"))
    if op == "if":
        branch = expression["then"] if _condition_matches(expression["condition"], row) else expression["else"]
        return _evaluate(branch, row, stats)
    args = [_evaluate(item, row, stats) for item in expression["args"]]
    if op == "coalesce":
        return next((value for value in args if _display(value) != ""), None)
    if op == "concat":
        separator = _display(expression.get("separator"))
        return separator.join(_display(value) for value in args if _display(value) != "")
    numbers = [_decimal(value) for value in args]
    if any(value is None for value in numbers):
        stats.missing_cells += 1
        return None
    values = [value for value in numbers if value is not None]
    try:
        if op == "add":
            return sum(values, Decimal("0"))
        if op == "subtract":
            return values[0] - values[1]
        if op == "multiply":
            result = Decimal("1")
            for value in values:
                result *= value
            return result
        if op == "divide":
            if values[1] == 0:
                stats.invalid_cells += 1
                return None
            return values[0] / values[1]
        if op == "abs":
            return values[0].copy_abs()
        if op == "negate":
            return -values[0]
    except (InvalidOperation, ArithmeticError) as exc:
        stats.invalid_cells += 1
        raise EvaluationError(str(exc)) from exc
    raise EvaluationError(f"Unsupported expression operation: {op}")


def _output_value(value: Any) -> Any:
    return _number_text(value) if isinstance(value, Decimal) else value


def _sort_value(value: Any) -> tuple[int, Any]:
    numeric = _decimal(value)
    return (0, numeric) if numeric is not None else (1, _display(value).casefold())


def _sort_records(records: list[dict[str, Any]], sorts: list[dict[str, Any]]) -> None:
    for item in reversed(sorts):
        present = [row for row in records if _display(row.get(item["field"])) != ""]
        missing = [row for row in records if _display(row.get(item["field"])) == ""]
        present.sort(key=lambda row: _sort_value(row.get(item["field"])), reverse=item["direction"] == "desc")
        records[:] = present + missing


def _sort_detail_rows(rows: list[list[Any]], sorts: list[dict[str, Any]], columns: list[str]) -> None:
    indexes = {name: index for index, name in enumerate(columns)}
    for item in reversed(sorts):
        index = indexes[item["field"]]
        present = [row for row in rows if _display(row[index]) != ""]
        missing = [row for row in rows if _display(row[index]) == ""]
        present.sort(key=lambda row: _sort_value(row[index]), reverse=item["direction"] == "desc")
        rows[:] = present + missing


def _new_aggregate_state(aggregation: str) -> dict[str, Any]:
    return {"aggregation": aggregation, "sum": Decimal("0"), "count": 0, "min": None, "max": None, "distinct": set()}


def _update_aggregate_state(state: dict[str, Any], value: Any) -> None:
    aggregation = state["aggregation"]
    if aggregation == "count":
        state["count"] += 1
        return
    if value is None or _display(value) == "":
        return
    if aggregation == "count_distinct":
        state["distinct"].add(_display(value))
        return
    if aggregation in {"min", "max"}:
        state["count"] += 1
        if state["min"] is None or _sort_value(value) < _sort_value(state["min"]):
            state["min"] = value
        if state["max"] is None or _sort_value(value) > _sort_value(state["max"]):
            state["max"] = value
        return
    numeric = _decimal(value)
    if numeric is None:
        raise EvaluationError("聚合指标包含非数值内容。")
    state["sum"] += numeric
    state["count"] += 1
    state["min"] = numeric if state["min"] is None else min(state["min"], numeric)
    state["max"] = numeric if state["max"] is None else max(state["max"], numeric)


def _finalize_aggregate_state(state: dict[str, Any]) -> Any:
    aggregation = state["aggregation"]
    if aggregation == "count":
        return state["count"]
    if aggregation == "count_distinct":
        return len(state["distinct"])
    if aggregation == "average":
        return state["sum"] / state["count"] if state["count"] else None
    if aggregation == "min":
        return state["min"]
    if aggregation == "max":
        return state["max"]
    return state["sum"]


def execute_table_plan(
    spec: dict[str, Any],
    source_columns: list[str],
    rows: Iterable[tuple[str, str, int, list[Any]]],
    *,
    planned_rows: int,
    skipped_rows: int,
    source_fingerprint: str,
    progress: Callable[[int, str, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    validate_table_spec(spec, source_columns)
    stats = _EvaluationStats()
    processed = filtered_rows = invalid_rows = 0
    mode = spec["mode"]
    detail_records: list[list[Any]] = []
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    group_defs = spec.get("group_by", [])
    aggregation_defs = spec.get("aggregations", [])

    for source_file, sheet_name, _source_row, values in rows:
        if cancelled and cancelled():
            raise TablePlanError("全量执行已取消。", path="execution")
        processed += 1
        if progress and (processed == 1 or processed % 5000 == 0):
            progress(processed, source_file, sheet_name)
        source = {name: values[index] if index < len(values) else None for index, name in enumerate(source_columns)}
        if any(not _condition_matches(condition, source) for condition in spec.get("filters", [])):
            filtered_rows += 1
            continue
        try:
            if mode == "detail":
                record = [
                    _evaluate(item["expression"], source, stats)
                    for item in spec["columns"]
                ]
                detail_records.append(record)
            else:
                group_values = tuple(_display(source.get(item["field"])) for item in group_defs)
                values = []
                for item in aggregation_defs:
                    value = None if item["aggregation"] == "count" else _evaluate(item["expression"], source, stats)
                    if (
                        item["aggregation"] in {"sum", "average"}
                        and value is not None
                        and _display(value) != ""
                        and _decimal(value) is None
                    ):
                        raise EvaluationError("聚合指标包含非数值内容。")
                    values.append(value)
                group = grouped.setdefault(group_values, {
                    "values": {item.get("name") or item["field"]: group_values[index] for index, item in enumerate(group_defs)},
                    "aggregations": {item["name"]: _new_aggregate_state(item["aggregation"]) for item in aggregation_defs},
                })
                for item, value in zip(aggregation_defs, values):
                    _update_aggregate_state(group["aggregations"][item["name"]], value)
        except EvaluationError:
            invalid_rows += 1
        if len(detail_records) > MAX_OUTPUT_ROWS or len(grouped) > MAX_OUTPUT_ROWS:
            raise TablePlanError("结果超过 Excel 单个工作表的行数上限，请增加筛选、分组或 Top N。", path="table")

    if mode == "aggregate":
        records = []
        for group in grouped.values():
            aggregate_record = dict(group["values"])
            aggregate_record.update({name: _finalize_aggregate_state(state) for name, state in group["aggregations"].items()})
            try:
                if spec.get("columns"):
                    record = {
                        item["name"]: _evaluate(item["expression"], aggregate_record, stats)
                        for item in spec["columns"]
                    }
                else:
                    record = aggregate_record
                records.append(record)
            except EvaluationError:
                invalid_rows += 1
        _sort_records(records, spec.get("sort", []))
        limit = spec.get("limit")
        if limit is not None:
            records = records[:limit]
        if spec.get("columns"):
            output_columns = [item["name"] for item in spec["columns"]]
        else:
            output_columns = [item.get("name") or item["field"] for item in group_defs] + [item["name"] for item in aggregation_defs]
        output_rows = [[_output_value(record.get(column)) for column in output_columns] for record in records]
    else:
        output_columns = [item["name"] for item in spec["columns"]]
        _sort_detail_rows(detail_records, spec.get("sort", []), output_columns)
        limit = spec.get("limit")
        if limit is not None:
            del detail_records[limit:]
        for row in detail_records:
            for index, value in enumerate(row):
                row[index] = _output_value(value)
        output_rows = detail_records

    row_reconciliation_ok = processed == planned_rows
    execution_ok = invalid_rows == 0 and stats.invalid_cells == 0
    reconciliations = [
        {
            "name": "source_to_processed_rows",
            "status": "passed" if row_reconciliation_ok else "failed",
            "delta": str(processed - planned_rows),
        },
        {
            "name": "plan_execution_errors",
            "status": "passed" if execution_ok else "failed",
            "delta": str(invalid_rows + stats.invalid_cells),
        },
    ]
    quality = {
        "scope": "full",
        "planned_rows": planned_rows,
        "processed_rows": processed,
        "valid_rows": processed - filtered_rows - invalid_rows,
        "invalid_rows": invalid_rows,
        "skipped_rows": skipped_rows,
        "filtered_rows": filtered_rows,
        "measure_invalid_cells": stats.invalid_cells,
        "excluded_null_measure_cells": stats.missing_cells,
        "transaction_sign_conflict_rows": 0,
        "entity_name_conflict_count": 0,
        "classification_enabled": False,
        "source_fingerprint": source_fingerprint,
        "reconciliations": reconciliations,
        "pending_classification_count": 0,
        "can_complete": row_reconciliation_ok and execution_ok,
    }
    return {
        "sheets": [{
            "name": _safe_name(spec.get("output_name"), "目标表")[:31],
            "columns": output_columns,
            "rows": output_rows,
        }],
        "quality": quality,
        "source_sales": "",
        "source_returns": "",
        "source_net": "",
        "source_metrics": {},
    }

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum
from typing import Any, Mapping

from .ir import (
    Additivity,
    ClarificationQuestion,
    Composition,
    DerivedExpression,
    DimensionBinding,
    DimensionIntent,
    DimensionTransform,
    JoinCardinality,
    JoinIntent,
    MetricIntent,
    MetricSemantic,
    MetricUnit,
    Operation,
    RiskLevel,
    SheetIntent,
    SortDirection,
    SortIntent,
    TransformKind,
    WorkbookIntentIR,
)


class IRParseError(ValueError):
    pass


def workbook_intent_to_dict(intent: WorkbookIntentIR) -> dict[str, Any]:
    return _jsonable(asdict(intent))


def workbook_intent_to_json(intent: WorkbookIntentIR) -> str:
    return json.dumps(workbook_intent_to_dict(intent), ensure_ascii=False, sort_keys=True)


def parse_workbook_intent_json(value: str) -> WorkbookIntentIR:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise IRParseError(f"invalid JSON: {exc}") from exc
    return parse_workbook_intent(payload)


def parse_workbook_intent(payload: Mapping[str, Any]) -> WorkbookIntentIR:
    root = _object(payload, "workbook")
    _keys(root, {"ir_version", "id", "title", "purpose", "sheets", "joins", "clarifications", "metadata"}, set(), "workbook")
    if root["ir_version"] != "1.0":
        raise IRParseError("workbook.ir_version must be 1.0")
    return WorkbookIntentIR(
        id=_text(root["id"], "workbook.id"),
        title=_text(root["title"], "workbook.title"),
        purpose=_text(root["purpose"], "workbook.purpose"),
        sheets=tuple(_parse_sheet(item, index) for index, item in enumerate(_list(root["sheets"], "workbook.sheets"))),
        joins=tuple(_parse_join(item, index) for index, item in enumerate(_list(root["joins"], "workbook.joins"))),
        clarifications=tuple(
            _parse_question(item, index)
            for index, item in enumerate(_list(root["clarifications"], "workbook.clarifications"))
        ),
        metadata=dict(_object(root["metadata"], "workbook.metadata")),
        ir_version="1.0",
    )


def _parse_sheet(value: Any, index: int) -> SheetIntent:
    path = f"workbook.sheets[{index}]"
    item = _object(value, path)
    _keys(
        item,
        {"id", "title", "purpose", "dimensions", "target_grain", "metrics"},
        {"order_by", "limit", "minimum_group_size", "group_count_metric_id"},
        path,
    )
    limit = item.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise IRParseError(f"{path}.limit must be an integer or null")
    return SheetIntent(
        id=_text(item["id"], f"{path}.id"),
        title=_text(item["title"], f"{path}.title"),
        purpose=_text(item["purpose"], f"{path}.purpose"),
        dimensions=tuple(_parse_dimension(raw, path, i) for i, raw in enumerate(_list(item["dimensions"], f"{path}.dimensions"))),
        target_grain=_text_tuple(item["target_grain"], f"{path}.target_grain"),
        metrics=tuple(_parse_metric(raw, path, i) for i, raw in enumerate(_list(item["metrics"], f"{path}.metrics"))),
        order_by=tuple(
            _parse_sort(raw, path, i)
            for i, raw in enumerate(_list(item.get("order_by", []), f"{path}.order_by"))
        ),
        limit=limit,
        minimum_group_size=item.get("minimum_group_size"),
        group_count_metric_id=_text_optional(
            item.get("group_count_metric_id") or "",
            f"{path}.group_count_metric_id",
        ) or None,
    )


def _parse_sort(value: Any, parent: str, index: int) -> SortIntent:
    path = f"{parent}.order_by[{index}]"
    item = _object(value, path)
    _keys(item, {"direction"}, {"column_id", "metric_id"}, path)
    column_id = item.get("column_id") or item.get("metric_id")
    if "column_id" in item and "metric_id" in item:
        raise IRParseError(f"{path} must use column_id only")
    return SortIntent(
        column_id=_text(column_id, f"{path}.column_id"),
        direction=_enum(SortDirection, item["direction"], f"{path}.direction"),
    )


def _parse_dimension(value: Any, parent: str, index: int) -> DimensionIntent:
    path = f"{parent}.dimensions[{index}]"
    item = _object(value, path)
    _keys(item, {"id", "name", "bindings"}, set(), path)
    return DimensionIntent(
        id=_text(item["id"], f"{path}.id"),
        name=_text(item["name"], f"{path}.name"),
        bindings=tuple(_parse_binding(raw, path, i) for i, raw in enumerate(_list(item["bindings"], f"{path}.bindings"))),
    )


def _parse_binding(value: Any, parent: str, index: int) -> DimensionBinding:
    path = f"{parent}.bindings[{index}]"
    item = _object(value, path)
    _keys(item, {"id", "source_sheet", "source_fields", "transform", "evidence_refs", "required_decision_ids"}, set(), path)
    transform = _object(item["transform"], f"{path}.transform")
    _keys(transform, {"kind", "parameters"}, set(), f"{path}.transform")
    return DimensionBinding(
        id=_text(item["id"], f"{path}.id"),
        source_sheet=_text(item["source_sheet"], f"{path}.source_sheet"),
        source_fields=_text_tuple(item["source_fields"], f"{path}.source_fields"),
        transform=DimensionTransform(
            _enum(TransformKind, transform["kind"], f"{path}.transform.kind"),
            dict(_object(transform["parameters"], f"{path}.transform.parameters")),
        ),
        evidence_refs=_text_tuple(item["evidence_refs"], f"{path}.evidence_refs"),
        required_decision_ids=_text_tuple(item["required_decision_ids"], f"{path}.required_decision_ids"),
    )


def _parse_metric(value: Any, parent: str, index: int) -> MetricIntent:
    path = f"{parent}.metrics[{index}]"
    item = _object(value, path)
    required = {
        "id", "name", "source_sheet", "source_fields", "source_grain", "target_grain",
        "dimension_binding_ids", "operation", "semantic", "entity_key", "additivity",
        "composition", "evidence_refs", "dependencies", "required_decision_ids",
        "uniqueness_required", "expression",
    }
    _keys(item, required, {"business_definition", "unit", "risk_level"}, path)
    expression = item["expression"]
    parsed_expression = None
    if expression is not None:
        expression = _object(expression, f"{path}.expression")
        _keys(expression, {"operator", "operands", "zero_division", "parameters"}, set(), f"{path}.expression")
        parsed_expression = DerivedExpression(
            _text(expression["operator"], f"{path}.expression.operator"),
            _text_tuple(expression["operands"], f"{path}.expression.operands"),
            _text(expression["zero_division"], f"{path}.expression.zero_division"),
            dict(_object(expression["parameters"], f"{path}.expression.parameters")),
        )
    source_sheet = item["source_sheet"]
    if source_sheet is not None:
        source_sheet = _text(source_sheet, f"{path}.source_sheet")
    if not isinstance(item["uniqueness_required"], bool):
        raise IRParseError(f"{path}.uniqueness_required must be boolean")
    return MetricIntent(
        id=_text(item["id"], f"{path}.id"),
        name=_text(item["name"], f"{path}.name"),
        source_sheet=source_sheet,
        source_fields=_text_tuple(item["source_fields"], f"{path}.source_fields"),
        source_grain=_text_tuple(item["source_grain"], f"{path}.source_grain"),
        target_grain=_text_tuple(item["target_grain"], f"{path}.target_grain"),
        dimension_binding_ids=_text_tuple(item["dimension_binding_ids"], f"{path}.dimension_binding_ids"),
        operation=_enum(Operation, item["operation"], f"{path}.operation"),
        semantic=_enum(MetricSemantic, item["semantic"], f"{path}.semantic"),
        entity_key=_text_tuple(item["entity_key"], f"{path}.entity_key"),
        additivity=_enum(Additivity, item["additivity"], f"{path}.additivity"),
        composition=_enum(Composition, item["composition"], f"{path}.composition"),
        evidence_refs=_text_tuple(item["evidence_refs"], f"{path}.evidence_refs"),
        dependencies=_text_tuple(item["dependencies"], f"{path}.dependencies"),
        required_decision_ids=_text_tuple(item["required_decision_ids"], f"{path}.required_decision_ids"),
        uniqueness_required=item["uniqueness_required"],
        expression=parsed_expression,
        business_definition=_text_optional(
            item.get("business_definition", ""),
            f"{path}.business_definition",
        ),
        unit=_enum(MetricUnit, item.get("unit", "unknown"), f"{path}.unit"),
        risk_level=_enum(
            RiskLevel,
            item.get("risk_level", "medium"),
            f"{path}.risk_level",
        ),
    )


def _parse_join(value: Any, index: int) -> JoinIntent:
    path = f"workbook.joins[{index}]"
    item = _object(value, path)
    _keys(item, {"id", "left_sheet", "right_sheet", "left_keys", "right_keys", "cardinality", "evidence_ref", "preserve_source_rows"}, set(), path)
    if not isinstance(item["preserve_source_rows"], bool):
        raise IRParseError(f"{path}.preserve_source_rows must be boolean")
    return JoinIntent(
        _text(item["id"], f"{path}.id"),
        _text(item["left_sheet"], f"{path}.left_sheet"),
        _text(item["right_sheet"], f"{path}.right_sheet"),
        _text_tuple(item["left_keys"], f"{path}.left_keys"),
        _text_tuple(item["right_keys"], f"{path}.right_keys"),
        _enum(JoinCardinality, item["cardinality"], f"{path}.cardinality"),
        _text(item["evidence_ref"], f"{path}.evidence_ref"),
        item["preserve_source_rows"],
    )


def _parse_question(value: Any, index: int) -> ClarificationQuestion:
    path = f"workbook.clarifications[{index}]"
    item = _object(value, path)
    _keys(item, {"id", "question", "why_it_matters", "affected_node_ids", "blocking", "options"}, set(), path)
    if not isinstance(item["blocking"], bool):
        raise IRParseError(f"{path}.blocking must be boolean")
    return ClarificationQuestion(
        _text(item["id"], f"{path}.id"),
        _text(item["question"], f"{path}.question"),
        _text(item["why_it_matters"], f"{path}.why_it_matters"),
        _text_tuple(item["affected_node_ids"], f"{path}.affected_node_ids"),
        item["blocking"],
        _text_tuple(item["options"], f"{path}.options"),
    )


def workbook_intent_json_schema() -> dict[str, Any]:
    text_array = {"type": "array", "items": {"type": "string"}}
    transform = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "parameters"],
        "properties": {
            "kind": {"enum": [item.value for item in TransformKind]},
            "parameters": {"type": "object"},
        },
    }
    binding = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "source_sheet", "source_fields", "transform", "evidence_refs", "required_decision_ids"],
        "properties": {
            "id": {"type": "string"}, "source_sheet": {"type": "string"},
            "source_fields": text_array, "transform": transform,
            "evidence_refs": text_array, "required_decision_ids": text_array,
        },
    }
    dimension = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "name", "bindings"],
        "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "bindings": {"type": "array", "items": binding},
        },
    }
    expression = {
        "type": ["object", "null"],
        "additionalProperties": False,
        "required": ["operator", "operands", "zero_division", "parameters"],
        "properties": {
            "operator": {"type": "string"}, "operands": text_array,
            "zero_division": {"enum": ["null", "zero", "error"]},
            "parameters": {"type": "object"},
        },
    }
    metric_properties = {
        "id": {"type": "string"}, "name": {"type": "string"},
        "source_sheet": {"type": ["string", "null"]}, "source_fields": text_array,
        "source_grain": text_array, "target_grain": text_array,
        "dimension_binding_ids": text_array,
        "operation": {"enum": [item.value for item in Operation]},
        "semantic": {"enum": [item.value for item in MetricSemantic]},
        "entity_key": text_array, "additivity": {"enum": [item.value for item in Additivity]},
        "composition": {"enum": [item.value for item in Composition]},
        "evidence_refs": text_array, "dependencies": text_array,
        "required_decision_ids": text_array, "uniqueness_required": {"type": "boolean"},
        "expression": expression,
        "business_definition": {"type": "string", "minLength": 1},
        "unit": {"enum": [item.value for item in MetricUnit if item != MetricUnit.UNKNOWN]},
        "risk_level": {"enum": [item.value for item in RiskLevel]},
    }
    metric = {
        "type": "object",
        "additionalProperties": False,
        "required": list(metric_properties),
        "properties": metric_properties,
    }
    sheet = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id", "title", "purpose", "dimensions", "target_grain", "metrics",
            "order_by", "limit", "minimum_group_size", "group_count_metric_id",
        ],
        "properties": {
            "id": {"type": "string"}, "title": {"type": "string"}, "purpose": {"type": "string"},
            "dimensions": {"type": "array", "items": dimension},
            "target_grain": text_array, "metrics": {"type": "array", "items": metric},
            "order_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["column_id", "direction"],
                    "properties": {
                        "column_id": {"type": "string"},
                        "direction": {"enum": [item.value for item in SortDirection]},
                    },
                },
            },
            "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 10000},
            "minimum_group_size": {"type": ["integer", "null"], "minimum": 2, "maximum": 10000},
            "group_count_metric_id": {"type": ["string", "null"]},
        },
    }
    join_properties = {
        "id": {"type": "string"}, "left_sheet": {"type": "string"}, "right_sheet": {"type": "string"},
        "left_keys": text_array, "right_keys": text_array,
        "cardinality": {"enum": [item.value for item in JoinCardinality]},
        "evidence_ref": {"type": "string"}, "preserve_source_rows": {"type": "boolean"},
    }
    question_properties = {
        "id": {"type": "string"}, "question": {"type": "string"},
        "why_it_matters": {"type": "string"}, "affected_node_ids": text_array,
        "blocking": {"type": "boolean"}, "options": text_array,
    }
    root_properties = {
        "ir_version": {"const": "1.0"}, "id": {"type": "string"},
        "title": {"type": "string"}, "purpose": {"type": "string"},
        "sheets": {"type": "array", "items": sheet},
        "joins": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False, "required": list(join_properties), "properties": join_properties},
        },
        "clarifications": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False, "required": list(question_properties), "properties": question_properties},
        },
        "metadata": {"type": "object"},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "WorkbookIntentIR",
        "type": "object",
        "additionalProperties": False,
        "required": list(root_properties),
        "properties": root_properties,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _keys(value: Mapping[str, Any], required: set[str], optional: set[str], path: str) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise IRParseError(f"{path} missing required keys: {sorted(missing)}")
    if unknown:
        raise IRParseError(f"{path} has unknown keys: {sorted(unknown)}")


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IRParseError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise IRParseError(f"{path} must be an array")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise IRParseError(f"{path} must be a non-empty string")
    return value


def _text_optional(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise IRParseError(f"{path} must be a string")
    return value


def _text_tuple(value: Any, path: str) -> tuple[str, ...]:
    return tuple(_text(item, f"{path}[]") for item in _list(value, path))


def _enum(enum_type, value: Any, path: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise IRParseError(f"{path} has unsupported value {value!r}") from exc

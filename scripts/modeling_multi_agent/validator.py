from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .evidence import EvidenceStore
from .ir import (
    Additivity,
    Composition,
    DecisionLedger,
    DimensionBinding,
    DimensionIntent,
    JoinCardinality,
    JoinIntent,
    MetricIntent,
    MetricSemantic,
    MetricUnit,
    Operation,
    RiskLevel,
    TransformKind,
    WorkbookIntentIR,
)


class Severity(str, Enum):
    ERROR = "error"
    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    node_id: str
    severity: Severity = Severity.ERROR
    repairable: bool = True


@dataclass(frozen=True)
class EnrichmentStep:
    join: JoinIntent
    reversed_edge: bool
    from_sheet: str
    to_sheet: str
    from_keys: tuple[str, ...]
    to_keys: tuple[str, ...]


class AlgebraicInvariantValidator:
    def __init__(self, evidence: EvidenceStore, decisions: DecisionLedger | None = None) -> None:
        self.evidence = evidence
        self.decisions = decisions or DecisionLedger()

    def validate(self, workbook: WorkbookIntentIR) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        node_ids = self._node_ids(workbook)
        if len(node_ids) != len(set(node_ids)):
            diagnostics.append(Diagnostic("DUPLICATE_NODE_ID", "IR node ids must be unique", workbook.id, repairable=False))

        questions = {item.id: item for item in workbook.clarifications}
        for question in workbook.clarifications:
            missing = set(question.affected_node_ids) - set(node_ids)
            if missing:
                diagnostics.append(Diagnostic("INVALID_QUESTION_BINDING", f"clarification references unknown nodes: {sorted(missing)}", question.id))
            if question.blocking and not self.decisions.is_confirmed(question.id):
                diagnostics.append(Diagnostic("BLOCKED_DEPENDENCY", question.why_it_matters, question.id, Severity.BLOCKING, False))

        metric_map = {metric.id: metric for sheet in workbook.sheets for metric in sheet.metrics}
        metric_ids = set(metric_map)
        metric_owners = {metric.id: sheet.id for sheet in workbook.sheets for metric in sheet.metrics}
        diagnostics.extend(self._validate_dependency_graph(workbook, metric_owners))
        for join in workbook.joins:
            diagnostics.extend(self._validate_join(join))

        for sheet in workbook.sheets:
            dimensions = {item.id: item for item in sheet.dimensions}
            bindings = {binding.id: binding for dimension in sheet.dimensions for binding in dimension.bindings}
            sheet_metric_ids = {item.id for item in sheet.metrics}
            if not sheet.metrics:
                diagnostics.append(Diagnostic("EMPTY_SHEET_METRICS", "sheet must contain at least one executable metric", sheet.id))
            if sheet.target_grain != tuple(item.id for item in sheet.dimensions):
                diagnostics.append(Diagnostic(
                    "INVALID_CANONICAL_GRAIN",
                    "sheet target_grain must list canonical dimension ids once and in dimension order",
                    sheet.id,
                ))
            if sheet.limit is not None:
                if isinstance(sheet.limit, bool) or not isinstance(sheet.limit, int) or not 1 <= sheet.limit <= 10000:
                    diagnostics.append(Diagnostic("INVALID_ROW_LIMIT", "sheet limit must be an integer from 1 to 10000", sheet.id))
                if not sheet.order_by:
                    diagnostics.append(Diagnostic("UNORDERED_ROW_LIMIT", "sheet limit requires an explicit deterministic order_by", sheet.id))
            suppression_values = (sheet.minimum_group_size, sheet.group_count_metric_id)
            if (suppression_values[0] is None) != (suppression_values[1] is None):
                diagnostics.append(Diagnostic(
                    "INCOMPLETE_GROUP_SUPPRESSION",
                    "minimum_group_size and group_count_metric_id must be declared together",
                    sheet.id,
                ))
            elif sheet.minimum_group_size is not None:
                if (
                    isinstance(sheet.minimum_group_size, bool)
                    or not isinstance(sheet.minimum_group_size, int)
                    or not 2 <= sheet.minimum_group_size <= 10000
                ):
                    diagnostics.append(Diagnostic(
                        "INVALID_MINIMUM_GROUP_SIZE",
                        "minimum_group_size must be an integer from 2 to 10000",
                        sheet.id,
                    ))
                count_metric = metric_map.get(sheet.group_count_metric_id or "")
                count_operations = {
                    Operation.COUNT_ROWS, Operation.COUNT_NONEMPTY, Operation.COUNT_DISTINCT,
                }
                if (
                    sheet.group_count_metric_id not in sheet_metric_ids
                    or count_metric is None
                    or count_metric.operation not in count_operations
                    or count_metric.unit not in {MetricUnit.COUNT, MetricUnit.UNKNOWN}
                ):
                    diagnostics.append(Diagnostic(
                        "INVALID_GROUP_COUNT_METRIC",
                        "group suppression requires a count metric from the same sheet",
                        sheet.id,
                    ))
            order_column_ids = [item.column_id for item in sheet.order_by]
            if len(order_column_ids) != len(set(order_column_ids)):
                diagnostics.append(Diagnostic("DUPLICATE_SORT_COLUMN", "order_by columns must be unique", sheet.id))
            output_column_ids = set(sheet.target_grain) | sheet_metric_ids
            for order in sheet.order_by:
                if order.column_id not in output_column_ids:
                    diagnostics.append(Diagnostic("UNKNOWN_SORT_COLUMN", f"order_by references unknown output column {order.column_id}", sheet.id))
            for dimension in sheet.dimensions:
                diagnostics.extend(self._validate_dimension(dimension, questions))
            for metric in sheet.metrics:
                diagnostics.extend(self._validate_metric(
                    metric,
                    sheet.target_grain,
                    dimensions,
                    bindings,
                    workbook.joins,
                    metric_ids,
                    metric_map,
                    questions,
                ))
        return self._deduplicate(diagnostics)

    def _validate_dimension(self, dimension: DimensionIntent, questions: dict) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        if not dimension.bindings:
            return [Diagnostic("MISSING_DIMENSION_BINDING", "canonical dimension requires at least one source binding", dimension.id)]
        for binding in dimension.bindings:
            if binding.source_sheet not in self.evidence.schemas:
                issues.append(Diagnostic("UNKNOWN_SOURCE_SHEET", f"unknown dimension source {binding.source_sheet}", binding.id))
                continue
            if not binding.source_fields:
                issues.append(Diagnostic("EMPTY_DIMENSION_INPUT", "dimension binding must bind real source fields", binding.id))
            for field in binding.source_fields:
                if not self.evidence.field_exists(binding.source_sheet, field):
                    issues.append(Diagnostic("UNKNOWN_SOURCE_FIELD", f"unknown dimension field {binding.source_sheet}.{field}", binding.id))
            if not binding.evidence_refs:
                issues.append(Diagnostic("MISSING_EVIDENCE", "dimension binding must cite local evidence", binding.id))
            for evidence_id in binding.evidence_refs:
                if not self.evidence.trusted(evidence_id):
                    issues.append(Diagnostic("UNTRUSTED_EVIDENCE", f"dimension evidence {evidence_id} is not trusted local evidence", binding.id, repairable=False))
            issues.extend(self._validate_transform(binding))
            if (
                binding.transform.kind in {TransformKind.NUMERIC_BUCKET, TransformKind.CATEGORICAL_RULE}
                and not binding.required_decision_ids
            ):
                issues.append(Diagnostic(
                    "UNCONFIRMED_BUSINESS_BUCKET",
                    "business bucket boundaries or grouping rules require an explicit user decision",
                    binding.id,
                ))
            issues.extend(self._validate_required_decisions(binding.id, binding.required_decision_ids, questions))
        return issues

    @staticmethod
    def _validate_transform(binding: DimensionBinding) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        kind = binding.transform.kind
        params = binding.transform.parameters
        if len(binding.source_fields) != 1:
            issues.append(Diagnostic("INVALID_TRANSFORM_INPUT", f"{kind.value} currently requires exactly one source field", binding.id))
            return issues
        if kind == TransformKind.IDENTITY:
            if params:
                issues.append(Diagnostic("INVALID_TRANSFORM_PARAMS", "identity transform must not contain parameters", binding.id))
        elif kind == TransformKind.DATE_BUCKET:
            if params.get("grain") not in {"day", "week", "month", "quarter", "year"}:
                issues.append(Diagnostic("INVALID_DATE_BUCKET", "date_bucket requires an explicit day/week/month/quarter/year grain", binding.id))
        elif kind == TransformKind.NUMERIC_BUCKET:
            boundaries = params.get("boundaries")
            labels = params.get("labels")
            if (
                not isinstance(boundaries, list)
                or not boundaries
                or any(not isinstance(value, (int, float)) for value in boundaries)
                or boundaries != sorted(set(boundaries))
            ):
                issues.append(Diagnostic("INVALID_NUMERIC_BUCKET", "numeric_bucket boundaries must be explicit, numeric, unique and increasing", binding.id))
            if not isinstance(labels, list) or not isinstance(boundaries, list) or len(labels) != len(boundaries) + 1:
                issues.append(Diagnostic("INVALID_NUMERIC_BUCKET", "numeric_bucket requires one label per interval", binding.id))
            if params.get("closed") not in {"left", "right"}:
                issues.append(Diagnostic("INVALID_NUMERIC_BUCKET", "numeric_bucket must declare left or right closed intervals", binding.id))
        elif kind == TransformKind.CATEGORICAL_RULE:
            rules = params.get("rules")
            allowed = {"equals", "in", "range", "is_empty", "not_empty"}
            if not isinstance(rules, list) or not rules:
                issues.append(Diagnostic("INVALID_CATEGORICAL_RULE", "categorical_rule requires explicit ordered rules", binding.id))
            else:
                for rule in rules:
                    if not isinstance(rule, dict) or not rule.get("label") or rule.get("operator") not in allowed:
                        issues.append(Diagnostic("INVALID_CATEGORICAL_RULE", "every categorical rule needs a label and supported operator", binding.id))
                        break
                    operator = rule["operator"]
                    if operator == "equals" and "value" not in rule:
                        issues.append(Diagnostic("INVALID_CATEGORICAL_RULE", "equals rule requires value", binding.id))
                        break
                    if operator == "in" and (not isinstance(rule.get("values"), list) or not rule["values"]):
                        issues.append(Diagnostic("INVALID_CATEGORICAL_RULE", "in rule requires a non-empty values list", binding.id))
                        break
                    if operator == "range" and (
                        not isinstance(rule.get("minimum"), (int, float))
                        or not isinstance(rule.get("maximum"), (int, float))
                        or rule["minimum"] > rule["maximum"]
                        or not isinstance(rule.get("include_minimum"), bool)
                        or not isinstance(rule.get("include_maximum"), bool)
                    ):
                        issues.append(Diagnostic("INVALID_CATEGORICAL_RULE", "range rule requires explicit ordered bounds and inclusivity", binding.id))
                        break
            if "default_label" not in params:
                issues.append(Diagnostic("INVALID_CATEGORICAL_RULE", "categorical_rule requires an explicit default_label", binding.id))
        return issues

    def _validate_join(self, join: JoinIntent) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        if not join.left_keys or len(join.left_keys) != len(join.right_keys):
            return [Diagnostic("INVALID_JOIN_KEYS", "join keys must be non-empty and paired", join.id)]
        for field in join.left_keys:
            if not self.evidence.field_exists(join.left_sheet, field):
                issues.append(Diagnostic("UNKNOWN_SOURCE_FIELD", f"unknown join field {join.left_sheet}.{field}", join.id))
        for field in join.right_keys:
            if not self.evidence.field_exists(join.right_sheet, field):
                issues.append(Diagnostic("UNKNOWN_SOURCE_FIELD", f"unknown join field {join.right_sheet}.{field}", join.id))
        valid, reason = self.evidence.verify_join(join)
        if not valid:
            issues.append(Diagnostic("UNVERIFIED_JOIN", reason, join.id, repairable=False))
        if join.cardinality == JoinCardinality.MANY_TO_MANY:
            issues.append(Diagnostic("UNSAFE_MANY_TO_MANY", "fact-to-fact many-to-many joins are not executable at raw grain", join.id, repairable=False))
        unique_side: tuple[str, tuple[str, ...]] | None = None
        if join.cardinality == JoinCardinality.MANY_TO_ONE:
            unique_side = (join.right_sheet, join.right_keys)
        elif join.cardinality == JoinCardinality.ONE_TO_MANY:
            unique_side = (join.left_sheet, join.left_keys)
        elif join.cardinality == JoinCardinality.ONE_TO_ONE:
            for sheet, keys in ((join.left_sheet, join.left_keys), (join.right_sheet, join.right_keys)):
                if self.evidence.uniqueness(sheet, keys, (join.evidence_ref,)) is not True:
                    issues.append(Diagnostic("MISSING_UNIQUENESS_EVIDENCE", f"join side {sheet}{keys} is not proven unique", join.id, repairable=False))
        if unique_side and self.evidence.uniqueness(*unique_side, (join.evidence_ref,)) is not True:
            issues.append(Diagnostic("MISSING_UNIQUENESS_EVIDENCE", f"lookup side {unique_side[0]}{unique_side[1]} is not proven unique", join.id, repairable=False))
        return issues

    def _validate_metric(
        self,
        metric: MetricIntent,
        sheet_grain: tuple[str, ...],
        dimensions: dict[str, DimensionIntent],
        bindings: dict[str, DimensionBinding],
        joins: tuple[JoinIntent, ...],
        metric_ids: set[str],
        metric_map: dict[str, MetricIntent],
        questions: dict,
    ) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        if metric.target_grain != sheet_grain:
            issues.append(Diagnostic("TARGET_GRAIN_MISMATCH", "metric target grain must equal the canonical sheet grain", metric.id))
        for evidence_id in metric.evidence_refs:
            if not self.evidence.trusted(evidence_id):
                issues.append(Diagnostic("UNTRUSTED_EVIDENCE", f"evidence {evidence_id} is absent or not produced by a verified local tool", metric.id, repairable=False))
        if not metric.evidence_refs:
            issues.append(Diagnostic("MISSING_EVIDENCE", "metric must cite local evidence", metric.id))

        selected_bindings: list[DimensionBinding] = []
        if metric.operation == Operation.DERIVED:
            if metric.source_sheet is not None or metric.source_fields or metric.source_grain or metric.entity_key or metric.dimension_binding_ids:
                issues.append(Diagnostic("INVALID_DERIVED_SOURCE", "derived metrics consume metric ids and inherit canonical grain", metric.id))
            if not metric.dependencies or set(metric.dependencies) - metric_ids:
                issues.append(Diagnostic("INVALID_DERIVED_DEPENDENCY", "derived dependencies must identify existing metrics", metric.id))
            if not metric.expression or tuple(metric.expression.operands) != metric.dependencies:
                issues.append(Diagnostic("INVALID_DERIVED_EXPRESSION", "derived expression must explicitly bind all dependencies", metric.id))
            elif metric.expression:
                issues.extend(self._validate_derived_expression(metric))
            if metric.composition == Composition.RATIO_OF_SUMS and (
                not metric.expression or metric.expression.operator != "divide" or len(metric.dependencies) != 2
            ):
                issues.append(Diagnostic("INVALID_RATIO_OF_SUMS", "ratio_of_sums requires an explicit divide of exactly two additive metrics", metric.id))
            elif metric.composition == Composition.RATIO_OF_SUMS:
                operands = [metric_map.get(item) for item in metric.dependencies]
                if any(
                    item is None
                    or item.additivity != Additivity.ADDITIVE
                    or item.composition != Composition.SUM_PARTIALS
                    for item in operands
                ):
                    issues.append(Diagnostic(
                        "INVALID_RATIO_OF_SUMS",
                        "ratio_of_sums operands must be additive metrics composed from summed partials",
                        metric.id,
                    ))
                if metric.additivity != Additivity.NON_ADDITIVE:
                    issues.append(Diagnostic("INVALID_RATIO_OF_SUMS", "ratio_of_sums must be declared non-additive", metric.id))
        else:
            if not metric.source_sheet or metric.source_sheet not in self.evidence.schemas:
                issues.append(Diagnostic("UNKNOWN_SOURCE_SHEET", f"unknown source sheet {metric.source_sheet}", metric.id))
                return issues
            for field in (*metric.source_fields, *metric.source_grain, *metric.entity_key):
                if not self.evidence.field_exists(metric.source_sheet, field):
                    issues.append(Diagnostic("UNKNOWN_SOURCE_FIELD", f"unknown field {metric.source_sheet}.{field}", metric.id))
            if len(metric.dimension_binding_ids) != len(metric.target_grain):
                issues.append(Diagnostic("INVALID_DIMENSION_BINDINGS", "every canonical target dimension needs one explicit binding", metric.id))
            else:
                for dimension_id, binding_id in zip(metric.target_grain, metric.dimension_binding_ids):
                    dimension = dimensions.get(dimension_id)
                    binding = bindings.get(binding_id)
                    if not dimension or not binding or binding not in dimension.bindings:
                        issues.append(Diagnostic("INVALID_DIMENSION_BINDING", f"binding {binding_id} does not implement canonical dimension {dimension_id}", metric.id))
                    else:
                        selected_bindings.append(binding)

        issues.extend(self._validate_semantics(metric))
        issues.extend(self._validate_metric_contract(metric, metric_map))
        issues.extend(self._validate_required_decisions(metric.id, metric.required_decision_ids, questions))

        if metric.operation != Operation.DERIVED and len(selected_bindings) == len(metric.target_grain):
            target_sheets = {item.source_sheet for item in selected_bindings if item.source_sheet != metric.source_sheet}
            steps, tree_error = build_enrichment_tree(metric.source_sheet or "", target_sheets, joins)
            if tree_error:
                issues.append(Diagnostic("INVALID_ENRICHMENT_TREE", tree_error, metric.id))
            for target_sheet in target_sheets:
                if not find_safe_enrichment_path(metric.source_sheet or "", target_sheet, joins):
                    issues.append(Diagnostic("MISSING_JOIN_PATH", f"no declared evidence-backed many-to-one path from {metric.source_sheet} to {target_sheet}", metric.id))
            for step in steps:
                if step.from_sheet == metric.source_sheet and not set(step.from_keys) <= set(metric.source_grain):
                    issues.append(Diagnostic("JOIN_KEY_NOT_RETAINED", f"source grain must retain enrichment keys {step.from_keys}", metric.id))

        if metric.uniqueness_required or metric.operation == Operation.VALUE:
            unique = self.evidence.uniqueness(metric.source_sheet or "", metric.source_grain, metric.evidence_refs)
            if unique is False:
                issues.append(Diagnostic("FALSE_UNIQUENESS_CLAIM", "local evidence disproves the declared source grain uniqueness", metric.id, repairable=False))
            elif unique is None:
                issues.append(Diagnostic("MISSING_UNIQUENESS_EVIDENCE", "value semantics require local uniqueness evidence", metric.id, repairable=False))

        if metric.operation == Operation.VALUE and selected_bindings:
            direct_identity = all(
                binding.source_sheet == metric.source_sheet
                and binding.transform.kind == TransformKind.IDENTITY
                for binding in selected_bindings
            )
            direct_identity_fields = {
                field for binding in selected_bindings for field in binding.source_fields
            }
            if not direct_identity or not set(metric.source_grain) <= direct_identity_fields:
                issues.append(Diagnostic("NON_COMPOSABLE_ROLLUP", "a value cannot roll from a finer source grain to a broader or transformed canonical grain", metric.id))
        return issues

    @staticmethod
    def _validate_derived_expression(metric: MetricIntent) -> list[Diagnostic]:
        assert metric.expression is not None
        expression = metric.expression
        operator = expression.operator
        operands = expression.operands
        parameters = expression.parameters
        issues: list[Diagnostic] = []
        if operator in {"divide", "subtract"}:
            valid = len(operands) == 2 and not parameters
        elif operator in {"add", "multiply"}:
            valid = len(operands) >= 2 and not parameters
        elif operator in {"add_constant", "subtract_constant"}:
            valid = len(operands) == 1 and set(parameters) == {"value"} and isinstance(parameters.get("value"), (int, float))
        elif operator == "multiply_constant":
            valid = len(operands) == 1 and set(parameters) == {"factor"} and isinstance(parameters.get("factor"), (int, float))
        elif operator == "divide_constant":
            valid = (
                len(operands) == 1
                and set(parameters) == {"divisor"}
                and isinstance(parameters.get("divisor"), (int, float))
                and parameters.get("divisor") != 0
            )
        else:
            return [Diagnostic("UNSUPPORTED_DERIVED_OPERATOR", f"unsupported deterministic derived operator {operator}", metric.id)]
        if not valid:
            issues.append(Diagnostic("INVALID_DERIVED_PARAMETERS", f"operator {operator} has invalid operands or parameters", metric.id))
        return issues

    @staticmethod
    def _validate_metric_contract(
        metric: MetricIntent,
        metric_map: dict[str, MetricIntent],
    ) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        if not metric.business_definition.strip():
            issues.append(Diagnostic(
                "MISSING_BUSINESS_DEFINITION",
                "legacy metric has no business definition; newly modeled metrics must declare one",
                metric.id, Severity.WARNING,
            ))
        count_operations = {
            Operation.COUNT_ROWS, Operation.COUNT_NONEMPTY, Operation.COUNT_DISTINCT,
        }
        if metric.operation in count_operations and metric.unit not in {MetricUnit.COUNT, MetricUnit.UNKNOWN}:
            issues.append(Diagnostic("INVALID_METRIC_UNIT", "count operations require unit=count", metric.id))
        if metric.operation == Operation.DERIVED and metric.risk_level == RiskLevel.LOW:
            issues.append(Diagnostic("INVALID_RISK_LEVEL", "derived metrics must be medium or high risk", metric.id))
        if metric.operation != Operation.DERIVED or not metric.expression:
            if metric.unit == MetricUnit.UNKNOWN:
                issues.append(Diagnostic(
                    "UNKNOWN_SOURCE_UNIT",
                    "source metric unit is unknown; dimensional checks are limited",
                    metric.id, Severity.WARNING,
                ))
            return issues
        dependencies = [metric_map.get(item) for item in metric.dependencies]
        if any(item is None for item in dependencies):
            return issues
        units = [item.unit for item in dependencies if item is not None]
        if metric.unit == MetricUnit.UNKNOWN or any(item == MetricUnit.UNKNOWN for item in units):
            issues.append(Diagnostic(
                "UNKNOWN_DERIVED_UNIT",
                "legacy derived metric has unknown units; newly modeled metrics must declare every operand and result unit",
                metric.id, Severity.WARNING,
            ))
            return issues
        operator = metric.expression.operator
        ratio_units = {MetricUnit.RATIO, MetricUnit.PERCENTAGE}
        if operator in {"add", "subtract"}:
            if len(set(units)) != 1 or metric.unit != units[0]:
                issues.append(Diagnostic("DIMENSIONAL_MISMATCH", "add/subtract operands and result must use the same unit", metric.id))
        elif operator == "divide":
            if units[0] == units[1] and metric.unit not in ratio_units:
                issues.append(Diagnostic("DIMENSIONAL_MISMATCH", "same-unit division must produce ratio or percentage", metric.id))
        elif operator == "multiply":
            non_ratio = [item for item in units if item not in ratio_units]
            if len(non_ratio) == 1 and metric.unit != non_ratio[0]:
                issues.append(Diagnostic("DIMENSIONAL_MISMATCH", "multiplying by a ratio must preserve the other operand unit", metric.id))
        elif operator in {"add_constant", "subtract_constant", "multiply_constant", "divide_constant"} and metric.unit != units[0]:
            issues.append(Diagnostic("DIMENSIONAL_MISMATCH", "scalar operations must preserve the operand unit", metric.id))
        return issues

    def _validate_required_decisions(self, node_id: str, decision_ids: tuple[str, ...], questions: dict) -> list[Diagnostic]:
        issues = []
        for decision_id in decision_ids:
            if self.decisions.is_confirmed(decision_id):
                continue
            question = questions.get(decision_id)
            if not question or node_id not in question.affected_node_ids or not question.blocking:
                issues.append(Diagnostic(
                    "MISSING_BLOCKING_QUESTION",
                    f"unconfirmed decision {decision_id} needs a blocking question bound to this node",
                    node_id,
                    Severity.ERROR,
                    True,
                ))
        return issues

    @staticmethod
    def _validate_semantics(metric: MetricIntent) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        expected = {
            Operation.SUM: (MetricSemantic.ADDITIVE_MEASURE, Additivity.ADDITIVE, Composition.SUM_PARTIALS),
            Operation.COUNT_ROWS: (MetricSemantic.RECORD_COUNT, Additivity.ADDITIVE, Composition.SUM_PARTIALS),
            Operation.COUNT_NONEMPTY: (MetricSemantic.RECORD_COUNT, Additivity.ADDITIVE, Composition.SUM_PARTIALS),
            Operation.COUNT_DISTINCT: (MetricSemantic.ENTITY_COUNT, Additivity.NON_ADDITIVE, Composition.DISTINCT_SET),
            Operation.AVERAGE: (MetricSemantic.NON_ADDITIVE_MEASURE, Additivity.NON_ADDITIVE, Composition.WEIGHTED_AVERAGE),
            Operation.MIN: (MetricSemantic.NON_ADDITIVE_MEASURE, Additivity.SEMI_ADDITIVE, Composition.MINMAX),
            Operation.MAX: (MetricSemantic.NON_ADDITIVE_MEASURE, Additivity.SEMI_ADDITIVE, Composition.MINMAX),
            Operation.VALUE: (MetricSemantic.ATTRIBUTE_VALUE, Additivity.NON_ADDITIVE, Composition.RECOMPUTE),
        }
        if metric.operation == Operation.DERIVED:
            if metric.semantic != MetricSemantic.DERIVED_MEASURE or metric.composition not in {Composition.RATIO_OF_SUMS, Composition.RECOMPUTE}:
                issues.append(Diagnostic("INVALID_COMPOSITION", "derived metric semantics and composition must be explicit", metric.id))
        else:
            semantic, additivity, composition = expected[metric.operation]
            if (metric.semantic, metric.additivity, metric.composition) != (semantic, additivity, composition):
                issues.append(Diagnostic("INVALID_COMPOSITION", f"{metric.operation.value} requires {semantic.value}/{additivity.value}/{composition.value}", metric.id))

        if metric.operation == Operation.COUNT_ROWS:
            if metric.source_fields or metric.entity_key:
                issues.append(Diagnostic("ROW_COUNT_HAS_ENTITY", "count_rows counts records and cannot bind an entity key", metric.id))
        elif metric.operation == Operation.COUNT_NONEMPTY:
            if len(metric.source_fields) != 1 or metric.entity_key:
                issues.append(Diagnostic("INVALID_COUNT_NONEMPTY", "count_nonempty requires exactly one field and no entity key", metric.id))
        elif metric.operation == Operation.COUNT_DISTINCT:
            if not metric.entity_key or not set(metric.entity_key) <= set(metric.source_grain):
                issues.append(Diagnostic("INVALID_ENTITY_COUNT", "entity counts require an explicit entity key retained in source grain", metric.id))
            if metric.source_fields:
                issues.append(Diagnostic("INVALID_ENTITY_COUNT", "count_distinct uses entity_key, not source_fields", metric.id))
        elif metric.operation in {Operation.SUM, Operation.AVERAGE, Operation.MIN, Operation.MAX, Operation.VALUE}:
            if len(metric.source_fields) != 1:
                issues.append(Diagnostic("INVALID_MEASURE_BINDING", f"{metric.operation.value} requires exactly one real source field", metric.id))
        return issues

    @staticmethod
    def _validate_dependency_graph(workbook: WorkbookIntentIR, owners: dict[str, str]) -> list[Diagnostic]:
        issues: list[Diagnostic] = []
        graph = {metric.id: metric.dependencies for sheet in workbook.sheets for metric in sheet.metrics}
        for metric_id, dependencies in graph.items():
            for dependency in dependencies:
                if dependency in owners and owners[dependency] != owners[metric_id]:
                    issues.append(Diagnostic("CROSS_SHEET_DERIVED_DEPENDENCY", "derived metrics may only depend on metrics at the same canonical grain", metric_id))
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(metric_id: str) -> None:
            if metric_id in visiting:
                issues.append(Diagnostic("CYCLIC_METRIC_DEPENDENCY", "metric dependency graph contains a cycle", metric_id))
                return
            if metric_id in visited:
                return
            visiting.add(metric_id)
            for dependency in graph.get(metric_id, ()):
                if dependency in graph:
                    visit(dependency)
            visiting.remove(metric_id)
            visited.add(metric_id)

        for metric_id in graph:
            visit(metric_id)
        return issues

    @staticmethod
    def _node_ids(workbook: WorkbookIntentIR) -> list[str]:
        return [
            workbook.id,
            *(join.id for join in workbook.joins),
            *(question.id for question in workbook.clarifications),
            *(sheet.id for sheet in workbook.sheets),
            *(dimension.id for sheet in workbook.sheets for dimension in sheet.dimensions),
            *(binding.id for sheet in workbook.sheets for dimension in sheet.dimensions for binding in dimension.bindings),
            *(metric.id for sheet in workbook.sheets for metric in sheet.metrics),
        ]

    @staticmethod
    def _deduplicate(items: Iterable[Diagnostic]) -> list[Diagnostic]:
        seen: set[tuple[str, str, str]] = set()
        result = []
        for item in items:
            key = (item.code, item.node_id, item.message)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


def find_safe_enrichment_path(source: str, target: str, joins: Iterable[JoinIntent]) -> tuple[tuple[JoinIntent, bool], ...]:
    if source == target:
        return ()
    adjacency: dict[str, list[tuple[str, JoinIntent, bool]]] = {}
    for join in joins:
        if join.cardinality in {JoinCardinality.MANY_TO_ONE, JoinCardinality.ONE_TO_ONE}:
            adjacency.setdefault(join.left_sheet, []).append((join.right_sheet, join, False))
        if join.cardinality in {JoinCardinality.ONE_TO_MANY, JoinCardinality.ONE_TO_ONE}:
            adjacency.setdefault(join.right_sheet, []).append((join.left_sheet, join, True))
    queue: list[tuple[str, tuple[tuple[JoinIntent, bool], ...]]] = [(source, ())]
    visited = {source}
    while queue:
        current, path = queue.pop(0)
        for next_sheet, join, reversed_edge in adjacency.get(current, []):
            next_path = (*path, (join, reversed_edge))
            if next_sheet == target:
                return next_path
            if next_sheet not in visited:
                visited.add(next_sheet)
                queue.append((next_sheet, next_path))
    return ()


def build_enrichment_tree(
    source: str,
    targets: Iterable[str],
    joins: Iterable[JoinIntent],
) -> tuple[tuple[EnrichmentStep, ...], str | None]:
    edges: dict[tuple[str, bool], EnrichmentStep] = {}
    parents: dict[str, str] = {}
    for target in sorted(set(targets)):
        path = find_safe_enrichment_path(source, target, joins)
        if not path:
            continue
        for join, reversed_edge in path:
            step = EnrichmentStep(
                join=join,
                reversed_edge=reversed_edge,
                from_sheet=join.right_sheet if reversed_edge else join.left_sheet,
                to_sheet=join.left_sheet if reversed_edge else join.right_sheet,
                from_keys=join.right_keys if reversed_edge else join.left_keys,
                to_keys=join.left_keys if reversed_edge else join.right_keys,
            )
            prior_parent = parents.get(step.to_sheet)
            if prior_parent and prior_parent != step.from_sheet:
                return (), f"ambiguous enrichment tree: {step.to_sheet} has parents {prior_parent} and {step.from_sheet}"
            parents[step.to_sheet] = step.from_sheet
            edges[(join.id, reversed_edge)] = step

    attached = {source}
    pending = list(edges.values())
    ordered: list[EnrichmentStep] = []
    while pending:
        ready = [step for step in pending if step.from_sheet in attached]
        if not ready:
            return (), "enrichment paths cannot be ordered from the metric source"
        for step in ready:
            pending.remove(step)
            if step.to_sheet not in attached:
                ordered.append(step)
                attached.add(step.to_sheet)
    return tuple(ordered), None

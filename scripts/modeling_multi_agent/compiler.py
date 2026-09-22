from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .evidence import EvidenceStore
from .ir import (
    DecisionLedger,
    DimensionBinding,
    MetricIntent,
    Operation,
    SheetIntent,
    WorkbookIntentIR,
)
from .validator import (
    AlgebraicInvariantValidator,
    Diagnostic,
    Severity,
    build_enrichment_tree,
)


class CompilationStatus(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    INVALID = "invalid"


@dataclass(frozen=True)
class PlanNode:
    id: str
    kind: str
    inputs: tuple[str, ...]
    output_grain: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionPlan:
    workbook_id: str
    nodes: tuple[PlanNode, ...]
    sheet_outputs: dict[str, str]


@dataclass(frozen=True)
class CompilationResult:
    status: CompilationStatus
    plan: ExecutionPlan | None
    diagnostics: tuple[Diagnostic, ...]


class PlanCompiler:
    def __init__(self, evidence: EvidenceStore, decisions: DecisionLedger | None = None) -> None:
        self.evidence = evidence
        self.decisions = decisions or DecisionLedger()
        self.validator = AlgebraicInvariantValidator(evidence, self.decisions)

    def compile(self, workbook: WorkbookIntentIR) -> CompilationResult:
        diagnostics = self.validator.validate(workbook)
        errors = [item for item in diagnostics if item.severity == Severity.ERROR]
        blockers = [item for item in diagnostics if item.severity == Severity.BLOCKING]
        if errors:
            return CompilationResult(CompilationStatus.INVALID, None, tuple(diagnostics))
        if blockers:
            return CompilationResult(CompilationStatus.BLOCKED, None, tuple(diagnostics))

        nodes: list[PlanNode] = []
        sheet_outputs: dict[str, str] = {}
        metric_nodes: dict[str, str] = {}
        for sheet in workbook.sheets:
            branch_outputs: list[str] = []
            derived_metrics: list[MetricIntent] = []
            for metric in sheet.metrics:
                if metric.operation == Operation.DERIVED:
                    derived_metrics.append(metric)
                    continue
                output = self._compile_source_metric(metric, sheet, workbook, nodes)
                metric_nodes[metric.id] = output
                branch_outputs.append(output)

            merge_id = f"{sheet.id}__merge"
            nodes.append(PlanNode(
                merge_id,
                "merge_metric_branches",
                tuple(branch_outputs),
                sheet.target_grain,
                {
                    "join_type": "full",
                    "canonical_keys": sheet.target_grain,
                    "inputs_already_unique_on_canonical_grain": True,
                },
            ))
            current = merge_id
            pending = list(derived_metrics)
            ordered_derived: list[MetricIntent] = []
            while pending:
                ready = [metric for metric in pending if set(metric.dependencies) <= set(metric_nodes)]
                if not ready:
                    raise RuntimeError("validated metric dependency graph could not be topologically compiled")
                for metric in ready:
                    pending.remove(metric)
                    ordered_derived.append(metric)
                    metric_nodes[metric.id] = "__pending__"
            for metric in ordered_derived:
                derive_id = f"{metric.id}__derive"
                dependency_nodes = tuple(metric_nodes[item] for item in metric.dependencies)
                nodes.append(PlanNode(
                    derive_id,
                    "derive_metric",
                    (current, *dependency_nodes),
                    sheet.target_grain,
                    {
                        "metric_id": metric.id,
                        "operator": metric.expression.operator if metric.expression else None,
                        "operands": metric.dependencies,
                        "composition": metric.composition.value,
                        "zero_division": metric.expression.zero_division if metric.expression else None,
                        "parameters": metric.expression.parameters if metric.expression else {},
                    },
                    metric.evidence_refs,
                ))
                metric_nodes[metric.id] = derive_id
                current = derive_id
            output_id = f"{sheet.id}__output"
            nodes.append(PlanNode(
                output_id,
                "project_sheet",
                (current,),
                sheet.target_grain,
                {
                    "title": sheet.title,
                    "metric_ids": tuple(item.id for item in sheet.metrics),
                    "dimension_names": {item.id: item.name for item in sheet.dimensions},
                    "metric_names": {item.id: item.name for item in sheet.metrics},
                    "order_by": tuple(
                        {"column_id": item.column_id, "direction": item.direction.value}
                        for item in sheet.order_by
                    ),
                    "limit": sheet.limit,
                    "minimum_group_size": sheet.minimum_group_size,
                    "group_count_metric_id": sheet.group_count_metric_id,
                },
            ))
            sheet_outputs[sheet.id] = output_id
        return CompilationResult(
            CompilationStatus.READY,
            ExecutionPlan(workbook.id, tuple(nodes), sheet_outputs),
            tuple(diagnostics),
        )

    def _compile_source_metric(
        self,
        metric: MetricIntent,
        sheet: SheetIntent,
        workbook: WorkbookIntentIR,
        nodes: list[PlanNode],
    ) -> str:
        assert metric.source_sheet is not None
        binding_map = {
            binding.id: binding
            for dimension in sheet.dimensions
            for binding in dimension.bindings
        }
        selected: list[tuple[str, DimensionBinding]] = [
            (dimension_id, binding_map[binding_id])
            for dimension_id, binding_id in zip(metric.target_grain, metric.dimension_binding_ids)
        ]
        external_sheets = {
            binding.source_sheet
            for _, binding in selected
            if binding.source_sheet != metric.source_sheet
        }
        enrichment_steps, error = build_enrichment_tree(metric.source_sheet, external_sheets, workbook.joins)
        if error:
            raise RuntimeError(f"validated enrichment tree failed compilation: {error}")

        required_fields = set(metric.source_fields) | set(metric.source_grain) | set(metric.entity_key)
        direct_dimensions = [
            (dimension_id, binding)
            for dimension_id, binding in selected
            if binding.source_sheet == metric.source_sheet
        ]
        for _, binding in direct_dimensions:
            required_fields.update(binding.source_fields)
        for step in enrichment_steps:
            if step.from_sheet == metric.source_sheet:
                required_fields.update(step.from_keys)

        scan_id = f"{metric.id}__scan"
        source_grain = tuple(f"{metric.source_sheet}.{field}" for field in metric.source_grain)
        nodes.append(PlanNode(
            scan_id,
            "scan_source",
            (),
            source_grain,
            {
                "source_sheet": metric.source_sheet,
                "fields": tuple(sorted(required_fields)),
                "field_provenance": {
                    field: f"{metric.source_sheet}.{field}"
                    for field in sorted(required_fields)
                },
            },
            metric.evidence_refs,
        ))
        current = scan_id
        direct_canonical_ids: list[str] = []
        for dimension_id, binding in direct_dimensions:
            direct_canonical_ids.append(dimension_id)
            current = self._append_dimension_node(
                metric,
                dimension_id,
                binding,
                current,
                (*source_grain, *direct_canonical_ids),
                nodes,
            )

        partial_name = f"__metric_{metric.id}_partial"
        preaggregate_id = f"{metric.id}__preaggregate"
        partial_params: dict[str, Any] = {
            "metric_id": metric.id,
            "operation": metric.operation.value,
            "source_fields": metric.source_fields,
            "entity_key": metric.entity_key,
            "source_grain": metric.source_grain,
            "canonical_dimensions_retained": tuple(direct_canonical_ids),
            "partial_column": partial_name,
        }
        if metric.operation == Operation.AVERAGE:
            partial_params["partial_columns"] = (f"{partial_name}__sum", f"{partial_name}__count")
        if metric.operation == Operation.COUNT_DISTINCT:
            partial_params["retain_distinct_keys"] = metric.entity_key
        preaggregate_grain = (*source_grain, *direct_canonical_ids)
        nodes.append(PlanNode(
            preaggregate_id,
            "pre_aggregate_to_source_grain",
            (current,),
            preaggregate_grain,
            partial_params,
            metric.evidence_refs,
        ))
        current = preaggregate_id

        for index, step in enumerate(enrichment_steps, start=1):
            downstream_keys = {
                field
                for later in enrichment_steps
                if later.from_sheet == step.to_sheet
                for field in later.from_keys
            }
            dimension_fields = {
                field
                for _, binding in selected
                if binding.source_sheet == step.to_sheet
                for field in binding.source_fields
            }
            projection = tuple(sorted(set(step.to_keys) | downstream_keys | dimension_fields))
            join_id = f"{metric.id}__enrich_{index}"
            nodes.append(PlanNode(
                join_id,
                "enrich_many_to_one",
                (current,),
                preaggregate_grain,
                {
                    "join_intent_id": step.join.id,
                    "from_sheet": step.from_sheet,
                    "to_sheet": step.to_sheet,
                    "from_keys": step.from_keys,
                    "to_keys": step.to_keys,
                    "projection": projection,
                    "projection_provenance": {
                        field: f"{step.to_sheet}.{field}"
                        for field in projection
                    },
                    "preserve_source_rows": True,
                },
                (step.join.evidence_ref,),
            ))
            current = join_id

        derived_canonical_ids = list(direct_canonical_ids)
        for dimension_id, binding in selected:
            if binding.source_sheet != metric.source_sheet:
                derived_canonical_ids.append(dimension_id)
                current = self._append_dimension_node(
                    metric,
                    dimension_id,
                    binding,
                    current,
                    (*preaggregate_grain, *derived_canonical_ids[len(direct_canonical_ids):]),
                    nodes,
                )

        rollup_id = f"{metric.id}__rollup"
        rollup_operation = {
            Operation.SUM: "sum_partials",
            Operation.COUNT_ROWS: "sum_partials",
            Operation.COUNT_NONEMPTY: "sum_partials",
            Operation.COUNT_DISTINCT: "count_distinct_keys",
            Operation.AVERAGE: "weighted_average_from_sum_count",
            Operation.MIN: "min_partials",
            Operation.MAX: "max_partials",
            Operation.VALUE: "project_unique_value",
        }[metric.operation]
        nodes.append(PlanNode(
            rollup_id,
            "aggregate_at_conformed_grain",
            (current,),
            metric.target_grain,
            {
                "metric_id": metric.id,
                "operation": rollup_operation,
                "partial_column": partial_name,
                "partial_columns": partial_params.get("partial_columns", ()),
                "entity_key": metric.entity_key,
                "canonical_target_grain": metric.target_grain,
                "dimension_binding_ids": metric.dimension_binding_ids,
            },
            metric.evidence_refs,
        ))
        return rollup_id

    @staticmethod
    def _append_dimension_node(
        metric: MetricIntent,
        dimension_id: str,
        binding: DimensionBinding,
        input_node: str,
        output_grain: tuple[str, ...],
        nodes: list[PlanNode],
    ) -> str:
        node_id = f"{metric.id}__dimension__{dimension_id}"
        nodes.append(PlanNode(
            node_id,
            "derive_dimension",
            (input_node,),
            output_grain,
            {
                "canonical_dimension_id": dimension_id,
                "binding_id": binding.id,
                "source_sheet": binding.source_sheet,
                "source_fields": binding.source_fields,
                "transform": binding.transform.kind.value,
                "parameters": binding.transform.parameters,
                "deterministic": True,
            },
            binding.evidence_refs,
        ))
        return node_id

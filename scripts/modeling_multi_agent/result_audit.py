from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

import pandas as pd

from .compiler import ExecutionPlan, PlanNode
from .executor import ExecutionResult
from .ir import MetricIntent, Operation, SheetIntent, WorkbookIntentIR


class AuditStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass(frozen=True)
class ResultAuditCheck:
    check_id: str
    layer: str
    status: AuditStatus
    sheet_id: str
    metric_id: str | None
    rows_checked: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultAuditReport:
    passed: bool
    checks: tuple[ResultAuditCheck, ...]
    verified: int
    failed: int


class FullDataResultAuditor:
    """Recompute every metric and final row selection from unrounded full data."""

    def audit(
        self,
        plan: ExecutionPlan,
        intent: WorkbookIntentIR,
        execution: ExecutionResult,
        sources: Mapping[str, pd.DataFrame],
    ) -> ResultAuditReport:
        nodes = {item.id: item for item in plan.nodes}
        checks: list[ResultAuditCheck] = []
        for sheet in intent.sheets:
            project = nodes[plan.sheet_outputs[sheet.id]]
            internal = execution.node_frames[project.inputs[0]]
            for metric in sheet.metrics:
                if metric.operation == Operation.DERIVED:
                    checks.append(self._derived(sheet, metric, internal))
                else:
                    checks.extend(self._source(sheet, metric, internal, nodes, execution, sources))
            checks.append(self._selection(sheet, project, internal, execution.sheets[sheet.id]))
        failed = sum(item.status == AuditStatus.FAILED for item in checks)
        return ResultAuditReport(
            failed == 0,
            tuple(checks),
            sum(item.status == AuditStatus.VERIFIED for item in checks),
            failed,
        )

    def _source(
        self,
        sheet: SheetIntent,
        metric: MetricIntent,
        final_internal: pd.DataFrame,
        nodes: dict[str, PlanNode],
        execution: ExecutionResult,
        sources: Mapping[str, pd.DataFrame],
    ) -> list[ResultAuditCheck]:
        pre_id, rollup_id = f"{metric.id}__preaggregate", f"{metric.id}__rollup"
        if not metric.source_sheet or pre_id not in nodes or rollup_id not in nodes:
            return [self._check(metric.id + ":source", "source_reconciliation", False, sheet.id, metric.id, 0, {"error": "missing metric plan nodes"})]
        source, pre = sources[metric.source_sheet], execution.node_frames[pre_id]
        pre_node = nodes[pre_id]
        partial = pre_node.params["partial_column"]
        operation = metric.operation
        if operation == Operation.SUM:
            expected = self._numeric(source[metric.source_fields[0]]).sum(min_count=1)
            actual = self._numeric(pre[partial]).sum(min_count=1)
        elif operation == Operation.COUNT_ROWS:
            expected, actual = len(source), self._numeric(pre[partial]).sum()
        elif operation == Operation.COUNT_NONEMPTY:
            values = source[metric.source_fields[0]]
            expected = int((values.notna() & values.astype("string").str.strip().ne("").fillna(False)).sum())
            actual = self._numeric(pre[partial]).sum()
        elif operation == Operation.COUNT_DISTINCT:
            expected_set = self._keys(source, metric.entity_key)
            actual_set: set[tuple[Any, ...]] = set()
            for item in pre[partial]:
                actual_set.update(item)
            expected, actual = len(expected_set), len(actual_set)
        elif operation == Operation.AVERAGE:
            sum_column, count_column = pre_node.params["partial_columns"]
            raw = self._numeric(source[metric.source_fields[0]])
            expected = (raw.sum(min_count=1), int(raw.notna().sum()))
            actual = (self._numeric(pre[sum_column]).sum(min_count=1), int(self._numeric(pre[count_column]).sum()))
        elif operation == Operation.MIN:
            expected, actual = source[metric.source_fields[0]].min(), pre[partial].min()
        elif operation == Operation.MAX:
            expected, actual = source[metric.source_fields[0]].max(), pre[partial].max()
        else:
            expected = source.loc[:, list(metric.source_grain)].drop_duplicates().shape[0]
            actual = len(pre)
        source_check = self._check(
            metric.id + ":source", "source_reconciliation", self._equal(expected, actual),
            sheet.id, metric.id, len(source),
            {"expected": self._plain(expected), "actual": self._plain(actual), "full_source_rows": len(source)},
        )
        preaggregate_check = self._preaggregate(sheet, metric, pre_node, execution)
        rollup_check = self._rollup(sheet, metric, nodes[rollup_id], execution)
        checks = [source_check, preaggregate_check, rollup_check]
        if operation in {Operation.SUM, Operation.COUNT_ROWS, Operation.COUNT_NONEMPTY}:
            output_total = self._numeric(final_internal[metric.id]).sum(min_count=1)
            checks.append(self._check(
                metric.id + ":total", "total_reconciliation", self._equal(expected, output_total),
                sheet.id, metric.id, len(final_internal),
                {"expected": self._plain(expected), "actual": self._plain(output_total)},
            ))
        return checks

    def _preaggregate(
        self,
        sheet: SheetIntent,
        metric: MetricIntent,
        node: PlanNode,
        execution: ExecutionResult,
    ) -> ResultAuditCheck:
        if not node.inputs or node.inputs[0] not in execution.node_frames:
            return self._check(
                metric.id + ":preaggregate", "source_grain_recalculation", False,
                sheet.id, metric.id, 0, {"error": "missing full-data preaggregate input"},
            )
        source = execution.node_frames[node.inputs[0]]
        actual = execution.node_frames[node.id]
        keys = tuple(dict.fromkeys((
            *node.params.get("source_grain", ()),
            *node.params.get("canonical_dimensions_retained", ()),
        )))
        if not keys:
            keys = tuple(node.output_grain)
        partial = node.params["partial_column"]
        operation = metric.operation
        records: list[dict[str, Any]] = []
        if keys:
            grouper: str | list[str] = keys[0] if len(keys) == 1 else list(keys)
            groups = (
                (key if isinstance(key, tuple) else (key,), group)
                for key, group in source.groupby(grouper, dropna=False, sort=False, observed=True)
            )
        else:
            groups = [((), source)]
        for key, group in groups:
            record = dict(zip(keys, key))
            if operation == Operation.SUM:
                record[partial] = self._numeric(group[metric.source_fields[0]]).sum(min_count=1)
            elif operation == Operation.COUNT_ROWS:
                record[partial] = len(group)
            elif operation == Operation.COUNT_NONEMPTY:
                values = group[metric.source_fields[0]]
                record[partial] = int((values.notna() & values.astype("string").str.strip().ne("").fillna(False)).sum())
            elif operation == Operation.COUNT_DISTINCT:
                record[partial] = frozenset(self._keys(group, metric.entity_key))
            elif operation == Operation.AVERAGE:
                values = self._numeric(group[metric.source_fields[0]])
                sum_column, count_column = node.params["partial_columns"]
                record[sum_column] = values.sum(min_count=1)
                record[count_column] = int(values.notna().sum())
            elif operation in {Operation.MIN, Operation.MAX}:
                values = group[metric.source_fields[0]].dropna()
                record[partial] = pd.NA if values.empty else (
                    values.min() if operation == Operation.MIN else values.max()
                )
            else:
                values = group[metric.source_fields[0]]
                record[partial] = values.iloc[0] if len(values) == 1 else pd.NA
            records.append(record)
        value_columns = (
            list(node.params["partial_columns"])
            if operation == Operation.AVERAGE
            else [partial]
        )
        expected = pd.DataFrame.from_records(records).reindex(columns=[*keys, *value_columns])
        comparable = actual.loc[:, [*keys, *value_columns]]
        return self._check(
            metric.id + ":preaggregate", "source_grain_recalculation",
            self._frame_equal(expected, comparable, keys),
            sheet.id, metric.id, len(source),
            {"groups_checked": len(expected), "operation": operation.value},
        )

    def _rollup(
        self,
        sheet: SheetIntent,
        metric: MetricIntent,
        node: PlanNode,
        execution: ExecutionResult,
    ) -> ResultAuditCheck:
        source, actual = execution.node_frames[node.inputs[0]], execution.node_frames[node.id]
        keys, operation, partial = tuple(node.output_grain), node.params["operation"], node.params["partial_column"]
        records: list[dict[str, Any]] = []
        if keys:
            grouper: str | list[str] = keys[0] if len(keys) == 1 else list(keys)
            groups = ((key if isinstance(key, tuple) else (key,), group) for key, group in source.groupby(grouper, dropna=False, sort=False, observed=True))
        else:
            groups = [((), source)]
        for key, group in groups:
            record = dict(zip(keys, key))
            if operation == "sum_partials":
                value = self._numeric(group[partial]).sum(min_count=1)
            elif operation == "count_distinct_keys":
                values: set[tuple[Any, ...]] = set()
                for item in group[partial]:
                    values.update(item)
                value = len(values)
            elif operation == "weighted_average_from_sum_count":
                sum_column, count_column = node.params["partial_columns"]
                total_sum = self._numeric(group[sum_column]).sum(min_count=1)
                total_count = self._numeric(group[count_column]).sum(min_count=1)
                value = pd.NA if total_count == 0 else total_sum / total_count
            elif operation == "min_partials":
                value = group[partial].min()
            elif operation == "max_partials":
                value = group[partial].max()
            else:
                unique = group[partial].drop_duplicates()
                value = unique.iloc[0] if len(unique) == 1 else pd.NA
            records.append({**record, metric.id: value})
        expected = pd.DataFrame.from_records(records).reindex(columns=[*keys, metric.id])
        ok = self._frame_equal(expected, actual.loc[:, [*keys, metric.id]], keys)
        return self._check(
            metric.id + ":rollup", "grain_rollup", ok, sheet.id, metric.id,
            len(source), {"groups_checked": len(actual), "operation": operation},
        )

    def _derived(self, sheet: SheetIntent, metric: MetricIntent, frame: pd.DataFrame) -> ResultAuditCheck:
        assert metric.expression is not None
        values = [self._numeric(frame[item]) for item in metric.dependencies]
        operator, parameters = metric.expression.operator, metric.expression.parameters
        expected = values[0].copy()
        if operator == "divide":
            expected = values[0].div(values[1].replace(0, float("nan")))
            if metric.expression.zero_division == "zero":
                expected = expected.fillna(0)
        elif operator == "subtract":
            expected = values[0] - values[1]
        elif operator == "add":
            for item in values[1:]: expected = expected + item
        elif operator == "multiply":
            for item in values[1:]: expected = expected * item
        elif operator == "add_constant": expected = expected + parameters["value"]
        elif operator == "subtract_constant": expected = expected - parameters["value"]
        elif operator == "multiply_constant": expected = expected * parameters["factor"]
        else: expected = expected / parameters["divisor"]
        return self._check(
            metric.id + ":formula", "derived_reverse_calculation",
            self._series_equal(expected, self._numeric(frame[metric.id])),
            sheet.id, metric.id, len(frame),
            {"operator": operator, "dependencies": list(metric.dependencies), "definition": metric.business_definition, "unit": metric.unit.value, "risk": metric.risk_level.value},
        )

    def _selection(self, sheet: SheetIntent, node: PlanNode, internal: pd.DataFrame, actual: pd.DataFrame) -> ResultAuditCheck:
        metric_ids = tuple(item.id for item in sheet.metrics)
        expected = internal.loc[:, [*sheet.target_grain, *metric_ids]].copy()
        minimum_group_size = node.params.get("minimum_group_size")
        group_count_metric_id = node.params.get("group_count_metric_id")
        if minimum_group_size is not None and group_count_metric_id is not None:
            counts = pd.to_numeric(expected[group_count_metric_id], errors="coerce")
            expected = expected.loc[counts.ge(minimum_group_size).fillna(False)].copy()
        order_by = node.params.get("order_by") or ()
        if order_by:
            expected = expected.sort_values(
                [item["column_id"] for item in order_by],
                ascending=[item["direction"] == "asc" for item in order_by],
                na_position="last", kind="mergesort",
            )
        if node.params.get("limit") is not None:
            expected = expected.head(node.params["limit"])
        rename = {**node.params.get("dimension_names", {}), **node.params.get("metric_names", {})}
        expected = expected.rename(columns=rename).reset_index(drop=True)
        ok = self._frame_equal(expected, actual.reset_index(drop=True), ())
        return self._check(
            sheet.id + ":selection", "selection_and_top_n", ok, sheet.id, None,
            len(internal), {
                "output_rows": len(actual),
                "limit": node.params.get("limit"),
                "order_by": list(order_by),
                "minimum_group_size": minimum_group_size,
                "group_count_metric_id": group_count_metric_id,
            },
        )

    @staticmethod
    def _numeric(series: pd.Series) -> pd.Series:
        return pd.to_numeric(series, errors="coerce")

    @staticmethod
    def _keys(frame: pd.DataFrame, keys: tuple[str, ...]) -> set[tuple[Any, ...]]:
        return set(frame.loc[:, list(keys)].dropna(how="any").itertuples(index=False, name=None))

    @classmethod
    def _equal(cls, expected: Any, actual: Any) -> bool:
        if isinstance(expected, tuple):
            return isinstance(actual, tuple) and len(expected) == len(actual) and all(cls._equal(a, b) for a, b in zip(expected, actual))
        if isinstance(expected, (set, frozenset)):
            return isinstance(actual, (set, frozenset)) and set(expected) == set(actual)
        if pd.isna(expected) and pd.isna(actual): return True
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return abs(float(expected) - float(actual)) <= 1e-9 * max(1.0, abs(float(expected)))
        return expected == actual

    @classmethod
    def _series_equal(cls, expected: pd.Series, actual: pd.Series) -> bool:
        return len(expected) == len(actual) and all(cls._equal(a, b) for a, b in zip(expected.tolist(), actual.tolist()))

    @classmethod
    def _frame_equal(cls, expected: pd.DataFrame, actual: pd.DataFrame, keys: tuple[str, ...]) -> bool:
        if set(expected.columns) != set(actual.columns) or len(expected) != len(actual): return False
        if keys:
            expected = expected.sort_values(list(keys), key=lambda col: col.astype(str))
            actual = actual.sort_values(list(keys), key=lambda col: col.astype(str))
        expected, actual = expected.reset_index(drop=True), actual.reset_index(drop=True).loc[:, expected.columns]
        return all(cls._series_equal(expected[column], actual[column]) for column in expected.columns)

    @staticmethod
    def _plain(value: Any) -> Any:
        if isinstance(value, tuple): return [FullDataResultAuditor._plain(item) for item in value]
        return value.item() if hasattr(value, "item") else value

    @staticmethod
    def _check(check_id: str, layer: str, ok: bool, sheet_id: str, metric_id: str | None, rows: int, details: dict[str, Any]) -> ResultAuditCheck:
        return ResultAuditCheck(check_id, layer, AuditStatus.VERIFIED if ok else AuditStatus.FAILED, sheet_id, metric_id, rows, details)

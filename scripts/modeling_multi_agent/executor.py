from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype

from .compiler import ExecutionPlan, PlanNode


class ExecutionError(RuntimeError):
    """A deterministic plan cannot be executed without guessing."""

    def __init__(self, code: str, message: str, node_id: str | None = None) -> None:
        self.code = code
        self.node_id = node_id
        prefix = f"[{code}]"
        if node_id:
            prefix += f" {node_id}:"
        super().__init__(f"{prefix} {message}")


@dataclass(frozen=True)
class NodeAudit:
    node_id: str
    kind: str
    input_rows: dict[str, int]
    output_rows: int
    output_columns: tuple[str, ...]
    output_grain: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    sheets: dict[str, pd.DataFrame]
    provenance: dict[str, dict[str, tuple[str, ...]]]
    audit: tuple[NodeAudit, ...]
    node_frames: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass
class _NodeResult:
    frame: pd.DataFrame
    provenance: dict[str, tuple[str, ...]]
    details: dict[str, Any] = field(default_factory=dict)


class PandasPlanExecutor:
    """Execute a compiled plan against complete, in-memory source tables."""

    _NODE_KINDS = {
        "scan_source",
        "derive_dimension",
        "pre_aggregate_to_source_grain",
        "enrich_many_to_one",
        "aggregate_at_conformed_grain",
        "merge_metric_branches",
        "derive_metric",
        "project_sheet",
    }

    def execute(
        self,
        plan: ExecutionPlan,
        sources: Mapping[str, pd.DataFrame],
    ) -> ExecutionResult:
        if not isinstance(plan, ExecutionPlan):
            raise ExecutionError("INVALID_PLAN", "plan must be an ExecutionPlan")
        self._validate_sources(sources)
        node_ids = [node.id for node in plan.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ExecutionError("DUPLICATE_NODE_ID", "plan node ids must be unique")

        results: dict[str, _NodeResult] = {}
        audits: list[NodeAudit] = []
        for node in plan.nodes:
            if node.kind not in self._NODE_KINDS:
                raise ExecutionError(
                    "UNKNOWN_NODE_KIND",
                    f"unsupported operation {node.kind!r}",
                    node.id,
                )
            missing_inputs = [item for item in node.inputs if item not in results]
            if missing_inputs:
                raise ExecutionError(
                    "MISSING_NODE_INPUT",
                    f"inputs have not been executed: {missing_inputs}",
                    node.id,
                )
            inputs = {item: results[item] for item in node.inputs}
            result = self._execute_node(node, inputs, sources)
            result.frame = result.frame.reset_index(drop=True)
            results[node.id] = result
            audits.append(NodeAudit(
                node_id=node.id,
                kind=node.kind,
                input_rows={key: len(value.frame) for key, value in inputs.items()},
                output_rows=len(result.frame),
                output_columns=tuple(str(column) for column in result.frame.columns),
                output_grain=node.output_grain,
                evidence_refs=node.evidence_refs,
                details=result.details,
            ))

        sheets: dict[str, pd.DataFrame] = {}
        provenance: dict[str, dict[str, tuple[str, ...]]] = {}
        for sheet_id, node_id in plan.sheet_outputs.items():
            if node_id not in results:
                raise ExecutionError(
                    "MISSING_SHEET_OUTPUT",
                    f"sheet {sheet_id!r} references unknown node {node_id!r}",
                )
            sheets[sheet_id] = results[node_id].frame.copy(deep=True)
            provenance[sheet_id] = dict(results[node_id].provenance)
        return ExecutionResult(
            sheets, provenance, tuple(audits),
            {node_id: item.frame.copy(deep=True) for node_id, item in results.items()},
        )

    @staticmethod
    def _validate_sources(sources: Mapping[str, pd.DataFrame]) -> None:
        if not isinstance(sources, Mapping):
            raise ExecutionError("INVALID_SOURCES", "sources must map sheet names to DataFrames")
        invalid = [name for name, frame in sources.items() if not isinstance(name, str) or not isinstance(frame, pd.DataFrame)]
        if invalid:
            raise ExecutionError("INVALID_SOURCES", f"invalid DataFrame sources: {invalid}")
        duplicate_columns = [
            name for name, frame in sources.items()
            if frame.columns.duplicated().any()
        ]
        if duplicate_columns:
            raise ExecutionError(
                "DUPLICATE_SOURCE_COLUMN",
                f"source sheets contain duplicate column labels: {duplicate_columns}",
            )

    def _execute_node(
        self,
        node: PlanNode,
        inputs: Mapping[str, _NodeResult],
        sources: Mapping[str, pd.DataFrame],
    ) -> _NodeResult:
        handlers = {
            "scan_source": self._scan,
            "derive_dimension": self._derive_dimension,
            "pre_aggregate_to_source_grain": self._pre_aggregate,
            "enrich_many_to_one": self._enrich,
            "aggregate_at_conformed_grain": self._rollup,
            "merge_metric_branches": self._merge_metric_branches,
            "derive_metric": self._derive_metric,
            "project_sheet": self._project_sheet,
        }
        return handlers[node.kind](node, inputs, sources)

    def _scan(self, node: PlanNode, inputs, sources: Mapping[str, pd.DataFrame]) -> _NodeResult:
        self._require_input_count(node, inputs, 0)
        self._require_params(node, {"source_sheet", "fields", "field_provenance"})
        source_sheet = self._nonempty_text(node.params["source_sheet"], node, "source_sheet")
        if source_sheet not in sources:
            raise ExecutionError("MISSING_SOURCE", f"source sheet {source_sheet!r} was not supplied", node.id)
        fields = self._text_tuple(node.params["fields"], node, "fields")
        if len(fields) != len(set(fields)):
            raise ExecutionError("DUPLICATE_FIELD", "scan fields must be unique", node.id)
        self._require_columns(sources[source_sheet], fields, node)
        declared = node.params["field_provenance"]
        if not isinstance(declared, dict) or set(declared) != set(fields):
            raise ExecutionError(
                "INVALID_PROVENANCE",
                "scan field_provenance must explicitly cover exactly the scanned fields",
                node.id,
            )
        frame = sources[source_sheet].loc[:, list(fields)].copy(deep=True)
        provenance = {
            field: (f"source:{declared[field]}",)
            for field in fields
        }
        return _NodeResult(frame, provenance, {"source_sheet": source_sheet, "full_source_rows": len(frame)})

    def _derive_dimension(self, node: PlanNode, inputs, _sources) -> _NodeResult:
        self._require_input_count(node, inputs, 1)
        self._require_params(node, {
            "canonical_dimension_id", "binding_id", "source_sheet", "source_fields",
            "transform", "parameters", "deterministic",
        })
        if node.params["deterministic"] is not True:
            raise ExecutionError("NONDETERMINISTIC_TRANSFORM", "dimension transform must be deterministic", node.id)
        source_fields = self._text_tuple(node.params["source_fields"], node, "source_fields")
        if len(source_fields) != 1:
            raise ExecutionError("INVALID_TRANSFORM_INPUT", "dimension transform requires one source field", node.id)
        source_field = source_fields[0]
        current = self._only_input(inputs)
        self._require_columns(current.frame, (source_field,), node)
        dimension_id = self._nonempty_text(node.params["canonical_dimension_id"], node, "canonical_dimension_id")
        transform = self._nonempty_text(node.params["transform"], node, "transform")
        parameters = node.params["parameters"]
        if not isinstance(parameters, dict):
            raise ExecutionError("INVALID_TRANSFORM_PARAMS", "parameters must be an object", node.id)
        if dimension_id in current.frame.columns and dimension_id != source_field:
            raise ExecutionError("COLUMN_COLLISION", f"dimension {dimension_id!r} already exists", node.id)
        if dimension_id == source_field and transform != "identity":
            raise ExecutionError(
                "COLUMN_COLLISION",
                "a transformed canonical dimension cannot overwrite its physical source field",
                node.id,
            )

        output = current.frame.copy(deep=True)
        output[dimension_id] = self._apply_transform(output[source_field], transform, parameters, node)
        provenance = dict(current.provenance)
        lineage = current.provenance.get(source_field, (f"column:{source_field}",))
        provenance[dimension_id] = (*lineage, f"transform:{transform}")
        return _NodeResult(output, provenance, {"dimension_id": dimension_id, "transform": transform})

    def _apply_transform(
        self,
        source: pd.Series,
        transform: str,
        parameters: dict[str, Any],
        node: PlanNode,
    ) -> pd.Series:
        if transform == "identity":
            self._exact_keys(parameters, set(), node, "identity parameters")
            return source.copy()
        if transform == "date_bucket":
            self._exact_keys(parameters, {"grain"}, node, "date_bucket parameters")
            grain = parameters["grain"]
            if grain not in {"day", "week", "month", "quarter", "year"}:
                raise ExecutionError("INVALID_DATE_BUCKET", f"unsupported date grain {grain!r}", node.id)
            converted = pd.to_datetime(source, errors="coerce")
            invalid = source.notna() & converted.isna()
            if invalid.any():
                raise ExecutionError(
                    "INVALID_DATE_VALUE",
                    f"{int(invalid.sum())} non-null values cannot be parsed as dates",
                    node.id,
                )
            if is_datetime64_any_dtype(converted.dtype) and getattr(converted.dt, "tz", None) is not None:
                converted = converted.dt.tz_localize(None)
            if grain == "day":
                return converted.dt.normalize()
            frequencies = {"week": "W-SUN", "month": "M", "quarter": "Q", "year": "Y"}
            return converted.dt.to_period(frequencies[grain]).dt.start_time
        if transform == "numeric_bucket":
            self._exact_keys(parameters, {"boundaries", "labels", "closed"}, node, "numeric_bucket parameters")
            boundaries = parameters["boundaries"]
            labels = parameters["labels"]
            closed = parameters["closed"]
            if (
                not isinstance(boundaries, list)
                or not boundaries
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in boundaries)
                or any(not math.isfinite(value) for value in boundaries)
                or boundaries != sorted(set(boundaries))
                or not isinstance(labels, list)
                or len(labels) != len(boundaries) + 1
                or len(set(map(str, labels))) != len(labels)
                or closed not in {"left", "right"}
            ):
                raise ExecutionError("INVALID_NUMERIC_BUCKET", "numeric bucket parameters are invalid", node.id)
            numeric = self._numeric(source, node, "numeric bucket input")
            result = pd.cut(
                numeric,
                bins=[float("-inf"), *boundaries, float("inf")],
                labels=labels,
                right=closed == "right",
                include_lowest=True,
            )
            return result.astype(object)
        if transform == "categorical_rule":
            self._exact_keys(parameters, {"rules", "default_label"}, node, "categorical_rule parameters")
            rules = parameters["rules"]
            default_label = parameters["default_label"]
            if not isinstance(rules, list) or not rules or not isinstance(default_label, str):
                raise ExecutionError("INVALID_CATEGORICAL_RULE", "rules and a text default_label are required", node.id)
            result = pd.Series(default_label, index=source.index, dtype=object)
            unmatched = pd.Series(True, index=source.index)
            empty = source.isna() | source.astype("string").str.strip().eq("").fillna(False)
            for rule in rules:
                if not isinstance(rule, dict):
                    raise ExecutionError("INVALID_CATEGORICAL_RULE", "every rule must be an object", node.id)
                operator = rule.get("operator")
                expected = {
                    "equals": {"label", "operator", "value"},
                    "in": {"label", "operator", "values"},
                    "range": {"label", "operator", "minimum", "maximum", "include_minimum", "include_maximum"},
                    "is_empty": {"label", "operator"},
                    "not_empty": {"label", "operator"},
                }.get(operator)
                if expected is None or set(rule) != expected or not isinstance(rule.get("label"), str):
                    raise ExecutionError("INVALID_CATEGORICAL_RULE", f"invalid {operator!r} rule boundary", node.id)
                if operator == "equals":
                    mask = source.eq(rule["value"]).fillna(False)
                elif operator == "in":
                    if not isinstance(rule["values"], list) or not rule["values"]:
                        raise ExecutionError("INVALID_CATEGORICAL_RULE", "in rule values must be non-empty", node.id)
                    mask = source.isin(rule["values"])
                elif operator == "range":
                    minimum = rule["minimum"]
                    maximum = rule["maximum"]
                    if (
                        isinstance(minimum, bool)
                        or isinstance(maximum, bool)
                        or not isinstance(minimum, (int, float))
                        or not isinstance(maximum, (int, float))
                        or minimum > maximum
                        or not isinstance(rule["include_minimum"], bool)
                        or not isinstance(rule["include_maximum"], bool)
                    ):
                        raise ExecutionError("INVALID_CATEGORICAL_RULE", "range boundary is invalid", node.id)
                    numeric = pd.to_numeric(source, errors="coerce")
                    lower = numeric.ge(minimum) if rule["include_minimum"] else numeric.gt(minimum)
                    upper = numeric.le(maximum) if rule["include_maximum"] else numeric.lt(maximum)
                    mask = (lower & upper).fillna(False)
                elif operator == "is_empty":
                    mask = empty
                else:
                    mask = ~empty
                selected = unmatched & mask
                result.loc[selected] = rule["label"]
                unmatched &= ~selected
            return result
        raise ExecutionError("UNKNOWN_TRANSFORM", f"unsupported dimension transform {transform!r}", node.id)

    def _pre_aggregate(self, node: PlanNode, inputs, _sources) -> _NodeResult:
        self._require_input_count(node, inputs, 1)
        required = {
            "metric_id", "operation", "source_fields", "entity_key", "source_grain",
            "canonical_dimensions_retained", "partial_column",
        }
        optional = {"partial_columns", "retain_distinct_keys"}
        self._require_params(node, required, optional)
        current = self._only_input(inputs)
        metric_id = self._nonempty_text(node.params["metric_id"], node, "metric_id")
        operation = self._nonempty_text(node.params["operation"], node, "operation")
        source_fields = self._text_tuple(node.params["source_fields"], node, "source_fields")
        source_grain = self._text_tuple(node.params["source_grain"], node, "source_grain")
        canonical = self._text_tuple(
            node.params["canonical_dimensions_retained"], node, "canonical_dimensions_retained"
        )
        entity_key = self._text_tuple(node.params["entity_key"], node, "entity_key")
        partial = self._nonempty_text(node.params["partial_column"], node, "partial_column")
        group_keys = self._dedupe((*source_grain, *canonical))
        self._require_columns(current.frame, (*group_keys, *source_fields, *entity_key), node)
        if partial in current.frame.columns:
            raise ExecutionError("COLUMN_COLLISION", f"partial column {partial!r} already exists", node.id)

        records: list[dict[str, Any]] = []
        for key, group in self._groups(current.frame, group_keys):
            record = {field: value for field, value in zip(group_keys, key)}
            if operation == "sum":
                self._require_measure_fields(source_fields, node)
                record[partial] = self._numeric(group[source_fields[0]], node, source_fields[0]).sum(min_count=1)
            elif operation == "count_rows":
                if source_fields or entity_key:
                    raise ExecutionError("INVALID_COUNT_ROWS", "count_rows cannot bind fields or entity keys", node.id)
                record[partial] = int(len(group))
            elif operation == "count_nonempty":
                self._require_measure_fields(source_fields, node)
                values = group[source_fields[0]]
                nonempty = values.notna() & values.astype("string").str.strip().ne("").fillna(False)
                record[partial] = int(nonempty.sum())
            elif operation == "count_distinct":
                if not entity_key or source_fields:
                    raise ExecutionError(
                        "INVALID_COUNT_DISTINCT",
                        "count_distinct requires entity_key and no source_fields",
                        node.id,
                    )
                retain = self._text_tuple(node.params.get("retain_distinct_keys"), node, "retain_distinct_keys")
                if retain != entity_key:
                    raise ExecutionError("INVALID_COUNT_DISTINCT", "retained keys must equal entity_key", node.id)
                complete = group.loc[:, list(entity_key)].dropna(how="any")
                record[partial] = frozenset(tuple(row) for row in complete.itertuples(index=False, name=None))
            elif operation == "average":
                self._require_measure_fields(source_fields, node)
                partials = self._text_tuple(node.params.get("partial_columns"), node, "partial_columns")
                expected = (f"{partial}__sum", f"{partial}__count")
                if partials != expected:
                    raise ExecutionError("INVALID_AVERAGE_PARTIALS", f"expected partial columns {expected}", node.id)
                values = self._numeric(group[source_fields[0]], node, source_fields[0])
                record[partials[0]] = values.sum(min_count=1)
                record[partials[1]] = int(values.notna().sum())
            elif operation in {"min", "max"}:
                self._require_measure_fields(source_fields, node)
                values = group[source_fields[0]].dropna()
                record[partial] = pd.NA if values.empty else getattr(values, operation)()
            elif operation == "value":
                self._require_measure_fields(source_fields, node)
                if len(group) != 1:
                    raise ExecutionError(
                        "NON_UNIQUE_SOURCE_GRAIN",
                        f"value metric {metric_id!r} has {len(group)} rows at one declared source grain",
                        node.id,
                    )
                record[partial] = group[source_fields[0]].iloc[0]
            else:
                raise ExecutionError("UNKNOWN_AGGREGATION", f"unsupported pre-aggregation {operation!r}", node.id)
            records.append(record)

        output = pd.DataFrame.from_records(records)
        expected_columns = list(group_keys)
        if operation == "average":
            expected_columns.extend(node.params["partial_columns"])
        else:
            expected_columns.append(partial)
        output = output.reindex(columns=expected_columns)
        provenance = {key: current.provenance.get(key, (f"column:{key}",)) for key in group_keys}
        lineage = tuple(
            item
            for field in (*source_fields, *entity_key)
            for item in current.provenance.get(field, (f"column:{field}",))
        )
        for column in expected_columns[len(group_keys):]:
            provenance[column] = (*lineage, f"aggregate:{operation}", f"metric:{metric_id}")
        return _NodeResult(output, provenance, {"metric_id": metric_id, "operation": operation, "groups": len(output)})

    def _enrich(self, node: PlanNode, inputs, sources: Mapping[str, pd.DataFrame]) -> _NodeResult:
        self._require_input_count(node, inputs, 1)
        self._require_params(node, {
            "join_intent_id", "from_sheet", "to_sheet", "from_keys", "to_keys",
            "projection", "projection_provenance", "preserve_source_rows",
        })
        if node.params["preserve_source_rows"] is not True:
            raise ExecutionError("UNSAFE_JOIN", "enrichment must preserve all source rows", node.id)
        current = self._only_input(inputs)
        to_sheet = self._nonempty_text(node.params["to_sheet"], node, "to_sheet")
        if to_sheet not in sources:
            raise ExecutionError("MISSING_SOURCE", f"lookup sheet {to_sheet!r} was not supplied", node.id)
        from_keys = self._text_tuple(node.params["from_keys"], node, "from_keys")
        to_keys = self._text_tuple(node.params["to_keys"], node, "to_keys")
        projection = self._text_tuple(node.params["projection"], node, "projection")
        if not from_keys or len(from_keys) != len(to_keys):
            raise ExecutionError("INVALID_JOIN_KEYS", "enrichment keys must be non-empty and paired", node.id)
        if len(projection) != len(set(projection)):
            raise ExecutionError("DUPLICATE_FIELD", "lookup projection must be unique", node.id)
        self._require_columns(current.frame, from_keys, node)
        lookup = sources[to_sheet]
        right_fields = self._dedupe((*to_keys, *projection))
        self._require_columns(lookup, right_fields, node)
        declared = node.params["projection_provenance"]
        if not isinstance(declared, dict) or set(declared) != set(projection):
            raise ExecutionError("INVALID_PROVENANCE", "projection provenance must be explicit", node.id)
        duplicate_lookup = lookup.duplicated(subset=list(to_keys), keep=False)
        if duplicate_lookup.any():
            raise ExecutionError(
                "NON_UNIQUE_LOOKUP",
                f"lookup {to_sheet!r} has {int(duplicate_lookup.sum())} rows on keys {to_keys}",
                node.id,
            )
        allowed_collisions = {
            right for left, right in zip(from_keys, to_keys) if left == right
        }
        collisions = (set(projection) & set(current.frame.columns)) - allowed_collisions
        if collisions:
            raise ExecutionError(
                "COLUMN_COLLISION",
                f"lookup projection collides with existing columns: {sorted(collisions)}",
                node.id,
            )

        left = current.frame.copy(deep=True)
        order_column = self._temporary_column(left.columns, "__executor_order")
        left[order_column] = range(len(left))
        right = lookup.loc[:, list(right_fields)].copy(deep=True)
        right_names = {
            field: self._temporary_column((*left.columns, *right.columns), f"__lookup_{index}")
            for index, field in enumerate(right_fields)
        }
        right = right.rename(columns=right_names)
        valid_left = left.loc[left.loc[:, list(from_keys)].notna().all(axis=1)].copy()
        invalid_left = left.loc[~left.loc[:, list(from_keys)].notna().all(axis=1)].copy()
        valid_right = right.loc[
            right.loc[:, [right_names[field] for field in to_keys]].notna().all(axis=1)
        ].copy()
        try:
            merged = valid_left.merge(
                valid_right,
                how="left",
                left_on=list(from_keys),
                right_on=[right_names[field] for field in to_keys],
                sort=False,
                validate="many_to_one",
                indicator=True,
            )
        except pd.errors.MergeError as exc:
            raise ExecutionError("NON_UNIQUE_LOOKUP", str(exc), node.id) from exc
        matched_rows = int(merged["_merge"].eq("both").sum()) if "_merge" in merged else 0
        merged = merged.drop(columns=["_merge"])
        for temporary in right_names.values():
            if temporary not in invalid_left.columns:
                invalid_left[temporary] = pd.NA
        output = pd.concat([merged, invalid_left], ignore_index=True, sort=False)
        output = output.sort_values(order_column, kind="stable").drop(columns=[order_column])
        for field in projection:
            temporary = right_names[field]
            if field in allowed_collisions:
                output = output.drop(columns=[temporary])
            else:
                output[field] = output.pop(temporary)
        unused_temporary = [name for name in right_names.values() if name in output.columns]
        output = output.drop(columns=unused_temporary)
        if len(output) != len(current.frame):
            raise ExecutionError("ROW_MULTIPLICATION", "many-to-one enrichment changed source row count", node.id)
        provenance = dict(current.provenance)
        for field in projection:
            source_lineage = f"source:{declared[field]}"
            if field in provenance:
                provenance[field] = (*provenance[field], source_lineage)
            else:
                provenance[field] = (source_lineage, f"join:{node.params['join_intent_id']}")
        return _NodeResult(
            output,
            provenance,
            {"lookup_sheet": to_sheet, "matched_rows": matched_rows, "unmatched_rows": len(output) - matched_rows},
        )

    def _rollup(self, node: PlanNode, inputs, _sources) -> _NodeResult:
        self._require_input_count(node, inputs, 1)
        self._require_params(node, {
            "metric_id", "operation", "partial_column", "entity_key",
            "canonical_target_grain", "dimension_binding_ids", "partial_columns",
        })
        current = self._only_input(inputs)
        metric_id = self._nonempty_text(node.params["metric_id"], node, "metric_id")
        operation = self._nonempty_text(node.params["operation"], node, "operation")
        partial = self._nonempty_text(node.params["partial_column"], node, "partial_column")
        canonical = self._text_tuple(node.params["canonical_target_grain"], node, "canonical_target_grain")
        if canonical != node.output_grain:
            raise ExecutionError("GRAIN_MISMATCH", "rollup canonical grain differs from node output grain", node.id)
        self._require_columns(current.frame, canonical, node)
        records: list[dict[str, Any]] = []
        for key, group in self._groups(current.frame, canonical):
            record = {field: value for field, value in zip(canonical, key)}
            if operation == "sum_partials":
                self._require_columns(group, (partial,), node)
                record[metric_id] = self._numeric(group[partial], node, partial).sum(min_count=1)
            elif operation == "count_distinct_keys":
                self._require_columns(group, (partial,), node)
                distinct: set[tuple[Any, ...]] = set()
                for value in group[partial]:
                    if not isinstance(value, (set, frozenset)):
                        raise ExecutionError("INVALID_DISTINCT_PARTIAL", "distinct partial must contain key sets", node.id)
                    distinct.update(value)
                record[metric_id] = int(len(distinct))
            elif operation == "weighted_average_from_sum_count":
                partial_columns = self._text_tuple(node.params["partial_columns"], node, "partial_columns")
                if len(partial_columns) != 2:
                    raise ExecutionError("INVALID_AVERAGE_PARTIALS", "weighted average requires explicit sum/count columns", node.id)
                sum_column, count_column = partial_columns
                self._require_columns(group, (sum_column, count_column), node)
                total_sum = self._numeric(group[sum_column], node, sum_column).sum(min_count=1)
                total_count = self._numeric(group[count_column], node, count_column).sum(min_count=1)
                record[metric_id] = pd.NA if pd.isna(total_count) or total_count == 0 else total_sum / total_count
            elif operation in {"min_partials", "max_partials"}:
                self._require_columns(group, (partial,), node)
                values = group[partial].dropna()
                record[metric_id] = pd.NA if values.empty else (
                    values.min() if operation == "min_partials" else values.max()
                )
            elif operation == "project_unique_value":
                self._require_columns(group, (partial,), node)
                if len(group) != 1:
                    raise ExecutionError(
                        "DUPLICATE_CANONICAL_GRAIN",
                        f"value metric has {len(group)} rows at canonical grain",
                        node.id,
                    )
                record[metric_id] = group[partial].iloc[0]
            else:
                raise ExecutionError("UNKNOWN_ROLLUP", f"unsupported rollup {operation!r}", node.id)
            records.append(record)
        output = pd.DataFrame.from_records(records).reindex(columns=[*canonical, metric_id])
        self._ensure_unique_grain(output, canonical, node)
        provenance = {key: current.provenance.get(key, (f"column:{key}",)) for key in canonical}
        partial_lineage = current.provenance.get(partial, (f"column:{partial}",))
        provenance[metric_id] = (*partial_lineage, f"rollup:{operation}", f"metric:{metric_id}")
        return _NodeResult(output, provenance, {"metric_id": metric_id, "operation": operation})

    def _merge_metric_branches(self, node: PlanNode, inputs, _sources) -> _NodeResult:
        self._require_params(node, {"join_type", "canonical_keys", "inputs_already_unique_on_canonical_grain"})
        if not inputs:
            raise ExecutionError("EMPTY_METRIC_MERGE", "sheet needs at least one source metric branch", node.id)
        if node.params["join_type"] != "full" or node.params["inputs_already_unique_on_canonical_grain"] is not True:
            raise ExecutionError("UNSAFE_METRIC_MERGE", "metric branches require a declared full one-to-one merge", node.id)
        canonical = self._text_tuple(node.params["canonical_keys"], node, "canonical_keys")
        if canonical != node.output_grain:
            raise ExecutionError("GRAIN_MISMATCH", "merge keys differ from node output grain", node.id)
        ordered = [inputs[item] for item in node.inputs]
        for branch in ordered:
            self._require_columns(branch.frame, canonical, node)
            self._ensure_unique_grain(branch.frame, canonical, node)
        output = ordered[0].frame.copy(deep=True)
        provenance = dict(ordered[0].provenance)
        for branch in ordered[1:]:
            metric_columns = [column for column in branch.frame.columns if column not in canonical]
            collisions = set(metric_columns) & set(output.columns)
            if collisions:
                raise ExecutionError("COLUMN_COLLISION", f"metric columns collide: {sorted(collisions)}", node.id)
            if canonical:
                output = output.merge(
                    branch.frame.loc[:, [*canonical, *metric_columns]],
                    how="outer",
                    on=list(canonical),
                    sort=False,
                    validate="one_to_one",
                )
            else:
                if len(output) != 1 or len(branch.frame) != 1:
                    raise ExecutionError("DUPLICATE_CANONICAL_GRAIN", "global metric branches must each have one row", node.id)
                for column in metric_columns:
                    output[column] = branch.frame[column].iloc[0]
            provenance.update({column: branch.provenance[column] for column in metric_columns})
        self._ensure_unique_grain(output, canonical, node)
        return _NodeResult(output, provenance, {"branches": len(ordered), "join_type": "full"})

    def _derive_metric(self, node: PlanNode, inputs, _sources) -> _NodeResult:
        self._require_params(node, {
            "metric_id", "operator", "operands", "composition", "zero_division", "parameters",
        })
        if not inputs:
            raise ExecutionError("MISSING_NODE_INPUT", "derived metric needs the current canonical table", node.id)
        current = inputs[node.inputs[0]]
        canonical = node.output_grain
        self._ensure_unique_grain(current.frame, canonical, node)
        metric_id = self._nonempty_text(node.params["metric_id"], node, "metric_id")
        operator = self._nonempty_text(node.params["operator"], node, "operator")
        operands = self._text_tuple(node.params["operands"], node, "operands")
        parameters = node.params["parameters"]
        if not isinstance(parameters, dict):
            raise ExecutionError("INVALID_DERIVED_PARAMETERS", "parameters must be an object", node.id)
        composition = node.params["composition"]
        if composition not in {"ratio_of_sums", "recompute"}:
            raise ExecutionError(
                "INVALID_DERIVED_COMPOSITION",
                f"unsupported derived composition {composition!r}",
                node.id,
            )
        zero_division_policy = node.params["zero_division"]
        if zero_division_policy not in {"null", "zero", "error"}:
            raise ExecutionError(
                "INVALID_ZERO_DIVISION_POLICY",
                f"unsupported policy {zero_division_policy!r}",
                node.id,
            )
        supported = {
            "ratio", "divide", "subtract", "add", "multiply",
            "add_constant", "subtract_constant", "multiply_constant", "divide_constant",
        }
        if operator not in supported:
            raise ExecutionError("UNKNOWN_DERIVED_OPERATOR", f"unsupported derived operator {operator!r}", node.id)
        if operator in {"ratio", "divide", "subtract"}:
            valid_boundary = len(operands) == 2 and not parameters
        elif operator in {"add", "multiply"}:
            valid_boundary = len(operands) >= 2 and not parameters
        elif operator in {"add_constant", "subtract_constant"}:
            valid_boundary = (
                len(operands) == 1
                and set(parameters) == {"value"}
                and self._is_number(parameters.get("value"))
            )
        elif operator == "multiply_constant":
            valid_boundary = (
                len(operands) == 1
                and set(parameters) == {"factor"}
                and self._is_number(parameters.get("factor"))
            )
        else:
            valid_boundary = (
                len(operands) == 1
                and set(parameters) == {"divisor"}
                and self._is_number(parameters.get("divisor"))
                and parameters.get("divisor") != 0
            )
        if not valid_boundary:
            raise ExecutionError(
                "INVALID_DERIVED_PARAMETERS",
                f"operator {operator!r} has invalid operands or parameters",
                node.id,
            )
        if len(node.inputs) != len(operands) + 1:
            raise ExecutionError("INVALID_DERIVED_INPUTS", "dependency inputs must align with operands", node.id)
        self._require_columns(current.frame, operands, node)
        for operand, input_id in zip(operands, node.inputs[1:]):
            dependency = inputs[input_id]
            self._ensure_unique_grain(dependency.frame, canonical, node)
            self._require_columns(dependency.frame, (*canonical, operand), node)
        if metric_id in current.frame.columns:
            raise ExecutionError("COLUMN_COLLISION", f"derived metric {metric_id!r} already exists", node.id)
        values = [self._numeric(current.frame[operand], node, operand) for operand in operands]
        left = values[0]
        output = current.frame.copy(deep=True)
        details: dict[str, Any] = {"metric_id": metric_id, "operator": operator}
        if operator in {"ratio", "divide"}:
            right = values[1]
            zero = right.eq(0) & right.notna()
            details["zero_division_rows"] = int(zero.sum())
            if zero.any():
                raise ExecutionError("DIVISION_BY_ZERO", f"denominator is zero in {int(zero.sum())} rows", node.id)
            output[metric_id] = left.div(right)
        elif operator == "subtract":
            output[metric_id] = left - values[1]
        elif operator == "add":
            value = left.copy()
            for operand in values[1:]:
                value = value + operand
            output[metric_id] = value
        elif operator == "multiply":
            value = left.copy()
            for operand in values[1:]:
                value = value * operand
            output[metric_id] = value
        elif operator == "add_constant":
            output[metric_id] = left + parameters["value"]
        elif operator == "subtract_constant":
            output[metric_id] = left - parameters["value"]
        elif operator == "multiply_constant":
            output[metric_id] = left * parameters["factor"]
        else:
            output[metric_id] = left / parameters["divisor"]
        provenance = dict(current.provenance)
        provenance[metric_id] = tuple(
            item
            for operand in operands
            for item in current.provenance.get(operand, (f"metric:{operand}",))
        ) + (f"derive:{operator}", f"metric:{metric_id}")
        return _NodeResult(output, provenance, details)

    def _project_sheet(self, node: PlanNode, inputs, _sources) -> _NodeResult:
        self._require_input_count(node, inputs, 1)
        self._require_params(
            node,
            {"title", "metric_ids"},
            {
                "dimension_names", "metric_names", "order_by", "limit",
                "minimum_group_size", "group_count_metric_id",
            },
        )
        current = self._only_input(inputs)
        metric_ids = self._text_tuple(node.params["metric_ids"], node, "metric_ids")
        columns = self._dedupe((*node.output_grain, *metric_ids))
        if len(columns) != len(node.output_grain) + len(metric_ids):
            raise ExecutionError("DUPLICATE_OUTPUT_COLUMN", "sheet dimensions and metrics must be unique", node.id)
        self._require_columns(current.frame, columns, node)
        self._ensure_unique_grain(current.frame, node.output_grain, node)
        frame = current.frame.loc[:, list(columns)].copy(deep=True)
        minimum_group_size = node.params.get("minimum_group_size")
        group_count_metric_id = node.params.get("group_count_metric_id")
        if (minimum_group_size is None) != (group_count_metric_id is None):
            raise ExecutionError(
                "INCOMPLETE_GROUP_SUPPRESSION",
                "minimum_group_size and group_count_metric_id must be provided together",
                node.id,
            )
        suppressed_rows = 0
        if minimum_group_size is not None:
            if (
                isinstance(minimum_group_size, bool)
                or not isinstance(minimum_group_size, int)
                or not 2 <= minimum_group_size <= 10000
                or group_count_metric_id not in metric_ids
            ):
                raise ExecutionError(
                    "INVALID_GROUP_SUPPRESSION",
                    "group suppression requires a valid minimum and output count metric",
                    node.id,
                )
            counts = pd.to_numeric(frame[group_count_metric_id], errors="coerce")
            mask = counts.ge(minimum_group_size).fillna(False)
            suppressed_rows = int((~mask).sum())
            frame = frame.loc[mask].copy(deep=True)
        order_by = node.params.get("order_by") or ()
        if not isinstance(order_by, (list, tuple)):
            raise ExecutionError("INVALID_ROW_ORDER", "order_by must be an array", node.id)
        sort_columns: list[str] = []
        ascending: list[bool] = []
        for index, raw in enumerate(order_by):
            if not isinstance(raw, dict) or set(raw) != {"column_id", "direction"}:
                raise ExecutionError("INVALID_ROW_ORDER", f"order_by[{index}] has an invalid shape", node.id)
            column_id = raw["column_id"]
            direction = raw["direction"]
            if column_id not in columns or direction not in {"asc", "desc"}:
                raise ExecutionError("INVALID_ROW_ORDER", f"order_by[{index}] is not executable", node.id)
            sort_columns.append(column_id)
            ascending.append(direction == "asc")
        if sort_columns:
            frame = frame.sort_values(
                sort_columns,
                ascending=ascending,
                na_position="last",
                kind="mergesort",
            )
        limit = node.params.get("limit")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
                raise ExecutionError("INVALID_ROW_LIMIT", "limit must be an integer from 1 to 10000", node.id)
            if not sort_columns:
                raise ExecutionError("UNORDERED_ROW_LIMIT", "limit requires order_by", node.id)
            frame = frame.head(limit)
        provenance = {column: current.provenance[column] for column in columns}
        dimension_names = node.params.get("dimension_names") or {item: item for item in node.output_grain}
        metric_names = node.params.get("metric_names") or {item: item for item in metric_ids}
        if set(dimension_names) != set(node.output_grain) or set(metric_names) != set(metric_ids):
            raise ExecutionError("INVALID_DISPLAY_NAMES", "display-name mappings must cover every output id exactly", node.id)
        rename = {**dimension_names, **metric_names}
        if any(not isinstance(value, str) or not value for value in rename.values()):
            raise ExecutionError("INVALID_DISPLAY_NAMES", "display names must be non-empty strings", node.id)
        if len(set(rename.values())) != len(rename):
            raise ExecutionError("DUPLICATE_DISPLAY_NAME", "sheet display names must be unique", node.id)
        frame = frame.rename(columns=rename)
        provenance = {rename[column]: lineage for column, lineage in provenance.items()}
        return _NodeResult(
            frame,
            provenance,
            {
                "title": node.params["title"],
                "display_names": rename,
                "order_by": tuple(order_by),
                "limit": limit,
                "minimum_group_size": minimum_group_size,
                "group_count_metric_id": group_count_metric_id,
                "suppressed_rows": suppressed_rows,
            },
        )

    @staticmethod
    def _groups(frame: pd.DataFrame, keys: tuple[str, ...]):
        if not keys:
            return [((), frame)]
        grouper: str | list[str] = keys[0] if len(keys) == 1 else list(keys)
        return (
            ((key,) if len(keys) == 1 else tuple(key), group)
            for key, group in frame.groupby(grouper, dropna=False, sort=False, observed=True)
        )

    @staticmethod
    def _only_input(inputs: Mapping[str, _NodeResult]) -> _NodeResult:
        return next(iter(inputs.values()))

    @staticmethod
    def _require_input_count(node: PlanNode, inputs: Mapping[str, _NodeResult], expected: int) -> None:
        if len(inputs) != expected or len(node.inputs) != expected:
            raise ExecutionError(
                "INVALID_INPUT_COUNT",
                f"{node.kind} requires {expected} inputs, got {len(node.inputs)}",
                node.id,
            )

    @staticmethod
    def _require_params(node: PlanNode, required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        actual = set(node.params)
        missing = required - actual
        unknown = actual - required - optional
        if missing or unknown:
            raise ExecutionError(
                "INVALID_NODE_PARAMS",
                f"missing params {sorted(missing)}, unknown params {sorted(unknown)}",
                node.id,
            )

    @staticmethod
    def _exact_keys(value: Mapping[str, Any], expected: set[str], node: PlanNode, label: str) -> None:
        if not isinstance(value, Mapping) or set(value) != expected:
            actual = sorted(value) if isinstance(value, Mapping) else type(value).__name__
            raise ExecutionError("INVALID_TRANSFORM_PARAMS", f"{label} expected {sorted(expected)}, got {actual}", node.id)

    @staticmethod
    def _nonempty_text(value: Any, node: PlanNode, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ExecutionError("INVALID_NODE_PARAM", f"{label} must be non-empty text", node.id)
        return value

    @staticmethod
    def _text_tuple(value: Any, node: PlanNode, label: str) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or any(not isinstance(item, str) or not item for item in value):
            raise ExecutionError("INVALID_NODE_PARAM", f"{label} must be a text sequence", node.id)
        return tuple(value)

    @staticmethod
    def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], node: PlanNode) -> None:
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ExecutionError("MISSING_COLUMN", f"required columns are absent: {missing}", node.id)

    @staticmethod
    def _require_measure_fields(fields: tuple[str, ...], node: PlanNode) -> None:
        if len(fields) != 1:
            raise ExecutionError("INVALID_MEASURE_FIELDS", "operation requires exactly one source field", node.id)

    @staticmethod
    def _numeric(series: pd.Series, node: PlanNode, label: str) -> pd.Series:
        converted = pd.to_numeric(series, errors="coerce")
        invalid = series.notna() & converted.isna()
        if invalid.any():
            raise ExecutionError(
                "NON_NUMERIC_VALUE",
                f"{label} contains {int(invalid.sum())} non-numeric values",
                node.id,
            )
        return converted

    @staticmethod
    def _is_number(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    @staticmethod
    def _ensure_unique_grain(frame: pd.DataFrame, grain: tuple[str, ...], node: PlanNode) -> None:
        if grain:
            duplicate = frame.duplicated(subset=list(grain), keep=False)
            if duplicate.any():
                raise ExecutionError(
                    "DUPLICATE_CANONICAL_GRAIN",
                    f"{int(duplicate.sum())} rows duplicate canonical keys {grain}",
                    node.id,
                )
        elif len(frame) > 1:
            raise ExecutionError(
                "DUPLICATE_CANONICAL_GRAIN",
                f"global canonical grain has {len(frame)} rows",
                node.id,
            )

    @staticmethod
    def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _temporary_column(existing, prefix: str) -> str:
        existing_set = set(existing)
        candidate = prefix
        index = 1
        while candidate in existing_set:
            candidate = f"{prefix}_{index}"
            index += 1
        return candidate

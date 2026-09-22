from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Operation(str, Enum):
    SUM = "sum"
    AVERAGE = "average"
    MIN = "min"
    MAX = "max"
    COUNT_ROWS = "count_rows"
    COUNT_NONEMPTY = "count_nonempty"
    COUNT_DISTINCT = "count_distinct"
    VALUE = "value"
    DERIVED = "derived"


class MetricSemantic(str, Enum):
    ADDITIVE_MEASURE = "additive_measure"
    NON_ADDITIVE_MEASURE = "non_additive_measure"
    RECORD_COUNT = "record_count"
    ENTITY_COUNT = "entity_count"
    ATTRIBUTE_VALUE = "attribute_value"
    DERIVED_MEASURE = "derived_measure"


class Additivity(str, Enum):
    ADDITIVE = "additive"
    SEMI_ADDITIVE = "semi_additive"
    NON_ADDITIVE = "non_additive"


class Composition(str, Enum):
    SUM_PARTIALS = "sum_partials"
    WEIGHTED_AVERAGE = "weighted_average"
    DISTINCT_SET = "distinct_set"
    MINMAX = "minmax"
    RECOMPUTE = "recompute"
    RATIO_OF_SUMS = "ratio_of_sums"


class JoinCardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    MANY_TO_ONE = "many_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


class TransformKind(str, Enum):
    IDENTITY = "identity"
    DATE_BUCKET = "date_bucket"
    NUMERIC_BUCKET = "numeric_bucket"
    CATEGORICAL_RULE = "categorical_rule"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class MetricUnit(str, Enum):
    UNKNOWN = "unknown"
    CURRENCY = "currency"
    COUNT = "count"
    RATIO = "ratio"
    PERCENTAGE = "percentage"
    HOURS = "hours"
    DAYS = "days"
    DURATION = "duration"
    QUANTITY = "quantity"
    TEXT = "text"
    IDENTIFIER = "identifier"
    DATE = "date"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class DimensionTransform:
    kind: TransformKind
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DimensionBinding:
    id: str
    source_sheet: str
    source_fields: tuple[str, ...]
    transform: DimensionTransform
    evidence_refs: tuple[str, ...]
    required_decision_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DimensionIntent:
    id: str
    name: str
    bindings: tuple[DimensionBinding, ...]


@dataclass(frozen=True)
class DerivedExpression:
    operator: str
    operands: tuple[str, ...]
    zero_division: str = "null"
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricIntent:
    id: str
    name: str
    source_sheet: str | None
    source_fields: tuple[str, ...]
    source_grain: tuple[str, ...]
    target_grain: tuple[str, ...]
    dimension_binding_ids: tuple[str, ...]
    operation: Operation
    semantic: MetricSemantic
    entity_key: tuple[str, ...]
    additivity: Additivity
    composition: Composition
    evidence_refs: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    required_decision_ids: tuple[str, ...] = ()
    uniqueness_required: bool = False
    expression: DerivedExpression | None = None
    business_definition: str = ""
    unit: MetricUnit = MetricUnit.UNKNOWN
    risk_level: RiskLevel = RiskLevel.MEDIUM


@dataclass(frozen=True)
class JoinIntent:
    id: str
    left_sheet: str
    right_sheet: str
    left_keys: tuple[str, ...]
    right_keys: tuple[str, ...]
    cardinality: JoinCardinality
    evidence_ref: str
    preserve_source_rows: bool = True


@dataclass(frozen=True)
class ClarificationQuestion:
    id: str
    question: str
    why_it_matters: str
    affected_node_ids: tuple[str, ...]
    blocking: bool
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class SortIntent:
    column_id: str
    direction: SortDirection


@dataclass(frozen=True)
class SheetIntent:
    id: str
    title: str
    purpose: str
    dimensions: tuple[DimensionIntent, ...]
    target_grain: tuple[str, ...]
    metrics: tuple[MetricIntent, ...]
    order_by: tuple[SortIntent, ...] = ()
    limit: int | None = None
    minimum_group_size: int | None = None
    group_count_metric_id: str | None = None


@dataclass(frozen=True)
class WorkbookIntentIR:
    id: str
    title: str
    purpose: str
    sheets: tuple[SheetIntent, ...]
    joins: tuple[JoinIntent, ...] = ()
    clarifications: tuple[ClarificationQuestion, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    ir_version: str = "1.0"


@dataclass(frozen=True)
class Decision:
    id: str
    value: Any
    confirmed: bool
    source: str = "user"


@dataclass
class DecisionLedger:
    decisions: dict[str, Decision] = field(default_factory=dict)

    def is_confirmed(self, decision_id: str) -> bool:
        item = self.decisions.get(decision_id)
        return bool(item and item.confirmed)

    def confirm(self, decision_id: str, value: Any, source: str = "user") -> None:
        self.decisions[decision_id] = Decision(decision_id, value, True, source)

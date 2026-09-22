from __future__ import annotations

from dataclasses import replace

from .ir import (
    ClarificationQuestion, DimensionIntent, MetricIntent, Operation, SheetIntent,
    WorkbookIntentIR,
)


def canonicalize_workbook_intent(intent: WorkbookIntentIR) -> WorkbookIntentIR:
    """Remove only redundant model-shape noise before invariant validation.

    This function never changes an operation, formula, source, grain, join,
    threshold or evidence claim. A decision reference is relinked only when
    exactly one blocking clarification is bound to that node; ambiguous
    references are preserved so the validator can reject them.
    """
    questions = {item.id: item for item in intent.clarifications}
    return replace(
        intent,
        sheets=tuple(_canonicalize_sheet(sheet, questions) for sheet in intent.sheets),
    )


def _canonicalize_sheet(
    sheet: SheetIntent,
    questions: dict[str, ClarificationQuestion],
) -> SheetIntent:
    canonical_dimensions = tuple(
        _canonicalize_dimension(item, questions) for item in sheet.dimensions
    )
    dimensions = {item.id: item for item in canonical_dimensions}
    binding_ids = {
        binding.id
        for dimension in canonical_dimensions
        for binding in dimension.bindings
    }
    return replace(
        sheet,
        dimensions=canonical_dimensions,
        metrics=tuple(
            _canonicalize_metric(metric, dimensions, binding_ids, questions)
            for metric in sheet.metrics
        ),
    )


def _canonicalize_dimension(
    dimension: DimensionIntent,
    questions: dict[str, ClarificationQuestion],
) -> DimensionIntent:
    return replace(
        dimension,
        bindings=tuple(
            replace(
                binding,
                required_decision_ids=_canonicalize_decision_ids(
                    binding.id, binding.required_decision_ids, questions
                ),
            )
            for binding in dimension.bindings
        ),
    )


def _canonicalize_metric(
    metric: MetricIntent,
    dimensions,
    binding_ids: set[str],
    questions: dict[str, ClarificationQuestion],
) -> MetricIntent:
    decision_ids = _canonicalize_decision_ids(
        metric.id, metric.required_decision_ids, questions
    )
    if metric.operation == Operation.DERIVED:
        return replace(
            metric,
            source_sheet=None,
            source_fields=(),
            source_grain=(),
            dimension_binding_ids=(),
            entity_key=(),
            required_decision_ids=decision_ids,
        )

    resolved: list[str] = []
    for index, dimension_id in enumerate(metric.target_grain):
        supplied = (
            metric.dimension_binding_ids[index]
            if index < len(metric.dimension_binding_ids)
            else None
        )
        dimension = dimensions.get(dimension_id)
        if supplied in binding_ids and dimension and any(
            item.id == supplied for item in dimension.bindings
        ):
            resolved.append(supplied)
            continue
        if not dimension:
            resolved.append(supplied or "")
            continue
        source_matches = [
            item.id
            for item in dimension.bindings
            if item.source_sheet == metric.source_sheet
        ]
        if len(source_matches) == 1:
            resolved.append(source_matches[0])
        elif len(dimension.bindings) == 1:
            resolved.append(dimension.bindings[0].id)
        else:
            resolved.append(supplied or "")

    source_fields = metric.source_fields
    entity_key = metric.entity_key
    if metric.operation == Operation.COUNT_ROWS:
        source_fields = ()
        entity_key = ()
    elif metric.operation == Operation.COUNT_DISTINCT:
        source_fields = ()

    return replace(
        metric,
        source_fields=source_fields,
        entity_key=entity_key,
        dimension_binding_ids=tuple(resolved),
        required_decision_ids=decision_ids,
    )


def _canonicalize_decision_ids(
    node_id: str,
    decision_ids: tuple[str, ...],
    questions: dict[str, ClarificationQuestion],
) -> tuple[str, ...]:
    candidates = [
        question.id
        for question in questions.values()
        if question.blocking and node_id in question.affected_node_ids
    ]
    resolved = [
        decision_id
        if decision_id in questions or len(candidates) != 1
        else candidates[0]
        for decision_id in decision_ids
    ]
    return tuple(dict.fromkeys(resolved))

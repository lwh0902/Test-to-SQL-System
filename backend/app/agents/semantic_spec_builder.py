"""Compile approved semantic objects into the physical AnalysisSpec IR."""

from __future__ import annotations

from typing import Any

from app.agents.analysis_spec import AnalysisSpec, FilterExpr, Measure, TimeRange
from app.agents.semantic_catalog import SemanticCatalog
from app.agents.semantic_model import SemanticModel
from app.agents.semantic_parser import SemanticQueryRequest


def build_analysis_spec(query: SemanticQueryRequest, model: SemanticModel, catalog: SemanticCatalog, *, original_question: str) -> AnalysisSpec:
    entity = model.entity(query.entity or "")
    if entity is None:
        raise ValueError("unknown_entity")
    metrics = [model.metric(mid) for mid in query.metrics]
    if not metrics or any(m is None for m in metrics):
        raise ValueError("metric_required")
    dimensions = [model.dimension(did) for did in query.dimensions]
    if any(d is None or d.entity != entity.id for d in dimensions):
        raise ValueError("invalid_dimension")
    measures, filters = [], []
    for metric in metrics:
        assert metric is not None
        if metric.entity != entity.id:
            raise ValueError("metric_entity_mismatch")
        local = metric.default_filters[0] if metric.aggregation == "rate" and metric.default_filters else None
        measures.append(Measure(metric.field or "id", metric.aggregation, metric.id, entity.table, FilterExpr(local.field, local.op, local.value, entity.table) if local else None))
        if metric.aggregation != "rate":
            filters.extend(FilterExpr(x.field, x.op, x.value, entity.table) for x in metric.default_filters)
    for item in query.filters:
        dim = model.dimension(item.field)
        field = dim.field if dim else item.field
        filters.append(FilterExpr(field, item.op, _canonical_filter_value(field, item.value), entity.table))
    time_range = None
    if query.time_range:
        field = query.time_range.field or entity.time_field
        time_range = TimeRange(field, query.time_range.start, query.time_range.end, raw_text="semantic_parser")
    dim_fields = [f"time:{query.time_grain}:{entity.time_field}" for _ in [0] if query.time_grain and entity.time_field] + [d.field for d in dimensions if d]
    return AnalysisSpec(
        task_type="aggregate", subject=entity.table, measures=measures, dimensions=dim_fields,
        filters=filters, time_range=time_range, ordering=query.order_by or None, limit=query.limit,
        required_tables=[entity.table], confidence=0.9, original_question=original_question,
        unresolved_slots=list(query.unresolved_slots or []),
    )


_PRODUCT_TYPE_VALUES = {
    "酒店": "hotel",
    "hotel": "hotel",
    "用车": "car",
    "车": "car",
    "car": "car",
    "门票": "ticket",
    "ticket": "ticket",
}


def _canonical_filter_value(field: str, value: Any) -> Any:
    if field != "product_type":
        return value
    text = str(value or "").strip()
    return _PRODUCT_TYPE_VALUES.get(text, _PRODUCT_TYPE_VALUES.get(text.lower(), value))

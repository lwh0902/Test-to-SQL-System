"""Derive a chat chart from AnalysisSpec and the actual result columns."""

from __future__ import annotations

from typing import Any

from app.agents.analysis_spec import AnalysisSpec
from app.agents.semantic_model import SemanticModel
from app.models.schemas import ChartConfig


def chart_from_spec(
    spec: AnalysisSpec | None,
    *,
    columns: list[str],
    rows: list[dict],
    model: SemanticModel | None = None,
) -> ChartConfig | None:
    if spec is None or not columns or not rows:
        return None
    y_fields = _measure_columns(spec, columns)
    if not y_fields:
        return None
    labels = {name: _label_for(name, spec, model) for name in y_fields}
    value_format = "percent" if any((m.aggregation or "").lower() == "rate" for m in spec.measures) else "number"
    x_field = _x_field(spec, columns, y_fields)
    if not x_field:
        return None
    chart_type = "line" if _has_time_dimension(spec) or x_field == "time_bucket" else "bar"
    if len(rows) == 1 and not _has_time_dimension(spec):
        return None
    return ChartConfig(
        type=chart_type,
        x_field=x_field,
        y_fields=y_fields,
        labels=labels,
        value_format=value_format,
    )


def _measure_columns(spec: AnalysisSpec, columns: list[str]) -> list[str]:
    preferred = []
    for measure in spec.measures:
        label = (measure.business_label or "").strip()
        if label and label in columns:
            preferred.append(label)
    if "value" in columns:
        preferred.append("value")
    out: list[str] = []
    for name in preferred:
        if name in columns and name not in out:
            out.append(name)
    return out


def _x_field(spec: AnalysisSpec, columns: list[str], y_fields: list[str]) -> str:
    if "time_bucket" in columns:
        return "time_bucket"
    dim_fields = []
    for dim in spec.dimensions or []:
        if dim.startswith("time:"):
            continue
        dim_fields.append(dim.split(".", 1)[-1])
    for name in dim_fields:
        if name in columns and name not in y_fields:
            return name
    for name in columns:
        if name not in y_fields:
            return name
    return ""


def _has_time_dimension(spec: AnalysisSpec) -> bool:
    return any(str(dim).startswith("time:") for dim in spec.dimensions or []) or spec.time_range is not None and any(
        str(dim).startswith("time:") for dim in spec.dimensions or []
    )


def _label_for(name: str, spec: AnalysisSpec, model: SemanticModel | None) -> str:
    if model is not None:
        metric = model.metric(name)
        if metric is not None:
            return metric.description or metric.id
    for measure in spec.measures:
        if measure.business_label == name and measure.business_label:
            if model is not None:
                metric = model.metric(measure.business_label)
                if metric is not None:
                    return metric.description or metric.id
            return measure.business_label
    if name == "value" and spec.measures:
        label = spec.measures[0].business_label
        if model is not None and label:
            metric = model.metric(label)
            if metric is not None:
                return metric.description or label
        return label or "数值"
    return name

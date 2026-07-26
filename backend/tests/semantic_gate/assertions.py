from __future__ import annotations

import math
from typing import Any

from sqlglot import exp, parse_one

from .contracts import (
    FilterExpectation,
    MeasureExpectation,
    SemanticExpectation,
    SemanticObservation,
    SemanticScore,
)


def _norm(value: Any) -> str:
    return str(value).strip().strip("`\"'").lower()


def _sql_tables(sql: str) -> set[str]:
    if not sql:
        return set()
    try:
        tree = parse_one(sql, read="mysql")
    except Exception:
        return set()
    return {_norm(node.name) for node in tree.find_all(exp.Table)}


def _spec_measures(spec: dict[str, Any]) -> list[dict[str, Any]]:
    values = spec.get("measures") or []
    if isinstance(values, dict):
        values = [values]
    return [v for v in values if isinstance(v, dict)]


def _measure_present(expected: MeasureExpectation, spec: dict[str, Any]) -> bool:
    for measure in _spec_measures(spec):
        if _norm(measure.get("aggregation")) != _norm(expected.aggregation):
            continue
        if expected.source_field and _norm(measure.get("source_field")) != _norm(expected.source_field):
            continue
        if expected.table and _norm(measure.get("table")) != _norm(expected.table):
            continue
        return True
    return False


def _filter_present(expected: FilterExpectation, spec: dict[str, Any]) -> bool:
    for item in spec.get("filters") or []:
        if not isinstance(item, dict):
            continue
        if _norm(item.get("field")) != _norm(expected.field):
            continue
        if _norm(item.get("op") or "=") != _norm(expected.op):
            continue
        if _norm(item.get("value")) != _norm(expected.value):
            continue
        if expected.table and _norm(item.get("table")) != _norm(expected.table):
            continue
        return True
    return False


def _value_equal(actual: Any, expected: Any) -> bool:
    try:
        a, b = float(actual), float(expected)
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
    except (TypeError, ValueError):
        return _norm(actual) == _norm(expected)


def score_semantics(
    expected: SemanticExpectation,
    observed: SemanticObservation,
) -> SemanticScore:
    spec = observed.spec or {}
    dims: dict[str, bool] = {}
    hard: list[str] = []
    notes: list[str] = []

    dims["behavior"] = _norm(observed.behavior) == _norm(expected.behavior)
    if expected.behavior == "clarify":
        actual_slots = {_norm(x) for x in observed.clarify_slots}
        required_slots = {_norm(x) for x in expected.clarify_slots}
        dims["clarify_slots"] = required_slots.issubset(actual_slots)
        if not dims["behavior"]:
            hard.append("guessed_when_clarification_required")
    else:
        dims["clarify_slots"] = True

    if expected.subject_table:
        dims["subject_table"] = _norm(spec.get("subject")) == _norm(expected.subject_table)
        if (
            not dims["subject_table"]
            and expected.behavior == "answer"
            and _norm(observed.behavior) == "answer"
        ):
            hard.append("wrong_subject_table")
    else:
        dims["subject_table"] = True

    if expected.sql_tables:
        actual_tables = _sql_tables(observed.sql)
        wanted_tables = {_norm(x) for x in expected.sql_tables}
        dims["sql_tables"] = actual_tables == wanted_tables
        if (
            not dims["sql_tables"]
            and expected.behavior == "answer"
            and _norm(observed.behavior) == "answer"
        ):
            hard.append("wrong_sql_tables")
            notes.append(f"expected_tables={sorted(wanted_tables)} actual_tables={sorted(actual_tables)}")
    else:
        dims["sql_tables"] = True

    missing_measures = [m for m in expected.measures if not _measure_present(m, spec)]
    dims["measures"] = not missing_measures
    if missing_measures:
        hard.append("missing_or_wrong_measure")

    missing_filters = [f for f in expected.filters if not _filter_present(f, spec)]
    dims["filters"] = not missing_filters
    if missing_filters:
        hard.append("missing_or_wrong_filter")

    actual_dimensions = {_norm(x) for x in spec.get("dimensions") or []}
    wanted_dimensions = {_norm(x) for x in expected.dimensions}
    dims["dimensions"] = wanted_dimensions.issubset(actual_dimensions)
    if not dims["dimensions"]:
        hard.append("missing_or_wrong_dimension")

    if expected.time_range:
        tr = spec.get("time_range") or {}
        dims["time_range"] = (
            _norm(tr.get("start")) == _norm(expected.time_range[0])
            and _norm(tr.get("end")) == _norm(expected.time_range[1])
        )
        if not dims["time_range"]:
            hard.append("missing_or_wrong_time_range")
    else:
        dims["time_range"] = True

    if expected.order_by:
        ordering = spec.get("ordering") or []
        ordering_text = " ".join(_norm(x) for x in ordering)
        dims["ordering"] = all(_norm(x) in ordering_text for x in expected.order_by)
        if not dims["ordering"]:
            hard.append("missing_or_wrong_ordering")
    else:
        dims["ordering"] = True

    if expected.limit is not None:
        dims["limit"] = spec.get("limit") == expected.limit
        if not dims["limit"]:
            hard.append("missing_or_wrong_limit")
    else:
        dims["limit"] = True

    missing_values = [
        key
        for key, value in expected.expected_values.items()
        if key not in observed.result_values or not _value_equal(observed.result_values[key], value)
    ]
    dims["values"] = not missing_values
    if missing_values:
        hard.append("value_mismatch")

    if expected.expect_reuse is not None:
        dims["reuse"] = observed.reused is expected.expect_reuse
        if not dims["reuse"]:
            hard.append("wrong_reuse_decision")
    else:
        dims["reuse"] = True

    leaked = [v for v in expected.forbidden_persisted_values if v and v in observed.persisted_payload]
    dims["privacy"] = not leaked
    if leaked:
        hard.append("sensitive_raw_value_persisted")

    hard = list(dict.fromkeys(hard))
    return SemanticScore(
        case_id=expected.case_id,
        family=expected.family,
        full_correct=all(dims.values()) and not hard,
        dimensions=dims,
        hard_failures=hard,
        notes=notes,
    )

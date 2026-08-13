"""Reject plans that lose explicit business meaning from the user's question."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from app.agents.analysis_spec import AnalysisSpec
from app.agents.semantic_model import SemanticModel
from app.services.entity_value_service import EntityValue


@dataclass(frozen=True)
class SemanticCoverage:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def validate_semantic_coverage(
    question: str,
    spec: AnalysisSpec,
    model: SemanticModel,
    entity_values: Iterable[EntityValue] = (),
) -> SemanticCoverage:
    """Ensure explicit model objects and exact database names survive to the Spec.

    This is intentionally not a keyword-to-SQL mapper.  It only checks business
    objects declared in the semantic model and exact names synchronized from the
    connected database.
    """
    q = _compact(question)
    errors: list[str] = []
    filter_pairs = {(f.field, _compact(str(f.value))) for f in spec.filters}
    spec_dimensions = set(spec.dimensions)

    for value in entity_values:
        canonical = _compact(value.canonical_value)
        aliases = [_compact(x) for x in value.aliases]
        if canonical and (canonical in q or any(alias and alias in q for alias in aliases)):
            if (value.dimension, canonical) not in filter_pairs:
                errors.append(f"missing_explicit_filter:{value.dimension}")

    for dimension in model.dimensions:
        terms = [_compact(dimension.id), *[_compact(x) for x in dimension.aliases]]
        if any(term and term in q for term in terms):
            if dimension.field not in spec_dimensions and not any(f.field == dimension.field for f in spec.filters):
                errors.append(f"missing_explicit_dimension:{dimension.id}")

    if re.search(r"(?:最近|近|过去)\d{1,3}(?:天|日)|(?:今年|本月|上月|本周|上周|今天|昨日|昨天)", q):
        if spec.time_range is None:
            errors.append("missing_explicit_time_range")

    return SemanticCoverage(ok=not errors, errors=sorted(set(errors)))

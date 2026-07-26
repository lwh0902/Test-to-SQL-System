from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MeasureExpectation:
    aggregation: str
    source_field: str = ""
    table: str = ""


@dataclass(frozen=True)
class FilterExpectation:
    field: str
    value: Any
    op: str = "="
    table: str = ""


@dataclass(frozen=True)
class SemanticExpectation:
    case_id: str
    family: str
    question: str
    behavior: str = "answer"
    subject_table: str | None = None
    sql_tables: frozenset[str] = frozenset()
    measures: tuple[MeasureExpectation, ...] = ()
    filters: tuple[FilterExpectation, ...] = ()
    dimensions: tuple[str, ...] = ()
    time_range: tuple[str, str] | None = None
    order_by: tuple[str, ...] = ()
    limit: int | None = None
    expected_values: dict[str, float | int | str] = field(default_factory=dict)
    clarify_slots: tuple[str, ...] = ()
    expect_reuse: bool | None = None
    forbidden_persisted_values: tuple[str, ...] = ()


@dataclass
class SemanticObservation:
    behavior: str
    terminal_status: str = ""
    sql: str = ""
    spec: dict[str, Any] | None = None
    result_values: dict[str, Any] = field(default_factory=dict)
    clarify_slots: list[str] = field(default_factory=list)
    reused: bool | None = None
    persisted_payload: str = ""


@dataclass
class SemanticScore:
    case_id: str
    family: str
    full_correct: bool
    dimensions: dict[str, bool]
    hard_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def explain(self) -> str:
        failed = [name for name, ok in self.dimensions.items() if not ok]
        return (
            f"case={self.case_id} family={self.family} failed={failed} "
            f"hard={self.hard_failures} notes={self.notes}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "family": self.family,
            "full_correct": self.full_correct,
            "dimensions": dict(self.dimensions),
            "hard_failures": list(self.hard_failures),
            "notes": list(self.notes),
        }


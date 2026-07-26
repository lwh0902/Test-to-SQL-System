"""AnalysisSpec — unique IR for analysis tasks (dev_spec §4.2)."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.agents.semantic_catalog import SemanticCatalog


@dataclass
class Measure:
    source_field: str
    aggregation: str = "sum"  # sum|avg|min|max|count
    business_label: str = ""
    table: str = ""


@dataclass
class FilterExpr:
    field: str
    op: str = "="
    value: Any = None
    table: str = ""


@dataclass
class TimeRange:
    field: str
    start: str
    end: str
    timezone: str = "UTC"
    raw_text: str = ""


@dataclass
class JoinStep:
    src_table: str
    src_column: str
    dst_table: str
    dst_column: str
    confidence: float = 1.0
    source: str = "explicit_fk"  # explicit_fk|inferred|user


@dataclass
class AnalysisSpec:
    task_type: str = "aggregate"
    subject: str = ""
    measures: list[Measure] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[FilterExpr] = field(default_factory=list)
    time_range: Optional[TimeRange] = None
    comparison: Optional[dict] = None
    ordering: Optional[list[dict]] = None
    limit: Optional[int] = None
    required_tables: list[str] = field(default_factory=list)
    join_path: list[JoinStep] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unresolved_slots: list[str] = field(default_factory=list)
    confidence: float = 0.0
    original_question: str = ""
    spec_id: str = field(default_factory=lambda: f"as_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class SpecValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def validate_spec(spec: AnalysisSpec, catalog: SemanticCatalog) -> SpecValidation:
    errors: list[str] = []
    unresolved = list(spec.unresolved_slots or [])
    tmap = catalog.table_map
    for t in spec.required_tables:
        if t not in tmap:
            errors.append(f"unknown_table:{t}")
    for m in spec.measures:
        table = m.table or (spec.required_tables[0] if spec.required_tables else "")
        if table and table not in tmap:
            errors.append(f"measure_table_missing:{table}")
            continue
        if table:
            cols = {c.name for c in tmap[table].columns}
            if m.source_field not in cols and m.aggregation != "count":
                errors.append(f"unknown_field:{table}.{m.source_field}")
        agg = (m.aggregation or "").lower()
        if agg not in {"sum", "avg", "min", "max", "count", "rate"}:
            errors.append(f"bad_aggregation:{agg}")
    if spec.time_range:
        tr = spec.time_range
        table = spec.required_tables[0] if spec.required_tables else ""
        if table and table in tmap:
            cols = {c.name for c in tmap[table].columns}
            if tr.field and tr.field not in cols:
                # try any table
                found = any(tr.field in {c.name for c in t.columns} for t in catalog.tables)
                if not found:
                    errors.append(f"unknown_time_field:{tr.field}")
    for j in spec.join_path:
        if j.confidence < 0.85 and j.source != "user":
            errors.append(f"low_confidence_join:{j.src_table}->{j.dst_table}")
        if j.src_table not in tmap or j.dst_table not in tmap:
            errors.append(f"join_table_missing:{j.src_table}/{j.dst_table}")
    # silent default time forbidden if assumptions contain last_30
    for a in spec.assumptions or []:
        if re.search(r"last_?\s*30|默认.*30", str(a), re.I):
            errors.append("silent_default_time_forbidden")
    return SpecValidation(ok=not errors and not unresolved, errors=errors, unresolved=unresolved)

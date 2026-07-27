"""Evidence Bundle — answer/report fuel (Phase 3)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class EvidenceBundle:
    analysis_spec_id: str
    query_outcome_id: Optional[str] = None
    sql: str = ""
    time_range: Optional[dict] = None
    measures: list[dict] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    rows_count: int = 0
    result_preview: list[dict] = field(default_factory=list)
    catalog_fingerprint: str = ""
    join_sources: list[str] = field(default_factory=list)
    bundle_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def answer_footer(self) -> str:
        parts = []
        if self.time_range:
            parts.append(
                f"时间范围 {self.time_range.get('start')} ~ {self.time_range.get('end')}"
                + (f"（字段 {self.time_range.get('field')}）" if self.time_range.get("field") else "")
            )
        if self.measures:
            m = self.measures[0]
            parts.append(f"口径 {m.get('aggregation', '')}({m.get('source_field', '')})")
        if self.sql:
            parts.append("证据: QueryResult + AnalysisSpec")
        return "；".join(parts)

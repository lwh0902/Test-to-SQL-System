"""Semantic query snapshot persisted across travel_b2b follow-up turns."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.agents.semantic_parser import SemanticFilterRequest, SemanticQueryRequest, SemanticTimeRange


@dataclass(frozen=True)
class SemanticSnapshot:
    entity: str
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: tuple[tuple[str, str, Any], ...] = ()
    time_start: str = ""
    time_end: str = ""
    time_field: str = ""
    time_grain: str | None = None
    limit: int | None = None
    model_version: str = ""

    def to_query(self) -> SemanticQueryRequest:
        time_range = None
        if self.time_start and self.time_end:
            time_range = SemanticTimeRange(self.time_start, self.time_end, self.time_field)
        return SemanticQueryRequest(
            operation="modify_query",
            entity=self.entity,
            metrics=list(self.metrics),
            dimensions=list(self.dimensions),
            filters=[
                SemanticFilterRequest(field, op, value) for field, op, value in self.filters
            ],
            time_range=time_range,
            time_grain=self.time_grain,
            limit=self.limit,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "metrics": list(self.metrics),
            "dimensions": list(self.dimensions),
            "filters": [
                {"field": field, "op": op, "value": value} for field, op, value in self.filters
            ],
            "time_range": (
                {"start": self.time_start, "end": self.time_end, "field": self.time_field}
                if self.time_start and self.time_end
                else None
            ),
            "time_grain": self.time_grain,
            "limit": self.limit,
            "model_version": self.model_version,
        }

    @classmethod
    def from_query(
        cls, query: SemanticQueryRequest, *, model_version: str = ""
    ) -> SemanticSnapshot:
        tr = query.time_range
        return cls(
            entity=str(query.entity or ""),
            metrics=tuple(str(x) for x in query.metrics if x),
            dimensions=tuple(str(x) for x in query.dimensions if x),
            filters=tuple(
                (str(item.field), str(item.op or "="), item.value) for item in query.filters
            ),
            time_start=str(tr.start) if tr else "",
            time_end=str(tr.end) if tr else "",
            time_field=str(tr.field) if tr else "",
            time_grain=query.time_grain,
            limit=query.limit,
            model_version=model_version,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> SemanticSnapshot | None:
        if not isinstance(raw, dict) or not raw.get("entity"):
            return None
        tr = raw.get("time_range") if isinstance(raw.get("time_range"), dict) else {}
        filters = []
        for item in raw.get("filters") or []:
            if isinstance(item, dict) and item.get("field"):
                filters.append((str(item["field"]), str(item.get("op") or "="), item.get("value")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                filters.append((str(item[0]), str(item[1] if len(item) > 1 else "="), item[2] if len(item) > 2 else None))
        return cls(
            entity=str(raw["entity"]),
            metrics=tuple(str(x) for x in raw.get("metrics") or [] if x),
            dimensions=tuple(str(x) for x in raw.get("dimensions") or [] if x),
            filters=tuple(filters),
            time_start=str(raw.get("time_start") or tr.get("start") or ""),
            time_end=str(raw.get("time_end") or tr.get("end") or ""),
            time_field=str(raw.get("time_field") or tr.get("field") or ""),
            time_grain=raw.get("time_grain"),
            limit=raw.get("limit"),
            model_version=str(raw.get("model_version") or ""),
        )


def snapshot_asdict(snapshot: SemanticSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    payload = asdict(snapshot)
    payload["filters"] = [
        {"field": field, "op": op, "value": value} for field, op, value in snapshot.filters
    ]
    return payload

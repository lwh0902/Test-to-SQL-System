"""ActiveAnalysisState — multi-turn analysis context (dev_spec §4.7 / Phase 4)."""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.agents.analysis_spec import (
    AnalysisSpec,
    FilterExpr,
    JoinStep,
    Measure,
    TimeRange,
)


def _spec_fingerprint(spec: AnalysisSpec) -> str:
    """Stable fingerprint for duplicate detection (ignores spec_id / question text)."""
    payload = {
        "task_type": spec.task_type,
        "subject": spec.subject,
        "measures": [
            {
                "table": m.table,
                "source_field": m.source_field,
                "aggregation": (m.aggregation or "").lower(),
            }
            for m in (spec.measures or [])
        ],
        "dimensions": list(spec.dimensions or []),
        "filters": [
            {"table": f.table, "field": f.field, "op": f.op, "value": f.value}
            for f in (spec.filters or [])
        ],
        "time_range": (
            {
                "field": spec.time_range.field,
                "start": spec.time_range.start,
                "end": spec.time_range.end,
            }
            if spec.time_range
            else None
        ),
        "required_tables": sorted(spec.required_tables or []),
        "join_path": [
            {
                "src_table": j.src_table,
                "src_column": j.src_column,
                "dst_table": j.dst_table,
                "dst_column": j.dst_column,
            }
            for j in (spec.join_path or [])
        ],
        "limit": spec.limit,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _measure_to_dict(m: Measure) -> dict[str, Any]:
    return {
        "source_field": m.source_field,
        "aggregation": m.aggregation,
        "business_label": m.business_label,
        "table": m.table,
    }


def _filter_to_dict(f: FilterExpr) -> dict[str, Any]:
    return {"field": f.field, "op": f.op, "value": f.value, "table": f.table}


def _time_to_dict(t: Optional[TimeRange]) -> Optional[dict[str, Any]]:
    if not t:
        return None
    return {
        "field": t.field,
        "start": t.start,
        "end": t.end,
        "timezone": t.timezone,
        "raw_text": t.raw_text,
    }


def _join_to_dict(j: JoinStep) -> dict[str, Any]:
    return {
        "src_table": j.src_table,
        "src_column": j.src_column,
        "dst_table": j.dst_table,
        "dst_column": j.dst_column,
        "confidence": j.confidence,
        "source": j.source,
    }


def spec_from_parts(
    *,
    subject: str,
    measures: list[dict],
    dimensions: list[str],
    filters: list[dict],
    time_range: Optional[dict],
    required_tables: list[str],
    join_path: list[dict],
    original_question: str = "",
    assumptions: list[str] | None = None,
    confidence: float = 0.85,
) -> AnalysisSpec:
    ms = [
        Measure(
            source_field=m.get("source_field", ""),
            aggregation=m.get("aggregation", "sum"),
            business_label=m.get("business_label", ""),
            table=m.get("table", ""),
            filter=(
                FilterExpr(
                    field=(m.get("filter") or {}).get("field", ""),
                    op=(m.get("filter") or {}).get("op", "="),
                    value=(m.get("filter") or {}).get("value"),
                    table=(m.get("filter") or {}).get("table", ""),
                )
                if isinstance(m.get("filter"), dict)
                else None
            ),
        )
        for m in measures
    ]
    fs = [
        FilterExpr(
            field=f.get("field", ""),
            op=f.get("op", "="),
            value=f.get("value"),
            table=f.get("table", ""),
        )
        for f in filters
    ]
    tr = None
    if time_range:
        tr = TimeRange(
            field=time_range.get("field", ""),
            start=time_range.get("start", ""),
            end=time_range.get("end", ""),
            timezone=time_range.get("timezone", "UTC"),
            raw_text=time_range.get("raw_text", ""),
        )
    jp = [
        JoinStep(
            src_table=j.get("src_table", ""),
            src_column=j.get("src_column", ""),
            dst_table=j.get("dst_table", ""),
            dst_column=j.get("dst_column", ""),
            confidence=float(j.get("confidence", 1.0)),
            source=j.get("source", "explicit_fk"),
        )
        for j in join_path
    ]
    return AnalysisSpec(
        task_type="aggregate",
        subject=subject,
        measures=ms,
        dimensions=list(dimensions or []),
        filters=fs,
        time_range=tr,
        required_tables=list(required_tables or []),
        join_path=jp,
        assumptions=list(assumptions or []),
        original_question=original_question,
        confidence=confidence,
    )


@dataclass
class ActiveAnalysisState:
    session_id: str = ""
    user_id: int = 0
    space_id: str = ""
    last_analysis_spec_id: str = ""
    last_query_outcome_id: Optional[str] = None
    last_evidence_bundle_id: Optional[str] = None
    active_subject: str = ""
    measures: list[dict] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    time_range: Optional[dict] = None
    comparison: Optional[dict] = None
    required_tables: list[str] = field(default_factory=list)
    join_path: list[dict] = field(default_factory=list)
    last_spec_fingerprint: str = ""
    last_result_preview: list[dict] = field(default_factory=list)
    last_sql: str = ""
    last_answer_text: str = ""
    approved_report_id: Optional[str] = None
    seen_fingerprints: list[str] = field(default_factory=list)
    turn_count: int = 0
    valid_until: float = 0.0
    updated_at: float = field(default_factory=time.time)
    state_id: str = field(default_factory=lambda: f"aas_{uuid.uuid4().hex[:12]}")
    catalog_fingerprint: str = ""
    catalog_version: str = ""
    version: int = 1  # optimistic concurrency / active_state_version
    semantic_snapshot: Optional[dict] = None

    @classmethod
    def from_spec(
        cls,
        spec: AnalysisSpec,
        *,
        session_id: str = "",
        user_id: int = 0,
        space_id: str = "",
        outcome_id: Optional[str] = None,
        evidence_id: Optional[str] = None,
        result_preview: Optional[list[dict]] = None,
        sql: str = "",
        answer_text: str = "",
        ttl_seconds: float = 86400.0,
        catalog_fingerprint: str = "",
        catalog_version: str = "",
    ) -> "ActiveAnalysisState":
        fp = _spec_fingerprint(spec)
        return cls(
            session_id=session_id,
            user_id=user_id,
            space_id=space_id,
            last_analysis_spec_id=spec.spec_id,
            last_query_outcome_id=outcome_id,
            last_evidence_bundle_id=evidence_id,
            active_subject=spec.subject,
            measures=[_measure_to_dict(m) for m in spec.measures],
            dimensions=list(spec.dimensions or []),
            filters=[_filter_to_dict(f) for f in spec.filters],
            time_range=_time_to_dict(spec.time_range),
            comparison=copy.deepcopy(spec.comparison) if spec.comparison else None,
            required_tables=list(spec.required_tables or []),
            join_path=[_join_to_dict(j) for j in (spec.join_path or [])],
            last_spec_fingerprint=fp,
            last_result_preview=list(result_preview or []),
            last_sql=sql or "",
            last_answer_text=answer_text or "",
            seen_fingerprints=[fp],
            turn_count=1,
            valid_until=time.time() + ttl_seconds,
            updated_at=time.time(),
            catalog_fingerprint=catalog_fingerprint or "",
            catalog_version=catalog_version or "",
            version=1,
        )

    def is_valid(self) -> bool:
        if self.valid_until and time.time() > self.valid_until:
            return False
        snap = self.semantic_snapshot if isinstance(self.semantic_snapshot, dict) else {}
        if snap.get("metrics") or snap.get("entity"):
            return True
        return bool(self.measures or self.last_analysis_spec_id)

    def to_spec(self, original_question: str = "") -> AnalysisSpec:
        return spec_from_parts(
            subject=self.active_subject,
            measures=list(self.measures),
            dimensions=list(self.dimensions),
            filters=list(self.filters),
            time_range=copy.deepcopy(self.time_range),
            required_tables=list(self.required_tables),
            join_path=list(self.join_path),
            original_question=original_question,
        )

    def update_from_result(
        self,
        spec: AnalysisSpec,
        *,
        outcome_id: Optional[str] = None,
        evidence_id: Optional[str] = None,
        result_preview: Optional[list[dict]] = None,
        sql: str = "",
        answer_text: str = "",
        ttl_seconds: float = 86400.0,
        catalog_fingerprint: str = "",
        catalog_version: str = "",
    ) -> None:
        fp = _spec_fingerprint(spec)
        self.last_analysis_spec_id = spec.spec_id
        self.last_query_outcome_id = outcome_id
        self.last_evidence_bundle_id = evidence_id
        self.active_subject = spec.subject
        self.measures = [_measure_to_dict(m) for m in spec.measures]
        self.dimensions = list(spec.dimensions or [])
        self.filters = [_filter_to_dict(f) for f in spec.filters]
        self.time_range = _time_to_dict(spec.time_range)
        self.comparison = copy.deepcopy(spec.comparison) if spec.comparison else None
        self.required_tables = list(spec.required_tables or [])
        self.join_path = [_join_to_dict(j) for j in (spec.join_path or [])]
        self.last_spec_fingerprint = fp
        if fp not in self.seen_fingerprints:
            self.seen_fingerprints.append(fp)
        self.last_result_preview = list(result_preview or [])
        self.last_sql = sql or ""
        self.last_answer_text = answer_text or ""
        self.turn_count += 1
        self.version = int(self.version or 0) + 1
        self.valid_until = time.time() + ttl_seconds
        self.updated_at = time.time()
        if catalog_fingerprint:
            self.catalog_fingerprint = catalog_fingerprint
        if catalog_version:
            self.catalog_version = catalog_version

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_from_dict(data: dict[str, Any] | None) -> Optional[ActiveAnalysisState]:
    if not data:
        return None
    known = {f.name for f in ActiveAnalysisState.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in data.items() if k in known}
    return ActiveAnalysisState(**kwargs)


def serialize_state(state: Optional[ActiveAnalysisState]) -> Optional[dict[str, Any]]:
    return state.to_dict() if state else None


def slim_state_payload(state: Optional[ActiveAnalysisState]) -> dict[str, Any]:
    """Identity + CAS metadata only. Snapshot lives in session_queries."""
    if state is None:
        return {}
    return {
        "session_id": state.session_id,
        "user_id": state.user_id,
        "space_id": state.space_id,
        "catalog_fingerprint": state.catalog_fingerprint or "",
        "catalog_version": state.catalog_version or "",
        "valid_until": float(state.valid_until or 0),
        "last_analysis_spec_id": state.last_analysis_spec_id or "",
        "last_sql": (state.last_sql or "")[:500],
    }

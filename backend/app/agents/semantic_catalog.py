"""SemanticCatalog — planning-facing DB profile (dev_spec §4.1 / Phase 2).

No credentials, no unbounded raw rows. Consumed by Planner / Supervisor.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class ReadinessStatus(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


class FieldRole(str, Enum):
    IDENTIFIER = "identifier"
    TIME = "time"
    ENUM_DIMENSION = "enum_dimension"
    CONTINUOUS_NUMERIC = "continuous_numeric"
    AMOUNT = "amount"
    RATIO = "ratio"
    STATUS = "status"
    TEXT = "description_text"
    UNKNOWN = "unknown"


@dataclass
class BoundedColumnStats:
    """Bounded, non-raw column profile evidence (R3.5a)."""

    null_ratio: Optional[float] = None
    approx_distinct_ratio: Optional[float] = None
    top_values: list[str] = field(default_factory=list)
    value_counts: dict[str, int] = field(default_factory=dict)
    numeric_min: Optional[float] = None
    numeric_max: Optional[float] = None
    sample_strategy: str = "none"
    sample_size: int = 0
    evidence_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "null_ratio": self.null_ratio,
            "approx_distinct_ratio": self.approx_distinct_ratio,
            "top_values": list(self.top_values)[:20],
            "value_counts": {
                str(k): int(v) for k, v in list(self.value_counts.items())[:20]
            },
            "numeric_min": self.numeric_min,
            "numeric_max": self.numeric_max,
            "sample_strategy": self.sample_strategy,
            "sample_size": int(self.sample_size or 0),
            "evidence_sources": list(self.evidence_sources)[:12],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Optional["BoundedColumnStats"]:
        if not isinstance(data, dict):
            return None
        return cls(
            null_ratio=data.get("null_ratio"),
            approx_distinct_ratio=data.get("approx_distinct_ratio"),
            top_values=[str(x) for x in (data.get("top_values") or [])][:20],
            value_counts={
                str(k): int(v) for k, v in dict(data.get("value_counts") or {}).items()
            },
            numeric_min=data.get("numeric_min"),
            numeric_max=data.get("numeric_max"),
            sample_strategy=str(data.get("sample_strategy") or "none"),
            sample_size=int(data.get("sample_size") or 0),
            evidence_sources=[str(x) for x in (data.get("evidence_sources") or [])][:12],
        )


@dataclass
class ColumnProfile:
    name: str
    data_type: str = ""
    nullable: bool = True
    comment: str = ""
    role: str = FieldRole.UNKNOWN.value
    sensitive: bool = False
    is_primary_key: bool = False
    is_foreign_key: bool = False
    profile: Optional[BoundedColumnStats] = None


@dataclass
class IndexProfile:
    name: str
    columns: list[str] = field(default_factory=list)
    unique: bool = False
    primary: bool = False


@dataclass
class TableProfile:
    name: str
    comment: str = ""
    columns: list[ColumnProfile] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    row_count_tier: str = "unknown"  # empty|tiny|small|medium|large|unknown
    time_bounds: dict[str, dict[str, Any]] = field(default_factory=dict)
    # field -> {min, max}


@dataclass
class RelationEdge:
    src_table: str
    src_column: str
    dst_table: str
    dst_column: str
    kind: str  # explicit_fk | inferred
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)
    risk: str = ""
    allow_auto_join: bool = False

    def key(self) -> tuple:
        return (self.src_table, self.src_column, self.dst_table, self.dst_column)


@dataclass
class CandidateMeasure:
    table: str
    field: str
    aggregations: list[str] = field(default_factory=lambda: ["sum", "avg", "min", "max"])
    business_label: str = ""
    role: str = FieldRole.AMOUNT.value


@dataclass
class ReadinessReport:
    status: ReadinessStatus = ReadinessStatus.BLOCKED
    reasons: list[str] = field(default_factory=list)
    limited_capabilities: list[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value if isinstance(self.status, ReadinessStatus) else str(self.status),
            "reasons": list(self.reasons),
            "limited_capabilities": list(self.limited_capabilities),
            "checked_at": self.checked_at,
        }


@dataclass
class SemanticCatalog:
    database_id: str
    version: str = "1"
    schema_fingerprint: str = ""
    tables: list[TableProfile] = field(default_factory=list)
    relations: list[RelationEdge] = field(default_factory=list)
    candidate_measures: list[CandidateMeasure] = field(default_factory=list)
    candidate_dimensions: list[dict] = field(default_factory=list)
    readiness: ReadinessReport = field(default_factory=ReadinessReport)
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    engine: str = "mysql"
    # never store password; optional safe identity only
    identity: dict[str, Any] = field(default_factory=dict)
    profiling_policy: dict[str, Any] = field(default_factory=dict)

    @property
    def table_map(self) -> dict[str, TableProfile]:
        return {t.name: t for t in self.tables}

    def to_public_dict(self) -> dict[str, Any]:
        """Safe serialization for APIs / artifacts."""
        return {
            "database_id": self.database_id,
            "version": self.version,
            "schema_fingerprint": self.schema_fingerprint,
            "engine": self.engine,
            "identity": {
                k: v
                for k, v in (self.identity or {}).items()
                if str(k).lower()
                not in {"password", "passwd", "secret", "token", "encrypted_password", "dsn"}
            },
            "tables": [
                {
                    "name": t.name,
                    "comment": t.comment,
                    "primary_key": list(t.primary_key),
                    "row_count_tier": t.row_count_tier,
                    "time_bounds": t.time_bounds,
                    "indexes": list(t.indexes),
                    "columns": [
                        {
                            "name": c.name,
                            "data_type": c.data_type,
                            "nullable": c.nullable,
                            "comment": c.comment,
                            "role": c.role,
                            "sensitive": c.sensitive,
                            "is_primary_key": c.is_primary_key,
                            "is_foreign_key": c.is_foreign_key,
                            "profile": c.profile.to_dict() if c.profile else None,
                        }
                        for c in t.columns
                    ],
                }
                for t in self.tables
            ],
            "relations": [
                {
                    "src_table": r.src_table,
                    "src_column": r.src_column,
                    "dst_table": r.dst_table,
                    "dst_column": r.dst_column,
                    "kind": r.kind,
                    "confidence": r.confidence,
                    "evidence": list(r.evidence)[:8],
                    "risk": r.risk,
                    "allow_auto_join": r.allow_auto_join,
                }
                for r in self.relations
            ],
            "candidate_measures": [asdict(m) for m in self.candidate_measures],
            "candidate_dimensions": list(self.candidate_dimensions),
            "readiness": self.readiness.to_dict() if self.readiness else None,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "profiling_policy": dict(self.profiling_policy or {}),
        }


def scrub_catalog_for_model(cat: SemanticCatalog, *, max_tables: int = 80) -> dict[str, Any]:
    """Bounded view for LLM — no credentials, no row samples."""
    pub = cat.to_public_dict()
    pub.pop("identity", None)
    tables = pub.get("tables") or []
    pub["tables"] = tables[:max_tables]
    # strip index noise if huge
    for t in pub["tables"]:
        t.pop("indexes", None)
        for c in t.get("columns") or []:
            if c.get("sensitive"):
                c["name"] = c["name"]  # keep name for planning but mark
    return pub


def compute_schema_fingerprint(ddl_or_payload: str) -> str:
    norm = " ".join((ddl_or_payload or "").split()).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def catalog_is_stale(cat: SemanticCatalog, *, current_fingerprint: str) -> bool:
    if not cat.schema_fingerprint or not current_fingerprint:
        return True
    if cat.schema_fingerprint != current_fingerprint:
        return True
    if cat.expires_at and time.time() > float(cat.expires_at):
        return True
    return False


def analysis_allowed(cat: SemanticCatalog) -> bool:
    st = cat.readiness.status if cat.readiness else ReadinessStatus.BLOCKED
    if isinstance(st, str):
        st = ReadinessStatus(st)
    return st in (ReadinessStatus.READY, ReadinessStatus.DEGRADED)


def require_analysis_ready(cat: SemanticCatalog) -> None:
    if not analysis_allowed(cat):
        reasons = (cat.readiness.reasons if cat.readiness else []) or ["catalog blocked"]
        raise PermissionError("BLOCKED: " + "; ".join(reasons[:5]))


class JoinPolicy:
    """Only high-confidence edges may auto-join; low never."""

    def __init__(self, catalog: SemanticCatalog, high_threshold: float = 0.85):
        self.catalog = catalog
        self.high_threshold = high_threshold
        self._edges = {
            (r.src_table, r.dst_table): r
            for r in catalog.relations
            if r.allow_auto_join and r.confidence >= high_threshold
        }
        # bidirectional lookup for convenience
        for r in catalog.relations:
            if r.allow_auto_join and r.confidence >= high_threshold:
                self._edges.setdefault((r.dst_table, r.src_table), r)

    def can_auto_join(self, a: str, b: str) -> bool:
        if (a, b) in self._edges or (b, a) in self._edges:
            return True
        # also check allow_auto_join flag directly
        for r in self.catalog.relations:
            if {r.src_table, r.dst_table} == {a, b}:
                if r.allow_auto_join and r.confidence >= self.high_threshold:
                    return True
                return False
        return False

    def join_path(self, a: str, b: str) -> Optional[RelationEdge]:
        for r in self.catalog.relations:
            if {r.src_table, r.dst_table} == {a, b} and r.allow_auto_join:
                return r
        return None

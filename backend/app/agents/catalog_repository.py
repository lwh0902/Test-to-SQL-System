"""SemanticCatalog repository — versioned persistence for main-chain load (R2)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.agents.semantic_catalog import (
    BoundedColumnStats,
    CandidateMeasure,
    ColumnProfile,
    ReadinessReport,
    ReadinessStatus,
    RelationEdge,
    SemanticCatalog,
    TableProfile,
    catalog_is_stale,
)


def _default_root() -> Path:
    env = os.getenv("DATAPILOT_CATALOG_DIR")
    if env:
        return Path(env)
    # repo-local default
    return Path(__file__).resolve().parents[3] / "eval" / "catalog_store"


class CatalogRepository:
    """Filesystem-backed catalog store keyed by space_id / database_id."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (key or "unknown"))
        return self.root / f"{safe}.json"

    def save(self, key: str, catalog: SemanticCatalog) -> dict[str, Any]:
        payload = catalog.to_public_dict()
        payload["_repo"] = {
            "key": key,
            "saved_at": time.time(),
            "version": catalog.version,
            "schema_fingerprint": catalog.schema_fingerprint,
        }
        path = self._path(key)
        with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(path), "key": key, "fingerprint": catalog.schema_fingerprint}

    def load(self, key: str) -> Optional[SemanticCatalog]:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return catalog_from_public_dict(data)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.is_file():
            path.unlink()
            return True
        return False

    def is_stale(self, key: str, *, current_fingerprint: str) -> bool:
        cat = self.load(key)
        if not cat:
            return True
        return catalog_is_stale(cat, current_fingerprint=current_fingerprint)


def catalog_from_public_dict(data: dict[str, Any]) -> SemanticCatalog:
    tables: list[TableProfile] = []
    for t in data.get("tables") or []:
        cols = [
            ColumnProfile(
                name=c.get("name") or "",
                data_type=c.get("data_type") or "",
                nullable=bool(c.get("nullable", True)),
                comment=c.get("comment") or "",
                role=c.get("role") or "unknown",
                sensitive=bool(c.get("sensitive")),
                is_primary_key=bool(c.get("is_primary_key")),
                is_foreign_key=bool(c.get("is_foreign_key")),
                profile=BoundedColumnStats.from_dict(c.get("profile")),
            )
            for c in (t.get("columns") or [])
            if isinstance(c, dict)
        ]
        tables.append(
            TableProfile(
                name=t.get("name") or "",
                comment=t.get("comment") or "",
                columns=cols,
                primary_key=list(t.get("primary_key") or []),
                indexes=list(t.get("indexes") or []),
                row_count_tier=t.get("row_count_tier") or "unknown",
                time_bounds=dict(t.get("time_bounds") or {}),
            )
        )
    relations = [
        RelationEdge(
            src_table=r.get("src_table") or "",
            src_column=r.get("src_column") or "",
            dst_table=r.get("dst_table") or "",
            dst_column=r.get("dst_column") or "",
            kind=r.get("kind") or "inferred",
            confidence=float(r.get("confidence") or 0),
            evidence=list(r.get("evidence") or []),
            risk=r.get("risk") or "",
            allow_auto_join=bool(r.get("allow_auto_join")),
        )
        for r in (data.get("relations") or [])
        if isinstance(r, dict)
    ]
    measures = [
        CandidateMeasure(
            table=m.get("table") or "",
            field=m.get("field") or "",
            aggregations=list(m.get("aggregations") or ["sum"]),
            business_label=m.get("business_label") or "",
            role=m.get("role") or "amount",
        )
        for m in (data.get("candidate_measures") or [])
        if isinstance(m, dict)
    ]
    rd = data.get("readiness") or {}
    try:
        st = ReadinessStatus(str(rd.get("status") or "BLOCKED"))
    except Exception:
        st = ReadinessStatus.BLOCKED
    readiness = ReadinessReport(
        status=st,
        reasons=list(rd.get("reasons") or []),
        limited_capabilities=list(rd.get("limited_capabilities") or []),
        checked_at=float(rd.get("checked_at") or time.time()),
    )
    return SemanticCatalog(
        database_id=str(data.get("database_id") or ""),
        version=str(data.get("version") or "1"),
        schema_fingerprint=str(data.get("schema_fingerprint") or ""),
        tables=tables,
        relations=relations,
        candidate_measures=measures,
        candidate_dimensions=list(data.get("candidate_dimensions") or []),
        readiness=readiness,
        created_at=float(data.get("created_at") or time.time()),
        expires_at=data.get("expires_at"),
        engine=str(data.get("engine") or "mysql"),
        identity=dict(data.get("identity") or {}),
        profiling_policy=dict(data.get("profiling_policy") or {}),
    )


_default_repo: CatalogRepository | None = None


def get_catalog_repository() -> CatalogRepository:
    global _default_repo
    if _default_repo is None:
        _default_repo = CatalogRepository()
    return _default_repo

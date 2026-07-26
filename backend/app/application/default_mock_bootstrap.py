"""Startup / small-pilot bootstrap: auto-connect the two preset mock spaces.

Preset spaces:
  - tech_quality  → MySQL db `datacheck` (users / scan_records / api_logs / feature_events)
  - ecommerce     → MySQL db `datacheck` (ecom_* tables)

On API startup (when DATAPILOT_AUTO_MOCK is on, default true for non-prod):
  1. Ensure analysis_spaces rows exist (best-effort)
  2. Live-profile each space if missing or forced
  3. Register in-process connection for GuardedMySQLExecutor

Users who log in can chat immediately without manual Profile.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# System / auth / agent runtime tables that must never enter business catalogs
_SYSTEM_TABLE_PREFIXES = (
    "auth_",
    "agent_",
    "a2a_",
    "chat_",
    "session_",
    "metric_",
    "db_",
    "analysis_",
    "refresh_",
    "trace",
)
_SYSTEM_TABLE_EXACT = {
    "analysis_spaces",
    "analysis_tasks",
    "auth_users",
    "chat_messages",
    "chat_sessions",
    "db_connections",
    "metric_templates",
    "refresh_tokens",
    "session_memories",
    "traces",
    "trace_steps",
    "a2a_messages",
    "agent_artifacts",
    "agent_experience_memories",
    "agent_working_memories",
}

# tech_quality business tables (legacy mock in datacheck)
TECH_QUALITY_TABLES = {
    "users",
    "scan_records",
    "feature_events",
    "api_logs",
}

# ecommerce business tables
ECOMMERCE_TABLE_PREFIXES = ("ecom_",)

PRESET_SPACES: tuple[dict[str, Any], ...] = (
    {
        "id": "tech_quality",
        "name": "技术质量分析",
        "description": "扫描成功率、API 性能、错误分布等技术质量指标（内置 mock）",
        "icon": "bug",
        "dataset_id": "tech_quality",
        "sort_order": 1,
        "include_tables": TECH_QUALITY_TABLES,
        "table_prefixes": None,
    },
    {
        "id": "ecommerce",
        "name": "电商经营分析",
        "description": "销售额、订单数、转化率等电商经营指标（内置 mock）",
        "icon": "shopping",
        "dataset_id": "ecommerce",
        "sort_order": 2,
        "include_tables": None,
        "table_prefixes": ECOMMERCE_TABLE_PREFIXES,
    },
)

# Physical DB name for both presets (tables live in same MySQL database)
DEFAULT_MOCK_DATABASE = "datacheck"


@dataclass
class BootstrapResult:
    enabled: bool = True
    spaces: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "spaces": self.spaces,
            "errors": self.errors,
        }


def auto_mock_enabled() -> bool:
    """Default ON. Set DATAPILOT_AUTO_MOCK=0 to disable (e.g. pure unit tests)."""
    flag = os.getenv("DATAPILOT_AUTO_MOCK", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _mysql_cfg() -> dict[str, Any]:
    return {
        "host": os.getenv("MYSQL_HOST") or os.getenv("DB_HOST") or "127.0.0.1",
        "port": int(os.getenv("MYSQL_PORT") or os.getenv("DB_PORT") or "3306"),
        "user": os.getenv("MYSQL_USER") or os.getenv("DB_USER") or "root",
        "password": os.getenv("MYSQL_PASSWORD")
        if os.getenv("MYSQL_PASSWORD") is not None
        else (os.getenv("DB_PASSWORD") or ""),
        "database": os.getenv("DATAPILOT_MOCK_DATABASE") or DEFAULT_MOCK_DATABASE,
    }


def ensure_preset_spaces() -> dict[str, Any]:
    """INSERT/UPDATE the two system spaces. Best-effort — never hard-fail bootstrap."""
    from sqlalchemy import text

    from app.core.database import engine

    try:
        with engine.connect() as conn:
            for s in PRESET_SPACES:
                conn.execute(
                    text(
                        """
                        INSERT INTO analysis_spaces
                          (id, name, description, icon, dataset_id, sort_order, status)
                        VALUES
                          (:id, :name, :description, :icon, :dataset_id, :sort_order, 'active')
                        ON DUPLICATE KEY UPDATE
                          name = VALUES(name),
                          description = VALUES(description),
                          icon = VALUES(icon),
                          dataset_id = VALUES(dataset_id),
                          sort_order = VALUES(sort_order),
                          status = 'active'
                        """
                    ),
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "description": s["description"],
                        "icon": s["icon"],
                        "dataset_id": s["dataset_id"],
                        "sort_order": s["sort_order"],
                    },
                )
            conn.commit()
        return {"ok": True}
    except Exception as e:
        logger.warning("ensure_preset_spaces skipped: %s", e)
        return {"ok": False, "error": str(e)}


def _space_ready(space_id: str) -> bool:
    try:
        from app.agents.catalog_repository import get_catalog_repository
        from app.agents.semantic_catalog import analysis_allowed
        from app.application.connection_registry import get_space_connection

        cat = get_catalog_repository().load(space_id)
        if cat is None or not analysis_allowed(cat):
            return False
        conn = get_space_connection(space_id)
        return bool(conn and conn.get("database"))
    except Exception:
        return False


def _preset_by_id(space_id: str) -> dict[str, Any] | None:
    for s in PRESET_SPACES:
        if s["id"] == space_id:
            return s
    return None


def profile_preset_space(space_id: str, *, force: bool = False) -> dict[str, Any]:
    """Profile one preset against mock MySQL and register connection."""
    if not force and _space_ready(space_id):
        return {"space_id": space_id, "status": "already_ready", "ok": True}

    preset = _preset_by_id(space_id) or {}
    cfg = _mysql_cfg()
    from app.agents.catalog_repository import get_catalog_repository
    from app.agents.live_profiler import catalog_contains_secrets, profile_live_mysql
    from app.agents.semantic_catalog import analysis_allowed
    from app.application.connection_registry import register_space_connection

    include = preset.get("include_tables")
    prefixes = preset.get("table_prefixes")
    cat = profile_live_mysql(
        host=str(cfg["host"]),
        port=int(cfg["port"]),
        user=str(cfg["user"]),
        password=str(cfg["password"] or ""),
        database=str(cfg["database"]),
        database_id=space_id,
        collect_stats=True,
        collect_time_bounds=True,
        include_tables=include,
        table_prefixes=list(prefixes) if prefixes else None,
        exclude_tables=_SYSTEM_TABLE_EXACT,
    )
    # Drop any remaining system tables that slipped through prefixes
    if cat.tables:
        filtered = [
            t
            for t in cat.tables
            if t.name not in _SYSTEM_TABLE_EXACT
            and not any(t.name.startswith(p) for p in _SYSTEM_TABLE_PREFIXES)
        ]
        if include:
            filtered = [t for t in filtered if t.name in include]
        if prefixes:
            filtered = [
                t for t in filtered if any(t.name.startswith(p) for p in prefixes)
            ]
        if filtered and len(filtered) != len(cat.tables):
            cat.tables = filtered
            # relations only among kept
            kept = {t.name for t in filtered}
            cat.relations = [
                r
                for r in (cat.relations or [])
                if r.src_table in kept and r.dst_table in kept
            ]

    if cat.tables:
        # Recompute fingerprint/readiness after business-table filter
        import json

        from app.agents.profiler import compute_readiness
        from app.agents.semantic_catalog import compute_schema_fingerprint

        fp_payload = json.dumps(
            {
                "db": cfg["database"],
                "tables": [
                    {
                        "n": t.name,
                        "c": [(c.name, c.data_type, c.is_primary_key) for c in t.columns],
                        "pk": t.primary_key,
                    }
                    for t in cat.tables
                ],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        cat.schema_fingerprint = compute_schema_fingerprint(fp_payload)
        cat.readiness = compute_readiness(cat)

    leaked = catalog_contains_secrets(cat)
    if leaked:
        return {"space_id": space_id, "ok": False, "error": f"secret_leak:{leaked}"}

    if not cat.tables:
        return {
            "space_id": space_id,
            "ok": False,
            "error": "no_business_tables_matched",
        }

    saved = get_catalog_repository().save(space_id, cat)
    register_space_connection(
        space_id,
        {
            "host": cfg["host"],
            "port": cfg["port"],
            "user": cfg["user"],
            "password": cfg["password"],
            "database": cfg["database"],
        },
    )
    rd = cat.readiness.to_dict() if cat.readiness else {}
    return {
        "space_id": space_id,
        "ok": True,
        "status": rd.get("status") or "READY",
        "analysis_allowed": analysis_allowed(cat),
        "table_count": len(cat.tables),
        "tables": [t.name for t in cat.tables],
        "schema_fingerprint": cat.schema_fingerprint,
        "database": cfg["database"],
        "saved": saved,
    }


def bootstrap_default_mocks(*, force: bool = False) -> BootstrapResult:
    """Idempotent bootstrap used at process startup."""
    if not auto_mock_enabled():
        logger.info("DATAPILOT_AUTO_MOCK disabled — skip default mock bootstrap")
        return BootstrapResult(enabled=False)

    result = BootstrapResult(enabled=True)
    space_res = ensure_preset_spaces()
    if not space_res.get("ok"):
        # non-fatal: catalog+connection still usable if spaces already seeded
        result.errors.append(f"ensure_preset_spaces:{space_res.get('error')}")

    for s in PRESET_SPACES:
        sid = s["id"]
        try:
            info = profile_preset_space(sid, force=force)
            result.spaces.append(info)
            if not info.get("ok"):
                result.errors.append(f"{sid}:{info.get('error')}")
            else:
                logger.info(
                    "default mock ready space=%s status=%s tables=%s db=%s",
                    sid,
                    info.get("status"),
                    info.get("table_count"),
                    info.get("database"),
                )
        except Exception as e:
            msg = f"{sid}: {e}"
            logger.exception("default mock profile failed space=%s", sid)
            result.errors.append(msg)
            result.spaces.append({"space_id": sid, "ok": False, "error": str(e)})
    return result

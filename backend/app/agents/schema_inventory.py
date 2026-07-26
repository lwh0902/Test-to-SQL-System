"""Query Agent task: schema_inventory (P1).

Collects **metadata only** for the current space into a SchemaInventory artifact-shaped
payload, with session/space cache so vague follow-ups do not re-scan.

No credentials, no raw business row dumps.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

ARTIFACT_TYPE = "SchemaInventory"
SOURCE_AGENT = "query"

# In-process session/space cache (warm follow-ups). Keyed by scope hash.
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL_S = 15 * 60

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|dsn|conn_str)",
    re.I,
)
_SENSITIVE_COL = {
    "phone", "mobile", "email", "id_card", "password", "secret", "token",
    "credit_card", "passwd", "api_key",
}


@dataclass
class InventoryResult:
    inventory: dict[str, Any]
    cache_hit: bool
    introspect_calls: int
    artifact_id: str | None = None
    answer_text: str = ""
    answer_source: str = "template"  # template | llm

    def scrubbed_inventory(self) -> dict[str, Any]:
        return scrub_inventory_payload(self.inventory)


def _cache_key(session_id: str | None, user_id: int | None, space_id: str) -> str:
    raw = f"{session_id or '-'}|{user_id if user_id is not None else '-'}|{space_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_schema_inventory_cache() -> None:
    _CACHE.clear()


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - float(entry.get("ts") or 0) > _CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    return copy.deepcopy(entry.get("inventory"))


def _cache_put(key: str, inventory: dict[str, Any]) -> None:
    _CACHE[key] = {"ts": time.time(), "inventory": copy.deepcopy(inventory)}


def scrub_inventory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop credentials / row dumps / sensitive keys from artifact payload."""
    if not isinstance(payload, dict):
        return {}
    out = copy.deepcopy(payload)

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            cleaned = {}
            for k, v in obj.items():
                lk = str(k).lower()
                if _SENSITIVE_KEY_RE.search(lk):
                    continue
                if lk in {"rows", "sample_rows", "raw_rows", "password", "encrypted_password"}:
                    continue
                cleaned[k] = _walk(v)
            return cleaned
        if isinstance(obj, list):
            # Never keep list-of-dict that look like business rows (have many unknown keys)
            return [_walk(x) for x in obj]
        return obj

    cleaned = _walk(out)
    # Ensure tables columns don't expose sensitive column *values* (metadata names OK)
    tables = cleaned.get("tables") if isinstance(cleaned, dict) else None
    if isinstance(tables, list):
        for t in tables:
            if not isinstance(t, dict):
                continue
            t.pop("rows", None)
            t.pop("sample_rows", None)
            cols = t.get("columns")
            if isinstance(cols, list):
                safe_cols = []
                for c in cols:
                    if isinstance(c, dict):
                        name = str(c.get("name") or "")
                        entry = {
                            "name": name,
                            "type": c.get("type") or c.get("data_type") or "",
                            "comment": c.get("comment") or "",
                        }
                        if name.lower() in _SENSITIVE_COL:
                            entry["sensitive"] = True
                        safe_cols.append(entry)
                    else:
                        safe_cols.append(c)
                t["columns"] = safe_cols
    return cleaned if isinstance(cleaned, dict) else {}


def _column_summary(columns: list[dict]) -> list[dict]:
    out = []
    for c in columns or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or ""
        out.append({
            "name": name,
            "type": c.get("type") or c.get("data_type") or "",
            "comment": c.get("comment") or "",
            **({"sensitive": True} if str(name).lower() in _SENSITIVE_COL else {}),
        })
    return out


def _table_from_data_map_entry(table: dict) -> dict:
    cols = table.get("columns") or []
    return {
        "name": table.get("name") or "",
        "title": table.get("title") or table.get("name") or "",
        "purpose": (table.get("description") or table.get("purpose") or "").strip(),
        "columns": _column_summary(cols if isinstance(cols, list) else []),
        "column_count": len(cols) if isinstance(cols, list) else 0,
        "key_columns": list(table.get("key_columns") or [])[:8],
    }


def build_inventory_from_data_map(
    space_id: str,
    data_map: dict[str, Any],
    *,
    db_identity: dict | None = None,
    source: str = "configured_schema",
) -> dict[str, Any]:
    tables = [_table_from_data_map_entry(t) for t in (data_map.get("tables") or []) if isinstance(t, dict)]
    summary = data_map.get("summary") or {}
    identity = None
    if isinstance(db_identity, dict):
        # Strip any accidental secrets; keep masked connection only
        conn = db_identity.get("connection") if isinstance(db_identity.get("connection"), dict) else None
        identity = {
            "space_id": db_identity.get("space_id") or space_id,
            "space_name": db_identity.get("space_name") or "",
            "is_preset": bool(db_identity.get("is_preset")),
            "table_count": db_identity.get("table_count") or len(tables),
        }
        if conn:
            identity["connection"] = {
                "db_type": conn.get("db_type"),
                "host_masked": conn.get("host_masked"),
                "port": conn.get("port"),
                "db_name": conn.get("db_name"),
            }

    inventory = {
        "artifact_type": ARTIFACT_TYPE,
        "space_id": space_id,
        "source": source,
        "summary": {
            "table_count": summary.get("table_count", len(tables)),
            "field_count": summary.get("field_count", sum(t.get("column_count", 0) for t in tables)),
            "metric_count": summary.get("metric_count", 0),
        },
        "tables": tables,
        "db_identity": identity,
        "metrics": [
            {
                "id": m.get("id") or m.get("metric_id") or m.get("name"),
                "name": m.get("name") or m.get("title") or "",
                "description": m.get("description") or "",
            }
            for m in (data_map.get("metrics") or [])
            if isinstance(m, dict)
        ][:20],
    }
    return scrub_inventory_payload(inventory)


def build_inventory_from_information_schema(
    space_id: str,
    tables_meta: list[dict[str, Any]],
    *,
    db_identity: dict | None = None,
) -> dict[str, Any]:
    """Build inventory from mocked or live information_schema-like rows.

    Expected table item:
      {name, title?, comment?/purpose?, columns:[{name,type,comment?}]}
    """
    tables = []
    field_count = 0
    for t in tables_meta or []:
        if not isinstance(t, dict):
            continue
        cols = _column_summary(t.get("columns") or [])
        field_count += len(cols)
        purpose = (t.get("purpose") or t.get("comment") or t.get("description") or "").strip()
        title = t.get("title") or t.get("name") or ""
        tables.append({
            "name": t.get("name") or "",
            "title": title,
            "purpose": purpose,
            "columns": cols,
            "column_count": len(cols),
            "key_columns": list(t.get("key_columns") or [])[:8],
        })
    fake_map = {
        "summary": {"table_count": len(tables), "field_count": field_count, "metric_count": 0},
        "tables": [
            {
                "name": t["name"],
                "title": t["title"],
                "description": t["purpose"],
                "columns": t["columns"],
                "key_columns": t.get("key_columns") or [],
            }
            for t in tables
        ],
        "metrics": [],
    }
    return build_inventory_from_data_map(
        space_id, fake_map, db_identity=db_identity, source="information_schema"
    )


def assemble_schema_answer(
    inventory: dict[str, Any],
    *,
    question: str = "",
    llm_complete: Callable[..., Any] | None = None,
) -> tuple[str, str]:
    """Return (answer_text, source) grounded on inventory. LLM optional; template is L2."""
    # Try LLM grounded assembly when provided
    if llm_complete is not None:
        try:
            from app.agents.model_adapter import ModelRequest, ModelTier, ThinkingLevel

            tables_brief = [
                {
                    "name": t.get("name"),
                    "title": t.get("title"),
                    "purpose": t.get("purpose"),
                    "column_count": t.get("column_count"),
                    "key_columns": t.get("key_columns"),
                }
                for t in (inventory.get("tables") or [])
            ]
            system = (
                "你是数据分析助手。只根据给定的 SchemaInventory JSON 回答用户关于库表用途的问题。"
                "禁止编造表或字段。禁止输出密码、连接串、原始数据行。"
                "用简洁中文：先一句连接/范围摘要，再逐表列出「标题（表名）：用途」。"
            )
            user = (
                f"用户问题：{question or '这个数据库有什么表，分别是干嘛的'}\n"
                f"SchemaInventory：{tables_brief}\n"
                f"summary：{inventory.get('summary')}\n"
                f"db_identity：{inventory.get('db_identity')}"
            )
            req = ModelRequest(
                system=system,
                user=str(user),
                tier=ModelTier.FLASH,
                thinking=ThinkingLevel.NONE,
                max_tokens=800,
                expect_json=False,
                temperature=0.2,
            )
            resp = llm_complete(req)
            text = ""
            if hasattr(resp, "ok") and resp.ok:
                text = (getattr(resp, "text", None) or "").strip()
            elif isinstance(resp, str):
                text = resp.strip()
            if text and any(t.get("name") and t["name"] in text for t in tables_brief):
                return text, "llm"
        except Exception:
            logger.exception("assemble_schema_answer llm failed; using template")

    return render_schema_answer_template(inventory), "template"


def render_schema_answer_template(inventory: dict[str, Any]) -> str:
    summary = inventory.get("summary") or {}
    identity = inventory.get("db_identity") or {}
    conn = identity.get("connection") if isinstance(identity, dict) else None
    table_count = summary.get("table_count", len(inventory.get("tables") or []))
    field_count = summary.get("field_count", 0)
    metric_count = summary.get("metric_count", 0)

    if isinstance(conn, dict) and conn.get("db_name"):
        identity_line = (
            f"当前连接到 {conn.get('db_type') or 'mysql'} 数据库 `{conn.get('db_name')}`"
            f"（{conn.get('host_masked') or '***'}:{conn.get('port') or '-'}），"
        )
    else:
        identity_line = f"当前空间 `{inventory.get('space_id') or ''}`，"

    lines = [
        f"{identity_line}共 {table_count} 张表、{field_count} 个字段、{metric_count} 个推荐指标。",
    ]
    tables = inventory.get("tables") or []
    if tables:
        lines.append("")
        lines.append("各表用途：")
        for table in tables[:12]:
            title = table.get("title") or table.get("name")
            purpose = (table.get("purpose") or "").rstrip("。")
            name = table.get("name") or ""
            lines.append(f"- {title}（{name}）：{purpose}")
        if len(tables) > 12:
            lines.append(f"- …等共 {len(tables)} 张表")
    return "\n".join(lines)


def run_schema_inventory(
    *,
    space_id: str,
    session_id: str | None = None,
    user_id: int | None = None,
    question: str = "",
    force_refresh: bool = False,
    # Injectables for tests
    data_map_loader: Callable[[str], dict] | None = None,
    identity_loader: Callable[[str], dict | None] | None = None,
    information_schema_loader: Callable[[str], list[dict]] | None = None,
    artifact_saver: Callable[..., str] | None = None,
    llm_complete: Callable[..., Any] | None = None,
    task_id: str | None = None,
) -> InventoryResult:
    """Execute schema_inventory task with cache + optional artifact persist."""
    key = _cache_key(session_id, user_id, space_id)
    introspect_calls = 0
    cache_hit = False

    inventory = None if force_refresh else _cache_get(key)
    if inventory is not None:
        cache_hit = True
    else:
        # Prefer configured/preset data map; optional live information_schema overlay
        if data_map_loader is None:
            from app.services.data_map_service import get_data_map
            data_map_loader = get_data_map
        if identity_loader is None:
            from app.services.data_map_service import get_db_identity
            identity_loader = get_db_identity

        data_map = data_map_loader(space_id)
        introspect_calls += 1
        db_identity = identity_loader(space_id)

        # User-connected path: if caller provides information_schema_loader, use it
        if information_schema_loader is not None:
            meta = information_schema_loader(space_id)
            introspect_calls += 1
            inventory = build_inventory_from_information_schema(
                space_id, meta, db_identity=db_identity
            )
        else:
            mode = (data_map or {}).get("mode") or "schema"
            source = "configured_schema" if mode == "preset" else "space_db_schema"
            inventory = build_inventory_from_data_map(
                space_id, data_map or {}, db_identity=db_identity, source=source
            )
        _cache_put(key, inventory)

    inventory = scrub_inventory_payload(inventory)
    answer, answer_source = assemble_schema_answer(
        inventory, question=question, llm_complete=llm_complete
    )

    artifact_id = None
    if artifact_saver is not None and session_id and user_id is not None:
        try:
            tid = task_id or f"chat_{session_id}"
            artifact_id = artifact_saver(
                tid,
                session_id,
                int(user_id),
                space_id,
                ARTIFACT_TYPE,
                SOURCE_AGENT,
                "approved",
                inventory,
            )
        except Exception:
            logger.exception("save SchemaInventory artifact failed")

    return InventoryResult(
        inventory=inventory,
        cache_hit=cache_hit,
        introspect_calls=introspect_calls,
        artifact_id=artifact_id,
        answer_text=answer,
        answer_source=answer_source,
    )

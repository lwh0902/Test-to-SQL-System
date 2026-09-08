"""Automatic per-space business-name dictionary and safe filter resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

from app.agents.analysis_spec import FilterExpr


@dataclass(frozen=True)
class EntityValue:
    dimension: str
    canonical_value: str
    aliases: tuple[str, ...] = ()


@dataclass
class EntityResolution:
    filters: list[FilterExpr] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)


_NAME_DIMENSIONS = frozenset({"channel_name", "supplier_name", "product_name"})


def ensure_entity_value_table() -> bool:
    """Best-effort bootstrap for installations that have not run migration 007 yet."""
    from app.core.database import engine as system_engine

    try:
        with system_engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS semantic_entity_values (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    space_id VARCHAR(64) NOT NULL,
                    dimension_id VARCHAR(128) NOT NULL,
                    canonical_value VARCHAR(255) NOT NULL,
                    aliases JSON DEFAULT NULL,
                    source_table VARCHAR(128) NOT NULL,
                    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_space_dimension_value (space_id, dimension_id, canonical_value),
                    INDEX idx_space_dimension (space_id, dimension_id)
                ) ENGINE=InnoDB COMMENT='自动同步的业务实体名称字典，不存订单明细'
            """))
            conn.commit()
        return True
    except Exception:
        return False


def sync_entity_values(
    *, space_id: str, model: Any, connection: dict[str, Any], max_values: int = 20_000
) -> int:
    """Read bounded distinct names from the business DB into the system DB."""
    from app.core.database import engine as system_engine

    name_dimensions = [d for d in model.dimensions if d.field in _NAME_DIMENSIONS]
    if not name_dimensions or not connection.get("database"):
        return 0
    if not ensure_entity_value_table():
        return 0
    url = "mysql+pymysql://{}:{}@{}:{}/{}?charset=utf8mb4".format(
        quote_plus(str(connection.get("user") or "root")),
        quote_plus(str(connection.get("password") or "")),
        connection.get("host") or "127.0.0.1", int(connection.get("port") or 3306),
        connection["database"],
    )
    business_engine = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    count = 0
    try:
        with business_engine.connect() as business, system_engine.connect() as system:
            for dimension in name_dimensions:
                entity = model.entity(dimension.entity)
                if entity is None:
                    continue
                sql = text(
                    f"SELECT DISTINCT `{dimension.field}` AS value FROM `{entity.table}` "
                    f"WHERE `{dimension.field}` IS NOT NULL AND `{dimension.field}` <> '' "
                    f"LIMIT {max(1, min(int(max_values), 20_000))}"
                )
                rows = business.execute(sql).fetchall()
                for row in rows:
                    value = str(row[0]).strip()
                    if not value:
                        continue
                    system.execute(text("""
                        INSERT INTO semantic_entity_values
                            (space_id, dimension_id, canonical_value, aliases, source_table, last_seen_at)
                        VALUES (:space_id, :dimension_id, :value, JSON_ARRAY(), :source_table, NOW())
                        ON DUPLICATE KEY UPDATE last_seen_at=NOW(), source_table=VALUES(source_table)
                    """), {
                        "space_id": space_id, "dimension_id": dimension.id,
                        "value": value, "source_table": entity.table,
                    })
                    count += 1
            system.commit()
    finally:
        business_engine.dispose()
    return count


def sync_entity_values_if_stale(
    *, space_id: str, model: Any, connection: dict[str, Any], max_age_seconds: int = 300
) -> int:
    """Avoid hitting the business DB for every query while keeping names fresh."""
    from app.core.database import engine as system_engine

    if not ensure_entity_value_table():
        return 0
    try:
        with system_engine.connect() as conn:
            fresh = conn.execute(text("""
                SELECT MAX(last_seen_at) >= DATE_SUB(NOW(), INTERVAL :seconds SECOND)
                FROM semantic_entity_values WHERE space_id = :space_id
            """), {"space_id": space_id, "seconds": max(1, int(max_age_seconds))}).scalar()
        if fresh:
            return 0
    except Exception:
        # Migration may not yet have been applied. Query execution must remain available.
        return 0
    return sync_entity_values(space_id=space_id, model=model, connection=connection)


def load_entity_values(
    space_id: str,
    dimensions: Iterable[str],
    *,
    field_by_dimension: Mapping[str, str] | None = None,
) -> list[EntityValue]:
    from app.core.database import engine as system_engine

    ids = [str(x) for x in dimensions if x]
    if not ids:
        return []
    params = {f"d{i}": value for i, value in enumerate(ids)}
    placeholders = ", ".join(f":d{i}" for i in range(len(ids)))
    try:
        with system_engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT dimension_id, canonical_value, aliases
                FROM semantic_entity_values
                WHERE space_id = :space_id AND dimension_id IN ({placeholders})
            """), {"space_id": space_id, **params}).fetchall()
    except Exception:
        return []
    result = []
    for row in rows:
        aliases = row[2] if isinstance(row[2], list) else []
        dimension = (field_by_dimension or {}).get(str(row[0]), str(row[0]))
        result.append(EntityValue(dimension, str(row[1]), tuple(str(x) for x in aliases)))
    return result


def resolve_filter_values(
    filters: list[FilterExpr], question: str, values: Iterable[EntityValue]
) -> EntityResolution:
    """Replace only with a unique full business name visibly present in the question."""
    by_dimension: dict[str, list[EntityValue]] = {}
    for value in values:
        if value.dimension in _NAME_DIMENSIONS and value.canonical_value:
            by_dimension.setdefault(value.dimension, []).append(value)

    resolved: list[FilterExpr] = []
    ambiguities: list[str] = []
    q = re.sub(r"\s+", "", question or "")
    q_names = re.sub(r"(?i)GMV|成交额|交易额|订单数|订单量|取消率|退款率", "", q)
    seen_dimensions = {item.field for item in filters}
    for item in filters:
        if item.field not in _NAME_DIMENSIONS:
            resolved.append(item)
            continue
        original = str(item.value or "")
        candidates = []
        for entry in by_dimension.get(item.field, []):
            names = (entry.canonical_value, *entry.aliases)
            if entry.canonical_value in q_names or any(alias and alias in q_names for alias in names):
                candidates.append(entry)
        # The model may split one real name across city + shortened name. Require
        # the full canonical value to occur in the user's text before repair.
        unique = {x.canonical_value: x for x in candidates}
        known = {entry.canonical_value for entry in by_dimension.get(item.field, [])}
        if len(unique) == 1:
            canonical = next(iter(unique.values())).canonical_value
            resolved.append(FilterExpr(item.field, item.op, canonical, item.table))
        elif len(unique) > 1:
            ambiguities.append(item.field)
        elif original and (original in q or original in known):
            # Keep an already-grounded inherited filter even if this turn
            # only changes metric/time and does not repeat the business name.
            resolved.append(item)
        else:
            ambiguities.append(item.field)

    # The dictionary is grounded in the customer's database, so a full canonical
    # name occurring verbatim in the question is safe to add even if the model
    # omitted the filter.  Do not infer partial names or choose among candidates.
    for dimension, entries in by_dimension.items():
        if dimension in seen_dimensions:
            continue
        matches = {
            entry.canonical_value: entry
            for entry in entries
            if entry.canonical_value in q_names
            or any(alias and alias in q_names for alias in entry.aliases)
        }
        if len(matches) == 1:
            resolved.append(FilterExpr(dimension, "=", next(iter(matches)), ""))
        elif len(matches) > 1:
            ambiguities.append(dimension)

    # A repaired full name subsumes accidental city splitting from the same phrase.
    if any(x.field == "channel_name" and str(x.value) in q for x in resolved):
        resolved = [x for x in resolved if x.field != "city"]
    return EntityResolution(filters=resolved, ambiguities=sorted(set(ambiguities)))

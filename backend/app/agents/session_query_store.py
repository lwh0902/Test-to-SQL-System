"""Persist travel_b2b query snapshots as session_queries + child rows."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text

from app.agents.semantic_snapshot import SemanticSnapshot


def persist_current_query(
    conn,
    *,
    session_id: str,
    user_id: int,
    space_id: str,
    version: int,
    snapshot: SemanticSnapshot,
    catalog_fingerprint: str = "",
    sql_text: str = "",
    chart_type: str = "",
) -> str:
    query_id = f"q_{uuid.uuid4().hex[:16]}"
    conn.execute(
        text(
            """
            UPDATE session_queries
            SET is_current = 0
            WHERE session_id=:session_id AND user_id=:user_id AND space_id=:space_id AND is_current = 1
            """
        ),
        {"session_id": session_id, "user_id": int(user_id), "space_id": space_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO session_queries (
              query_id, session_id, user_id, space_id, version, entity,
              time_start, time_end, time_field, time_grain, query_limit,
              model_version, catalog_fingerprint, sql_text, chart_type, is_current
            ) VALUES (
              :query_id, :session_id, :user_id, :space_id, :version, :entity,
              :time_start, :time_end, :time_field, :time_grain, :query_limit,
              :model_version, :catalog_fingerprint, :sql_text, :chart_type, 1
            )
            """
        ),
        {
            "query_id": query_id,
            "session_id": session_id,
            "user_id": int(user_id),
            "space_id": space_id,
            "version": int(version),
            "entity": snapshot.entity or "",
            "time_start": snapshot.time_start or "",
            "time_end": snapshot.time_end or "",
            "time_field": snapshot.time_field or "",
            "time_grain": snapshot.time_grain,
            "query_limit": snapshot.limit,
            "model_version": snapshot.model_version or "",
            "catalog_fingerprint": catalog_fingerprint or "",
            "sql_text": sql_text or "",
            "chart_type": chart_type or "",
        },
    )
    for position, metric_id in enumerate(snapshot.metrics):
        conn.execute(
            text(
                "INSERT INTO session_query_metrics (query_id, position, metric_id) "
                "VALUES (:query_id, :position, :metric_id)"
            ),
            {"query_id": query_id, "position": position, "metric_id": metric_id},
        )
    for position, dimension_id in enumerate(snapshot.dimensions):
        conn.execute(
            text(
                "INSERT INTO session_query_dimensions (query_id, position, dimension_id) "
                "VALUES (:query_id, :position, :dimension_id)"
            ),
            {"query_id": query_id, "position": position, "dimension_id": dimension_id},
        )
    for position, (field_name, op, value) in enumerate(snapshot.filters):
        conn.execute(
            text(
                "INSERT INTO session_query_filters (query_id, position, field_name, op, value_json) "
                "VALUES (:query_id, :position, :field_name, :op, :value_json)"
            ),
            {
                "query_id": query_id,
                "position": position,
                "field_name": field_name,
                "op": op or "=",
                "value_json": json.dumps(value, ensure_ascii=False, default=str),
            },
        )
    return query_id


def load_current_query(
    conn,
    *,
    session_id: str,
    user_id: int,
    space_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            SELECT query_id, version, entity, time_start, time_end, time_field, time_grain,
                   query_limit, model_version, catalog_fingerprint, sql_text, chart_type
            FROM session_queries
            WHERE session_id=:session_id AND user_id=:user_id AND space_id=:space_id AND is_current = 1
            ORDER BY version DESC
            LIMIT 1
            """
        ),
        {"session_id": session_id, "user_id": int(user_id), "space_id": space_id},
    ).fetchone()
    if not row:
        return None
    return _hydrate_query_row(conn, row)


def load_query_by_id(
    conn,
    query_id: str,
    *,
    user_id: int | None = None,
    space_id: str | None = None,
) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            SELECT query_id, version, entity, time_start, time_end, time_field, time_grain,
                   query_limit, model_version, catalog_fingerprint, sql_text, chart_type,
                   session_id, user_id, space_id
            FROM session_queries
            WHERE query_id=:query_id
            """
        ),
        {"query_id": query_id},
    ).fetchone()
    if not row:
        return None
    if user_id is not None and int(row[13]) != int(user_id):
        return None
    if space_id is not None and str(row[14] or "") != str(space_id):
        return None
    return _hydrate_query_row(conn, row)


def _hydrate_query_row(conn, row) -> dict[str, Any]:
    query_id = str(row[0])
    metrics = [
        str(item[0])
        for item in conn.execute(
            text("SELECT metric_id FROM session_query_metrics WHERE query_id=:id ORDER BY position"),
            {"id": query_id},
        ).fetchall()
    ]
    dimensions = [
        str(item[0])
        for item in conn.execute(
            text("SELECT dimension_id FROM session_query_dimensions WHERE query_id=:id ORDER BY position"),
            {"id": query_id},
        ).fetchall()
    ]
    filters = []
    for item in conn.execute(
        text(
            "SELECT field_name, op, value_json FROM session_query_filters "
            "WHERE query_id=:id ORDER BY position"
        ),
        {"id": query_id},
    ).fetchall():
        raw = item[2]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                pass
        filters.append({"field": str(item[0]), "op": str(item[1] or "="), "value": raw})
    return {
        "query_id": query_id,
        "version": int(row[1] or 0),
        "sql_text": str(row[10] or ""),
        "chart_type": str(row[11] or ""),
        "catalog_fingerprint": str(row[9] or ""),
        "snapshot": SemanticSnapshot(
            entity=str(row[2] or ""),
            metrics=tuple(metrics),
            dimensions=tuple(dimensions),
            filters=tuple((str(x["field"]), str(x["op"]), x["value"]) for x in filters),
            time_start=str(row[3] or ""),
            time_end=str(row[4] or ""),
            time_field=str(row[5] or ""),
            time_grain=row[6],
            limit=row[7],
            model_version=str(row[8] or ""),
        ),
    }

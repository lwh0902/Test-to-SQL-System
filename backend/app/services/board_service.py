"""Boards pin semantic query_id snapshots for travel_b2b."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agents.active_analysis_state import ActiveAnalysisState
from app.agents.analysis_state_repository import get_analysis_state_repository
from app.agents.catalog_repository import get_catalog_repository
from app.agents.semantic_snapshot import SemanticSnapshot, snapshot_asdict
from app.agents.session_query_store import load_query_by_id, persist_current_query
from app.core.database import engine
from app.services.semantic_model_service import get_semantic_model_service
from app.services.session_service import create_session

BOARD_SPACE = "travel_b2b"
_DEFAULT_TIME = ("2026-07-01", "2026-08-31")
_DEFAULT_TILES = (
    ("gmv_trend", "GMV 趋势", ("gmv",), (), "month"),
    ("cancel_rate", "取消率", ("cancel_rate",), (), None),
    ("channel_split", "渠道拆分", ("gmv",), ("channel_name",), None),
)


class BoardError(Exception):
    def __init__(self, message: str, *, code: str = "board_error"):
        super().__init__(message)
        self.code = code


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _require_board_space(space_id: str) -> None:
    if space_id != BOARD_SPACE:
        raise BoardError("当前空间不支持经营看板。", code="space_unsupported")


def unpublished_metrics(space_id: str, metric_ids: list[str]) -> list[str]:
    return get_semantic_model_service().unpublished_metric_ids(space_id, metric_ids)


def get_or_create_board(user_id: int, space_id: str) -> dict[str, Any]:
    _require_board_space(space_id)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT board_id, title FROM boards WHERE space_id=:space_id AND user_id=:user_id"),
            {"space_id": space_id, "user_id": int(user_id)},
        ).fetchone()
        created = False
        if row:
            board_id = str(row[0])
            title = str(row[1] or "经营看板")
        else:
            board_id = _new_id("board")
            title = "经营看板"
            try:
                conn.execute(
                    text(
                        "INSERT INTO boards (board_id, space_id, user_id, title) "
                        "VALUES (:board_id, :space_id, :user_id, :title)"
                    ),
                    {
                        "board_id": board_id,
                        "space_id": space_id,
                        "user_id": int(user_id),
                        "title": title,
                    },
                )
                conn.commit()
                created = True
            except IntegrityError:
                conn.rollback()
                row = conn.execute(
                    text(
                        "SELECT board_id, title FROM boards "
                        "WHERE space_id=:space_id AND user_id=:user_id"
                    ),
                    {"space_id": space_id, "user_id": int(user_id)},
                ).fetchone()
                if not row:
                    raise BoardError("无法创建看板。", code="board_create_failed")
                board_id = str(row[0])
                title = str(row[1] or "经营看板")
        tiles = _list_tiles(conn, board_id)
        if not tiles:
            tiles = _seed_default_tiles(
                conn, board_id=board_id, user_id=int(user_id), space_id=space_id
            )
            conn.commit()
        elif created:
            conn.commit()
    return {"board_id": board_id, "space_id": space_id, "title": title, "tiles": tiles}


def _list_tiles(conn, board_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT tile_id, query_id, title, seed_key, position "
            "FROM board_tiles WHERE board_id=:board_id ORDER BY position, created_at"
        ),
        {"board_id": board_id},
    ).fetchall()
    tiles = []
    for row in rows:
        loaded = load_query_by_id(conn, str(row[1]))
        snap = loaded["snapshot"] if loaded else None
        tiles.append({
            "tile_id": str(row[0]),
            "query_id": str(row[1]),
            "title": str(row[2]),
            "seed_key": str(row[3] or ""),
            "position": int(row[4] or 0),
            "metrics": list(snap.metrics) if snap else [],
            "dimensions": list(snap.dimensions) if snap else [],
            "filters": [
                {"field": field, "op": op, "value": value}
                for field, op, value in (snap.filters if snap else ())
            ],
            "time_range": (
                {"start": snap.time_start, "end": snap.time_end, "field": snap.time_field}
                if snap and snap.time_start else None
            ),
            "time_grain": snap.time_grain if snap else None,
        })
    return tiles


def _seed_default_tiles(conn, *, board_id: str, user_id: int, space_id: str) -> list[dict[str, Any]]:
    session_id = f"board:{board_id}"
    for index, (seed_key, title, metrics, dimensions, time_grain) in enumerate(_DEFAULT_TILES):
        snapshot = SemanticSnapshot(
            entity="order",
            metrics=metrics,
            dimensions=dimensions,
            filters=(),
            time_start=_DEFAULT_TIME[0],
            time_end=_DEFAULT_TIME[1],
            time_field="booked_at",
            time_grain=time_grain,
        )
        query_id = persist_current_query(
            conn,
            session_id=session_id,
            user_id=user_id,
            space_id=space_id,
            version=index + 1,
            snapshot=snapshot,
        )
        tile_id = _new_id("tile")
        conn.execute(
            text(
                "INSERT IGNORE INTO board_tiles (tile_id, board_id, query_id, title, seed_key, position) "
                "VALUES (:tile_id, :board_id, :query_id, :title, :seed_key, :position)"
            ),
            {
                "tile_id": tile_id,
                "board_id": board_id,
                "query_id": query_id,
                "title": title,
                "seed_key": seed_key,
                "position": index,
            },
        )
    return _list_tiles(conn, board_id)


def pin_query(*, user_id: int, space_id: str, query_id: str, title: str = "") -> dict[str, Any]:
    _require_board_space(space_id)
    board = get_or_create_board(user_id, space_id)
    board_id = board["board_id"]
    with engine.connect() as conn:
        loaded = load_query_by_id(conn, query_id, user_id=int(user_id), space_id=space_id)
        if loaded is None:
            raise BoardError("找不到可钉盘的查询快照。", code="query_not_found")
        snap = loaded["snapshot"]
        blocked = unpublished_metrics(space_id, list(snap.metrics))
        if blocked:
            raise BoardError("草稿或未发布指标不能上盘。", code="unpublished_metric")
        existing = conn.execute(
            text("SELECT tile_id, title FROM board_tiles WHERE board_id=:board_id AND query_id=:query_id"),
            {"board_id": board_id, "query_id": query_id},
        ).fetchone()
        if existing:
            return {
                "tile_id": str(existing[0]),
                "board_id": board_id,
                "query_id": query_id,
                "title": str(existing[1] or title or "查询"),
            }
        position = conn.execute(
            text("SELECT COALESCE(MAX(position), -1) + 1 FROM board_tiles WHERE board_id=:board_id"),
            {"board_id": board_id},
        ).scalar_one()
        tile_id = _new_id("tile")
        label = (title or "").strip() or ("、".join(snap.metrics) or "查询")
        conn.execute(
            text(
                "INSERT INTO board_tiles (tile_id, board_id, query_id, title, seed_key, position) "
                "VALUES (:tile_id, :board_id, :query_id, :title, :seed_key, :position)"
            ),
            {
                "tile_id": tile_id,
                "board_id": board_id,
                "query_id": query_id,
                "title": label,
                "seed_key": f"pin:{query_id}",
                "position": int(position),
            },
        )
        conn.commit()
    return {"tile_id": tile_id, "board_id": board_id, "query_id": query_id, "title": label}


def load_tile_for_user(tile_id: str, user_id: int, space_id: str) -> dict[str, Any]:
    _require_board_space(space_id)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT t.tile_id, t.board_id, t.query_id, t.title, t.seed_key, b.space_id, b.user_id
                FROM board_tiles t
                JOIN boards b ON b.board_id = t.board_id
                WHERE t.tile_id=:tile_id
                """
            ),
            {"tile_id": tile_id},
        ).fetchone()
        if not row or int(row[6]) != int(user_id) or str(row[5]) != space_id:
            raise BoardError("磁贴不存在。", code="tile_not_found")
        loaded = load_query_by_id(conn, str(row[2]), user_id=int(user_id), space_id=space_id)
        if loaded is None:
            raise BoardError("磁贴对应的查询快照不存在。", code="query_not_found")
        return {
            "tile_id": str(row[0]),
            "board_id": str(row[1]),
            "query_id": str(row[2]),
            "title": str(row[3]),
            "seed_key": str(row[4] or ""),
            "snapshot": loaded["snapshot"],
        }


def open_tile_chat(*, user_id: int, space_id: str, tile_id: str) -> dict[str, Any]:
    tile = load_tile_for_user(tile_id, user_id, space_id)
    snap = tile["snapshot"]
    blocked = unpublished_metrics(space_id, list(snap.metrics))
    if blocked:
        raise BoardError("草稿或未发布指标不能进入对话。", code="unpublished_metric")
    created = create_session(user_id, space_id, tile["title"])
    session_id = created["session_id"]
    fingerprint = ""
    catalog_version = ""
    try:
        cat = get_catalog_repository().load(space_id)
        if cat is not None:
            fingerprint = cat.schema_fingerprint or ""
            catalog_version = str(getattr(cat, "version", "") or "")
    except Exception:
        pass
    state = ActiveAnalysisState(
        session_id=session_id,
        user_id=int(user_id),
        space_id=space_id,
        last_analysis_spec_id=tile["query_id"],
        last_sql="",
        semantic_snapshot=snapshot_asdict(snap),
        catalog_fingerprint=fingerprint,
        catalog_version=catalog_version,
        version=0,
    )
    saved = get_analysis_state_repository().save(state, expected_version=0)
    if not saved.get("ok"):
        raise BoardError("无法把磁贴快照写入会话。", code="state_save_failed")
    return {
        "id": session_id,
        "session_id": session_id,
        "tile_id": tile_id,
        "query_id": tile["query_id"],
        "title": tile["title"],
        "space_id": space_id,
    }


async def refresh_tile(*, user_id: int, space_id: str, tile_id: str) -> dict[str, Any]:
    tile = load_tile_for_user(tile_id, user_id, space_id)
    snap = tile["snapshot"]
    blocked = unpublished_metrics(space_id, list(snap.metrics))
    if blocked:
        raise BoardError("草稿或未发布指标不能出数。", code="unpublished_metric")
    from uuid import uuid4

    from app.a2a.contracts import A2AMessage
    from app.a2a.dispatcher import Dispatcher
    from app.a2a.registry import registry as default_registry
    from app.agents.dispatcher_agent_call import _ensure_harness_registered
    from app.agents.semantic_query_runtime import turn_result_from_harness_payload

    _ensure_harness_registered()
    task_id = f"task_{uuid4().hex[:16]}"
    session_id = f"board:{tile['board_id']}"
    msg = A2AMessage(
        correlation_id=task_id,
        source_agent="supervisor",
        target_agent="query",
        idempotency_key=f"{task_id}:query:{uuid4().hex}",
        payload={
            "question": "",
            "original_question": tile["title"],
            "pinned_query_id": tile["query_id"],
            "supervisor_intent": "query",
            "task_spec": {"task_type": "pinned_refresh", "params": {}},
            "supervisor_params": {},
            "kernel_route": "analysis_kernel_v2",
        },
        task_id=task_id,
        session_id=session_id,
        user_id=int(user_id),
        space_id=space_id,
    )
    result = await Dispatcher(agent_registry=default_registry).deliver(msg)
    tr = turn_result_from_harness_payload(result, kernel_route="analysis_kernel_v2")
    evidence = tr.evidence if isinstance(tr.evidence, dict) else {}
    if tr.stop_reason == "unpublished_metric" or evidence.get("unpublished_metrics"):
        raise BoardError("草稿或未发布指标不能出数。", code="unpublished_metric")
    if tr.response_type == "error" and not tr.rows:
        raise BoardError(tr.message or "磁贴刷新失败。", code=tr.stop_reason or "refresh_failed")
    chart = tr.chart
    if chart is not None and hasattr(chart, "model_dump"):
        chart = chart.model_dump()
    return {
        "tile_id": tile["tile_id"],
        "board_id": tile["board_id"],
        "query_id": tile["query_id"],
        "title": tile["title"],
        "seed_key": tile["seed_key"],
        "rows": list(tr.rows or []),
        "columns": list(tr.columns or []),
        "rows_count": int(tr.rows_count or 0),
        "chart": chart,
        "sql": tr.sql or "",
        "metrics": list(snap.metrics),
        "dimensions": list(snap.dimensions),
        "filters": [
            {"field": field, "op": op, "value": value} for field, op, value in snap.filters
        ],
        "time_range": {
            "start": snap.time_start,
            "end": snap.time_end,
            "field": snap.time_field,
        } if snap.time_start else None,
        "time_grain": snap.time_grain,
        "via_harness": True,
        "patch_source": evidence.get("patch_source") or "pinned",
        "patch_ops": evidence.get("patch_ops") or ["pinned_refresh"],
        "semantic_parser": bool(evidence.get("semantic_parser")),
        "stop_reason": tr.stop_reason,
        "terminal_status": tr.terminal_status,
        "message": tr.message or "",
    }

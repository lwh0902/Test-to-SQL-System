"""会话服务 - 创建、查询、删除"""

import json
import uuid

from sqlalchemy import text

from app.core.database import engine


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


def create_session(user_id: int, space_id: str, title: str = "新对话") -> dict:
    session_id = _gen_id()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO chat_sessions (id, user_id, space_id, title)
            VALUES (:id, :user_id, :space_id, :title)
        """), {
            "id": session_id, "user_id": user_id,
            "space_id": space_id, "title": title,
        })
        conn.commit()
    return {"session_id": session_id, "space_id": space_id, "title": title}


def list_sessions(user_id: int, space_id: str | None = None) -> list[dict]:
    sql = "SELECT id, user_id, space_id, title, created_at, updated_at FROM chat_sessions WHERE user_id = :user_id"
    params: dict = {"user_id": user_id}
    if space_id:
        sql += " AND space_id = :space_id"
        params["space_id"] = space_id
    sql += " ORDER BY updated_at DESC LIMIT 50"

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [
            {"id": row[0], "user_id": row[1], "space_id": row[2],
             "title": row[3], "created_at": str(row[4]), "updated_at": str(row[5])}
            for row in result.fetchall()
        ]


def get_session(session_id: str, user_id: int) -> dict | None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, user_id, space_id, title, created_at, updated_at FROM chat_sessions WHERE id = :id AND user_id = :user_id"
        ), {"id": session_id, "user_id": user_id})
        row = result.fetchone()
        if not row:
            return None

        msg_result = conn.execute(text(
            "SELECT id, role, content, meta, created_at FROM chat_messages WHERE session_id = :sid ORDER BY created_at ASC, id ASC"
        ), {"sid": session_id})
        messages = []
        for m in msg_result.fetchall():
            msg = {"id": m[0], "role": m[1], "content": m[2], "created_at": str(m[4])}
            if m[3]:
                msg["meta"] = json.loads(m[3]) if isinstance(m[3], str) else m[3]
            messages.append(msg)

    bundle = None
    try:
        from app.services.agent_runtime_store import list_latest_diagnosis_bundle
        bundle = list_latest_diagnosis_bundle(session_id, user_id, row[2])
    except Exception:
        bundle = None

    active_state = None
    try:
        from app.agents.analysis_state_repository import get_analysis_state_repository
        st = get_analysis_state_repository().load(session_id, user_id, row[2])
        if st is not None:
            # public summary only — no full preview dump required for restore UI
            active_state = {
                "state_id": st.state_id,
                "version": st.version,
                "turn_count": st.turn_count,
                "active_subject": st.active_subject,
                "measures": list(st.measures or []),
                "dimensions": list(st.dimensions or []),
                "filters": list(st.filters or []),
                "time_range": st.time_range,
                "last_spec_fingerprint": st.last_spec_fingerprint,
                "catalog_fingerprint": st.catalog_fingerprint,
                "updated_at": st.updated_at,
            }
    except Exception:
        active_state = None

    diagnosis_summary = None
    try:
        from app.agents.diagnosis_summary import bundle_to_dict
        from app.agents.diagnosis_summary_repository import get_diagnosis_summary_repository

        ds = get_diagnosis_summary_repository().load(session_id, user_id, row[2])
        if ds is not None:
            diagnosis_summary = bundle_to_dict(ds)
    except Exception:
        diagnosis_summary = None

    return {
        "id": row[0], "user_id": row[1], "space_id": row[2],
        "title": row[3], "created_at": str(row[4]), "updated_at": str(row[5]),
        "messages": messages,
        "diagnosis_bundle": bundle,
        "active_analysis_state": active_state,
        "diagnosis_summary": diagnosis_summary,
    }


def delete_session(session_id: str, user_id: int) -> bool:
    space_id = None
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, space_id FROM chat_sessions WHERE id = :id AND user_id = :user_id"
        ), {"id": session_id, "user_id": user_id})
        row = result.fetchone()
        if not row:
            return False
        space_id = row[1]
        conn.execute(text("DELETE FROM chat_messages WHERE session_id = :sid"), {"sid": session_id})
        # session_memories 可能未迁移；缺表不应导致整个删除 500
        try:
            conn.execute(text("DELETE FROM session_memories WHERE session_id = :sid AND user_id = :user_id"), {"sid": session_id, "user_id": user_id})
        except Exception:
            pass
        conn.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": session_id})
        conn.commit()
    # ActiveAnalysisState disk store (R4)
    if space_id:
        try:
            from app.agents.analysis_state_repository import get_analysis_state_repository
            get_analysis_state_repository().delete(session_id, user_id, space_id)
        except Exception:
            pass
    return True


def rename_session(session_id: str, user_id: int, title: str) -> bool:
    """重命名会话；仅限所属用户。title 已在上层 trim/校验。"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "UPDATE chat_sessions SET title = :title WHERE id = :id AND user_id = :user_id"
        ), {"title": title, "id": session_id, "user_id": user_id})
        conn.commit()
        return result.rowcount > 0

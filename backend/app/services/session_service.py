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
            "SELECT id, role, content, meta, created_at FROM chat_messages WHERE session_id = :sid ORDER BY seq ASC"
        ), {"sid": session_id})
        messages = []
        for m in msg_result.fetchall():
            msg = {"id": m[0], "role": m[1], "content": m[2], "created_at": str(m[4])}
            if m[3]:
                msg["meta"] = json.loads(m[3]) if isinstance(m[3], str) else m[3]
            messages.append(msg)

    return {
        "id": row[0], "user_id": row[1], "space_id": row[2],
        "title": row[3], "created_at": str(row[4]), "updated_at": str(row[5]),
        "messages": messages,
    }


def delete_session(session_id: str, user_id: int) -> bool:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id FROM chat_sessions WHERE id = :id AND user_id = :user_id"
        ), {"id": session_id, "user_id": user_id})
        if not result.fetchone():
            return False
        conn.execute(text("DELETE FROM chat_messages WHERE session_id = :sid"), {"sid": session_id})
        conn.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": session_id})
        conn.commit()
    return True

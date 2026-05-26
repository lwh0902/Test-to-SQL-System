"""会话 API - 创建/列表/删除会话，消息历史"""

import uuid
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from pydantic import BaseModel

from app.core.database import engine

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


class CreateSessionRequest(BaseModel):
    user_id: int = 1
    space_id: str = "tech_quality"
    title: str = "新对话"


@router.post("")
def create_session(req: CreateSessionRequest):
    session_id = _gen_id()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO chat_sessions (id, user_id, space_id, title)
            VALUES (:id, :user_id, :space_id, :title)
        """), {
            "id": session_id, "user_id": req.user_id,
            "space_id": req.space_id, "title": req.title,
        })
        conn.commit()
    return {"session_id": session_id, "space_id": req.space_id, "title": req.title}


@router.get("")
def list_sessions(user_id: int = 1, space_id: str | None = None):
    sql = "SELECT id, user_id, space_id, title, created_at, updated_at FROM chat_sessions WHERE user_id = :user_id"
    params: dict = {"user_id": user_id}
    if space_id:
        sql += " AND space_id = :space_id"
        params["space_id"] = space_id
    sql += " ORDER BY updated_at DESC LIMIT 50"

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        sessions = []
        for row in result.fetchall():
            sessions.append({
                "id": row[0], "user_id": row[1], "space_id": row[2],
                "title": row[3], "created_at": str(row[4]), "updated_at": str(row[5]),
            })
    return {"sessions": sessions}


@router.get("/{session_id}")
def get_session(session_id: str):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, user_id, space_id, title, created_at, updated_at FROM chat_sessions WHERE id = :id"
        ), {"id": session_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")

        # 加载消息
        msg_result = conn.execute(text(
            "SELECT id, role, content, meta, created_at FROM chat_messages WHERE session_id = :sid ORDER BY created_at ASC"
        ), {"sid": session_id})
        messages = []
        for m in msg_result.fetchall():
            msg = {"id": m[0], "role": m[1], "content": m[2], "created_at": str(m[4])}
            if m[3]:
                import json
                msg["meta"] = json.loads(m[3]) if isinstance(m[3], str) else m[3]
            messages.append(msg)

    return {
        "id": row[0], "user_id": row[1], "space_id": row[2],
        "title": row[3], "created_at": str(row[4]), "updated_at": str(row[5]),
        "messages": messages,
    }


@router.delete("/{session_id}")
def delete_session(session_id: str):
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM chat_messages WHERE session_id = :sid"), {"sid": session_id})
        conn.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": session_id})
        conn.commit()
    return {"ok": True}

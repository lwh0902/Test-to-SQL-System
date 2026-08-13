"""集中资源授权：未知和越权资源统一隐藏为 404。"""

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database import engine


def get_space_for_access(space_id: str, user_id: int) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, user_id FROM analysis_spaces "
            "WHERE id = :space_id AND status = 'active' "
            "AND (user_id IS NULL OR user_id = :user_id)"
        ), {"space_id": space_id, "user_id": user_id}).fetchone()
    return {"id": row[0], "user_id": row[1]} if row else None


def require_space_access(space_id: str, user_id: int) -> dict:
    space = get_space_for_access(space_id, user_id)
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")
    return space


def require_admin(user: dict) -> None:
    """Guard actions that change platform-managed public data sources."""
    if str((user or {}).get("role") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def session_belongs_to_user_and_space(session_id: str, user_id: int, space_id: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT 1 FROM chat_sessions WHERE id = :session_id "
            "AND user_id = :user_id AND space_id = :space_id"
        ), {"session_id": session_id, "user_id": user_id, "space_id": space_id}).fetchone() is not None


def require_session_access(session_id: str | None, user_id: int, space_id: str) -> None:
    if session_id and not session_belongs_to_user_and_space(session_id, user_id, space_id):
        raise HTTPException(status_code=404, detail="会话不存在")


def require_trace_access(trace_id: str, user_id: int) -> None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM traces WHERE trace_id = :trace_id AND user_id = :user_id"
        ), {"trace_id": trace_id, "user_id": user_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trace 不存在")

"""会话 API - 创建/列表/删除会话，消息历史"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.services.session_service import create_session, list_sessions, get_session, delete_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    space_id: str = "tech_quality"
    title: str = "新对话"


@router.post("")
def create_session_endpoint(req: CreateSessionRequest, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    return create_session(user_id, req.space_id, req.title)


@router.get("")
def list_sessions_endpoint(space_id: str | None = None, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    return {"sessions": list_sessions(user_id, space_id)}


@router.get("/{session_id}")
def get_session_endpoint(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    session = get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.delete("/{session_id}")
def delete_session_endpoint(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    if not delete_session(session_id, user_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}

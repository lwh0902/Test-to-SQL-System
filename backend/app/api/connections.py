"""数据库连接 API - CRUD + 测试"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.services.db_connection_service import (
    create_connection, list_connections, test_connection, delete_connection,
)
from app.core.rate_limit import limiter
from app.services.audit_service import audit_security_event

router = APIRouter(prefix="/api/connections", tags=["connections"])


class CreateConnectionRequest(BaseModel):
    name: str
    host: str
    port: int = 3306
    db_user: str
    db_password: str
    db_name: str


@router.post("")
def create_connection_endpoint(req: CreateConnectionRequest, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    try:
        conn = create_connection(
            user_id=user_id, name=req.name, host=req.host, port=req.port,
            db_user=req.db_user, db_password=req.db_password, db_name=req.db_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return conn


@router.get("")
def list_connections_endpoint(user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    return {"connections": list_connections(user_id)}


@router.post("/{connection_id}/test")
@limiter.limit("10/minute")
def test_connection_endpoint(request: Request, connection_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    result = test_connection(connection_id, user_id)
    audit_security_event("connection_test", request.headers.get("X-Request-ID", ""), user_id, connection_id=connection_id, ok=result.get("ok", False))
    if not result.get("ok"):
        return {"ok": False, "code": "CONNECTION_FAILED", "message": "连接测试失败"}
    return result


@router.delete("/{connection_id}")
def delete_connection_endpoint(connection_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    if not delete_connection(connection_id, user_id):
        raise HTTPException(status_code=404, detail="连接不存在")
    return {"ok": True}

"""数据库连接 API - CRUD + 测试"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.services.db_connection_service import (
    create_connection, list_connections, test_connection,
    test_direct_connection, discover_schema_direct, delete_connection,
)

router = APIRouter(prefix="/api/connections", tags=["connections"])


class CreateConnectionRequest(BaseModel):
    name: str
    host: str
    port: int = 3306
    db_user: str
    db_password: str
    db_name: str


class TestDirectRequest(BaseModel):
    host: str
    port: int = 3306
    db_user: str
    db_password: str
    db_name: str


@router.post("")
def create_connection_endpoint(req: CreateConnectionRequest, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    conn = create_connection(
        user_id=user_id, name=req.name, host=req.host, port=req.port,
        db_user=req.db_user, db_password=req.db_password, db_name=req.db_name,
    )
    return conn


@router.get("")
def list_connections_endpoint(user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    return {"connections": list_connections(user_id)}


@router.post("/{connection_id}/test")
def test_connection_endpoint(connection_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    return test_connection(connection_id, user_id)


@router.post("/test-direct")
def test_direct_endpoint(req: TestDirectRequest, user: dict = Depends(get_current_user)):
    return test_direct_connection(req.host, req.port, req.db_user, req.db_password, req.db_name)


@router.post("/discover-schema")
def discover_schema_endpoint(req: TestDirectRequest, user: dict = Depends(get_current_user)):
    return discover_schema_direct(req.host, req.port, req.db_user, req.db_password, req.db_name)


@router.delete("/{connection_id}")
def delete_connection_endpoint(connection_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    if not delete_connection(connection_id, user_id):
        raise HTTPException(status_code=404, detail="连接不存在")
    return {"ok": True}

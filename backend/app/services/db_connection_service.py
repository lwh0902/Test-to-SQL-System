"""数据库连接服务 - CRUD + 测试连接"""

import uuid

from sqlalchemy import text

from app.core.database import engine
from app.core.crypto import encrypt_password, decrypt_password
from app.core.engine_registry import engine_registry


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


def create_connection(user_id: int, name: str, host: str, port: int,
                      db_user: str, db_password: str, db_name: str) -> dict:
    conn_id = _gen_id()
    encrypted_pw = encrypt_password(db_password)
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO db_connections (id, user_id, name, host, port, db_user, db_password_encrypted, db_name)
            VALUES (:id, :user_id, :name, :host, :port, :db_user, :pw, :db_name)
        """), {
            "id": conn_id, "user_id": user_id, "name": name,
            "host": host, "port": port, "db_user": db_user,
            "pw": encrypted_pw, "db_name": db_name,
        })
        conn.commit()
    return {"id": conn_id, "user_id": user_id, "name": name, "host": host,
            "port": port, "db_user": db_user, "db_name": db_name}


def list_connections(user_id: int) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, name, host, port, db_name, db_user, status, last_tested_at, created_at "
            "FROM db_connections WHERE user_id = :user_id AND status = 'active' ORDER BY created_at DESC"
        ), {"user_id": user_id})
        return [
            {"id": r[0], "name": r[1], "host": r[2], "port": r[3],
             "db_name": r[4], "db_user": r[5], "status": r[6],
             "last_tested_at": str(r[7]) if r[7] else None, "created_at": str(r[8])}
            for r in result.fetchall()
        ]


def get_connection(connection_id: str, user_id: int) -> dict | None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, user_id, name, host, port, db_name, db_user, db_password_encrypted, status "
            "FROM db_connections WHERE id = :id AND user_id = :user_id"
        ), {"id": connection_id, "user_id": user_id})
        row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "user_id": row[1], "name": row[2],
        "host": row[3], "port": row[4], "db_name": row[5],
        "db_user": row[6], "db_password_encrypted": row[7], "status": row[8],
    }


def test_connection(connection_id: str, user_id: int) -> dict:
    conn_data = get_connection(connection_id, user_id)
    if not conn_data:
        return {"ok": False, "error": "连接不存在"}
    try:
        password = decrypt_password(conn_data["db_password_encrypted"])
        ok = engine_registry.test_connection(
            host=conn_data["host"], port=conn_data["port"],
            db_user=conn_data["db_user"], db_password=password,
            db_name=conn_data["db_name"],
        )
        if ok:
            with engine.connect() as conn:
                conn.execute(text(
                    "UPDATE db_connections SET last_tested_at = NOW() WHERE id = :id"
                ), {"id": connection_id})
                conn.commit()
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def test_direct_connection(host: str, port: int, db_user: str, db_password: str, db_name: str) -> dict:
    try:
        ok = engine_registry.test_connection(host, port, db_user, db_password, db_name)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def discover_schema_direct(host: str, port: int, db_user: str, db_password: str, db_name: str) -> dict:
    try:
        schema = engine_registry.discover_schema(host, port, db_user, db_password, db_name)
        return {"ok": True, "schema": schema}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_connection(connection_id: str, user_id: int) -> bool:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id FROM db_connections WHERE id = :id AND user_id = :user_id"
        ), {"id": connection_id, "user_id": user_id})
        if not result.fetchone():
            return False
        conn.execute(text(
            "UPDATE db_connections SET status = 'inactive' WHERE id = :id"
        ), {"id": connection_id})
        conn.commit()
    return True

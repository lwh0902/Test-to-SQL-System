"""空间服务 - 空间查询、指标列表、用户空间创建"""

import json
import uuid

from sqlalchemy import text

from app.core.database import engine
from app.core.engine_registry import engine_registry
from app.core.crypto import decrypt_password


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


def list_spaces(user_id: int | None = None) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, name, description, icon, dataset_id, user_id, connection_id, db_schema, sort_order "
            "FROM analysis_spaces WHERE status = 'active' ORDER BY sort_order"
        ))
        spaces = []
        for row in result.fetchall():
            # 系统空间(无 user_id)对所有人可见；用户空间只对拥有者可见
            space_user_id = row[5]
            if space_user_id is not None and space_user_id != user_id:
                continue
            space = {
                "id": row[0], "name": row[1], "description": row[2],
                "icon": row[3], "dataset_id": row[4],
                "user_id": row[5], "connection_id": row[6],
                "db_schema": json.loads(row[7]) if isinstance(row[7], str) else row[7],
                "sort_order": row[8],
            }
            spaces.append(space)
    return spaces


def get_space(space_id: str) -> dict | None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, name, description, icon, dataset_id, user_id, connection_id, db_schema "
            "FROM analysis_spaces WHERE id = :id AND status = 'active'"
        ), {"id": space_id})
        row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "description": row[2], "icon": row[3],
        "dataset_id": row[4], "user_id": row[5], "connection_id": row[6],
        "db_schema": json.loads(row[7]) if isinstance(row[7], str) else row[7],
    }


def create_user_space(user_id: int, name: str, connection_id: str) -> dict:
    space_id = _gen_id()
    # 从连接信息获取 schema
    from app.services.db_connection_service import get_connection
    conn_data = get_connection(connection_id, user_id)
    if not conn_data:
        raise ValueError("连接不存在")

    db_schema = None
    try:
        password = decrypt_password(conn_data["db_password_encrypted"])
        schema_result = engine_registry.discover_schema(
            host=conn_data["host"], port=conn_data["port"],
            db_user=conn_data["db_user"], db_password=password,
            db_name=conn_data["db_name"],
        )
        db_schema = json.dumps(schema_result, ensure_ascii=False)
    except Exception:
        pass

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO analysis_spaces (id, name, connection_id, user_id, db_schema, dataset_id, status, sort_order)
            VALUES (:id, :name, :conn_id, :user_id, :schema, 'user', 'active', 100)
        """), {
            "id": space_id, "name": name, "conn_id": connection_id,
            "user_id": user_id, "schema": db_schema,
        })
        conn.commit()
    return {"id": space_id, "name": name, "connection_id": connection_id, "user_id": user_id}


def list_space_metrics(space_id: str) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT metric_key, name, description, config FROM metric_templates WHERE space_id = :space_id AND status = 'active' ORDER BY sort_order"
        ), {"space_id": space_id})
        metrics = []
        for row in result.fetchall():
            config = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            allowed_types = config.get("allowed_query_types", ["fact"])
            metrics.append({
                "key": row[0],
                "name": row[1],
                "description": row[2],
                "default_query_type": allowed_types[0] if allowed_types else "fact",
                "default_time_range": "last_7_days",
            })
    return metrics


def list_space_metric_configs(space_id: str) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT metric_key, name, description, config FROM metric_templates WHERE space_id = :space_id AND status = 'active' ORDER BY sort_order"
        ), {"space_id": space_id})
        metrics = []
        for row in result.fetchall():
            config = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            metrics.append({"key": row[0], "name": row[1], "description": row[2], "config": config})
    return metrics

"""分析空间 API"""

from fastapi import APIRouter
from sqlalchemy import text
import json

from app.core.database import engine

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


@router.get("")
def list_spaces():
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, name, description, icon, dataset_id, sort_order FROM analysis_spaces WHERE status = 'active' ORDER BY sort_order"
        ))
        spaces = []
        for row in result.fetchall():
            spaces.append({
                "id": row[0], "name": row[1], "description": row[2],
                "icon": row[3], "dataset_id": row[4], "sort_order": row[5],
            })
    return {"spaces": spaces}


@router.get("/{space_id}")
def get_space(space_id: str):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, name, description, icon, dataset_id FROM analysis_spaces WHERE id = :id AND status = 'active'"
        ), {"id": space_id})
        row = result.fetchone()
        if not row:
            return {"error": "space not found"}

        # 加载该空间的指标列表
        metrics_result = conn.execute(text(
            "SELECT metric_key, name, description, config FROM metric_templates WHERE space_id = :space_id AND status = 'active' ORDER BY sort_order"
        ), {"space_id": space_id})
        metrics = []
        for m in metrics_result.fetchall():
            config = json.loads(m[3]) if isinstance(m[3], str) else m[3]
            allowed_types = config.get("allowed_query_types", ["fact"])
            metrics.append({
                "key": m[0], "name": m[1], "description": m[2],
                "default_query_type": allowed_types[0] if allowed_types else "fact",
                "default_time_range": "last_7_days",
            })

    return {
        "id": row[0], "name": row[1], "description": row[2],
        "icon": row[3], "dataset_id": row[4],
        "metrics": metrics,
    }


@router.get("/{space_id}/metrics")
def list_space_metrics(space_id: str):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT metric_key, name, description, config FROM metric_templates WHERE space_id = :space_id AND status = 'active' ORDER BY sort_order"
        ), {"space_id": space_id})
        metrics = []
        for m in result.fetchall():
            config = json.loads(m[3]) if isinstance(m[3], str) else m[3]
            metrics.append({"key": m[0], "name": m[1], "description": m[2], "config": config})
    return {"metrics": metrics}

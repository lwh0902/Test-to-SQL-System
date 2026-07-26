"""Authorized download of ExportFile artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.agents.export import EXPORT_DIR
from app.core.auth import get_current_user
from app.services.agent_runtime_store import get_artifact
from app.services.authorization_service import require_session_access, require_space_access

router = APIRouter(prefix="/api/exports", tags=["exports"])


def get_export_artifact(
    artifact_id: str,
    session_id: str,
    user_id: int,
    space_id: str,
) -> dict | None:
    art = get_artifact(artifact_id, session_id, user_id, space_id)
    if not art or art.get("type") != "ExportFile":
        return None
    return art


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None


@router.get("/{artifact_id}/download")
def download_export(
    artifact_id: str,
    session_id: str = Query(...),
    space_id: str = Query(...),
    user: dict = Depends(get_current_user),
):
    require_space_access(space_id, user["user_id"])
    require_session_access(session_id, user["user_id"], space_id)

    art = get_export_artifact(artifact_id, session_id, user["user_id"], space_id)
    if not art:
        raise HTTPException(status_code=404, detail="导出不存在或无权访问")

    payload = art.get("payload") or {}
    if not payload.get("exported"):
        raise HTTPException(status_code=404, detail="导出未生成")

    # scope double-check (payload may carry owner fields)
    if int(payload.get("user_id", user["user_id"])) != int(user["user_id"]):
        raise HTTPException(status_code=403, detail="无权下载该导出")
    if payload.get("space_id") and payload.get("space_id") != space_id:
        raise HTTPException(status_code=403, detail="空间不匹配")
    if payload.get("session_id") and payload.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="会话不匹配")

    exp = _parse_expiry(payload.get("expires_at"))
    if exp is not None:
        now = datetime.now(timezone.utc)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if now > exp:
            raise HTTPException(status_code=410, detail="导出已过期")

    file_name = payload.get("file_name")
    if not file_name:
        raise HTTPException(status_code=404, detail="导出文件缺失")

    path = Path(EXPORT_DIR) / Path(file_name).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")

    media = payload.get("content_type") or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        filename=Path(file_name).name,
    )

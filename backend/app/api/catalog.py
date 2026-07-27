"""Catalog readiness API — Recovery R2."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agents.catalog_repository import get_catalog_repository
from app.agents.live_profiler import catalog_contains_secrets, profile_live_mysql
from app.agents.semantic_catalog import analysis_allowed
from app.application.connection_registry import register_space_connection
from app.core.auth import get_current_user
from app.services.authorization_service import require_space_access

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


class ProfileIn(BaseModel):
    space_id: str
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str
    collect_stats: bool = True
    collect_time_bounds: bool = True


@router.get("/{space_id}")
def get_catalog(space_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    require_space_access(space_id, user["user_id"])
    repo = get_catalog_repository()
    cat = repo.load(space_id)
    if not cat:
        raise HTTPException(status_code=404, detail="catalog_not_found")
    pub = cat.to_public_dict()
    return {
        "ok": True,
        "space_id": space_id,
        "readiness": pub.get("readiness"),
        "schema_fingerprint": pub.get("schema_fingerprint"),
        "analysis_allowed": analysis_allowed(cat),
        "catalog": pub,
        "secrets_leaked": catalog_contains_secrets(cat),
    }


@router.get("/{space_id}/readiness")
def get_readiness(space_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    require_space_access(space_id, user["user_id"])
    repo = get_catalog_repository()
    cat = repo.load(space_id)
    if not cat:
        return {
            "ok": False,
            "space_id": space_id,
            "status": "BLOCKED",
            "reasons": ["catalog_not_found"],
            "analysis_allowed": False,
        }
    rd = cat.readiness.to_dict() if cat.readiness else {"status": "BLOCKED"}
    return {
        "ok": True,
        "space_id": space_id,
        "status": rd.get("status"),
        "reasons": rd.get("reasons"),
        "limited_capabilities": rd.get("limited_capabilities"),
        "analysis_allowed": analysis_allowed(cat),
        "schema_fingerprint": cat.schema_fingerprint,
        "table_count": len(cat.tables),
        "column_count": sum(len(t.columns) for t in cat.tables),
    }


@router.post("/profile")
def profile_and_store(body: ProfileIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Live profile MySQL and persist catalog for space_id (main-chain load key)."""
    require_space_access(body.space_id, user["user_id"])
    try:
        cat = profile_live_mysql(
            host=body.host,
            port=body.port,
            user=body.user,
            password=body.password,
            database=body.database,
            database_id=body.space_id,
            collect_stats=body.collect_stats,
            collect_time_bounds=body.collect_time_bounds,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"profile_failed: {e}") from e

    leaked = catalog_contains_secrets(cat)
    if leaked:
        raise HTTPException(status_code=500, detail=f"secret_leak:{leaked}")

    repo = get_catalog_repository()
    saved = repo.save(body.space_id, cat)
    # memory-only connection for GuardedMySQLExecutor (never in catalog JSON)
    register_space_connection(
        body.space_id,
        {
            "host": body.host,
            "port": body.port,
            "user": body.user,
            "password": body.password,
            "database": body.database,
        },
    )
    pub = cat.to_public_dict()
    return {
        "ok": True,
        "space_id": body.space_id,
        "saved": saved,
        "readiness": pub.get("readiness"),
        "analysis_allowed": analysis_allowed(cat),
        "schema_fingerprint": cat.schema_fingerprint,
        "table_count": len(cat.tables),
        "column_count": sum(len(t.columns) for t in cat.tables),
        "relation_count": len(cat.relations),
        # do not echo password; catalog identity is scrubbed
        "identity": pub.get("identity"),
    }

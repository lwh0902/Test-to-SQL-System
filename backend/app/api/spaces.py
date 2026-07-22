"""分析空间 API"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.services.space_service import list_spaces, get_space, list_space_metrics, list_space_metric_configs, create_user_space
from app.services.data_map_service import get_data_map, profile_tables
from app.services.authorization_service import require_space_access
from app.core.rate_limit import limiter

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


class CreateUserSpaceRequest(BaseModel):
    name: str
    connection_id: str


@router.get("")
def list_spaces_endpoint(user: dict = Depends(get_current_user)):
    user_id = user.get("user_id")
    return {"spaces": list_spaces(user_id)}


@router.post("")
def create_space_endpoint(req: CreateUserSpaceRequest, user: dict = Depends(get_current_user)):
    user_id = user.get("user_id", 1)
    try:
        space = create_user_space(user_id, req.name, req.connection_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return space


@router.get("/{space_id}")
def get_space_endpoint(space_id: str, user: dict = Depends(get_current_user)):
    require_space_access(space_id, user["user_id"])
    space = get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")
    space["metrics"] = list_space_metrics(space_id)
    return space


@router.get("/{space_id}/metrics")
def list_metrics_endpoint(space_id: str, user: dict = Depends(get_current_user)):
    require_space_access(space_id, user["user_id"])
    return {"metrics": list_space_metric_configs(space_id)}


@router.get("/{space_id}/data-map")
def get_data_map_endpoint(space_id: str, user: dict = Depends(get_current_user)):
    require_space_access(space_id, user["user_id"])
    return {"data_map": get_data_map(space_id)}


@router.post("/{space_id}/profile")
@limiter.limit("5/minute")
def profile_tables_endpoint(request: Request, space_id: str, user: dict = Depends(get_current_user)):
    require_space_access(space_id, user["user_id"])
    return profile_tables(space_id)

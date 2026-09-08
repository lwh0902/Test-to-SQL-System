"""Board / tile APIs pin semantic query_id snapshots."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.services.authorization_service import require_space_access
from app.services.board_service import (
    BoardError,
    get_or_create_board,
    open_tile_chat,
    pin_query,
    refresh_tile,
)

router = APIRouter(prefix="/api/boards", tags=["boards"])


class PinTileRequest(BaseModel):
    space_id: str
    query_id: str
    title: str = ""


class TileActionRequest(BaseModel):
    space_id: str


def _raise_board_error(exc: BoardError) -> None:
    status = 400
    if exc.code in {"query_not_found", "tile_not_found"}:
        status = 404
    elif exc.code == "unpublished_metric":
        status = 409
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.get("")
def get_board(space_id: str, user: dict = Depends(get_current_user)):
    require_space_access(space_id, user["user_id"])
    try:
        return get_or_create_board(int(user["user_id"]), space_id)
    except BoardError as exc:
        _raise_board_error(exc)


@router.post("/tiles")
def pin_tile(req: PinTileRequest, user: dict = Depends(get_current_user)):
    require_space_access(req.space_id, user["user_id"])
    try:
        return pin_query(
            user_id=int(user["user_id"]),
            space_id=req.space_id,
            query_id=req.query_id,
            title=req.title,
        )
    except BoardError as exc:
        _raise_board_error(exc)


@router.post("/tiles/{tile_id}/refresh")
async def refresh_board_tile(
    tile_id: str, req: TileActionRequest, user: dict = Depends(get_current_user)
):
    require_space_access(req.space_id, user["user_id"])
    try:
        return await refresh_tile(
            user_id=int(user["user_id"]), space_id=req.space_id, tile_id=tile_id
        )
    except BoardError as exc:
        _raise_board_error(exc)


@router.post("/tiles/{tile_id}/open-chat")
def open_board_tile_chat(
    tile_id: str, req: TileActionRequest, user: dict = Depends(get_current_user)
):
    require_space_access(req.space_id, user["user_id"])
    try:
        return open_tile_chat(
            user_id=int(user["user_id"]), space_id=req.space_id, tile_id=tile_id
        )
    except BoardError as exc:
        _raise_board_error(exc)

"""认证 API - 登录（支持用户名/手机号）、注册、刷新、登出、获取用户信息"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import (
    create_access_token, create_refresh_token, decode_refresh_token,
    get_current_user, revoke_refresh_token, revoke_all_user_tokens,
    validate_password_strength,
)
from app.core.rate_limit import limiter
from app.services.auth_service import (
    authenticate_by_phone, authenticate_by_username,
    create_user_by_phone, check_phone_exists,
    validate_phone, get_user_by_id,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    phone: str | None = None
    username: str | None = None
    password: str


class RegisterRequest(BaseModel):
    phone: str
    password: str
    display_name: str
    role: str = "tester"


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


def _build_token_response(user: dict) -> TokenResponse:
    access = create_access_token(
        user_id=user["id"], role=user["role"],
        phone=user.get("phone"), username=user.get("username"),
    )
    refresh = create_refresh_token(user["id"])
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user={
            "id": user["id"],
            "username": user.get("username"),
            "phone": user.get("phone"),
            "display_name": user["display_name"],
            "role": user["role"],
        },
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, req: LoginRequest):
    if req.phone:
        user = authenticate_by_phone(req.phone, req.password)
    elif req.username:
        user = authenticate_by_username(req.username, req.password)
    else:
        raise HTTPException(status_code=400, detail="请提供 phone 或 username")

    if not user:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    return _build_token_response(user)


@router.post("/register", response_model=TokenResponse)
@limiter.limit("3/minute")
def register(request: Request, req: RegisterRequest):
    error = validate_phone(req.phone)
    if error:
        raise HTTPException(status_code=400, detail=error)
    pw_error = validate_password_strength(req.password)
    if pw_error:
        raise HTTPException(status_code=400, detail=pw_error)
    if check_phone_exists(req.phone):
        raise HTTPException(status_code=400, detail="该手机号已注册")

    user = create_user_by_phone(
        phone=req.phone,
        password=req.password,
        display_name=req.display_name,
        role=req.role,
    )
    return _build_token_response(user)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
def refresh_token(request: Request, req: RefreshRequest):
    payload = decode_refresh_token(req.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Refresh token 无效或已过期")
    user = get_user_by_id(payload["user_id"])
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    revoke_refresh_token(payload["id"])
    return _build_token_response(user)


@router.post("/logout")
def logout(user: dict = Depends(get_current_user)):
    revoke_all_user_tokens(user.get("user_id"))
    return {"ok": True}


@router.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    db_user = get_user_by_id(user["user_id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return db_user

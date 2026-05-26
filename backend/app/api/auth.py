"""认证 API - 登录、注册、获取用户信息"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import text
from pydantic import BaseModel

from app.core.database import engine
from app.core.auth import verify_password, create_access_token, decode_token, hash_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "tester"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def get_current_user(authorization: str = Header(None)) -> dict:
    """依赖注入：从 Authorization header 解析当前用户"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证信息")
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期或无效")
    return payload


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, username, display_name, role, password_hash, status FROM auth_users WHERE username = :username"
        ), {"username": req.username})
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if row[5] != "active":
        raise HTTPException(status_code=403, detail="账号已停用")
    if not verify_password(req.password, row[4]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user_id=row[0], username=row[1], role=row[3])
    return TokenResponse(
        access_token=token,
        user={"id": row[0], "username": row[1], "display_name": row[2], "role": row[3]},
    )


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest):
    with engine.connect() as conn:
        existing = conn.execute(text(
            "SELECT id FROM auth_users WHERE username = :username"
        ), {"username": req.username}).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")

        pw_hash = hash_password(req.password)
        conn.execute(text("""
            INSERT INTO auth_users (username, password_hash, display_name, role)
            VALUES (:username, :password_hash, :display_name, :role)
        """), {
            "username": req.username,
            "password_hash": pw_hash,
            "display_name": req.display_name,
            "role": req.role,
        })
        conn.commit()

        user_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        token = create_access_token(user_id=user_id, username=req.username, role=req.role)

    return TokenResponse(
        access_token=token,
        user={"id": user_id, "username": req.username, "display_name": req.display_name, "role": req.role},
    )


@router.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, username, display_name, role, status FROM auth_users WHERE username = :username"
        ), {"username": user["sub"]})
        row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"id": row[0], "username": row[1], "display_name": row[2], "role": row[3], "status": row[4]}

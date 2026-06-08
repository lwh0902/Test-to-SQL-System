"""JWT 认证工具"""

import re
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Header, HTTPException
from sqlalchemy import text

from app.core.config import JWT_SECRET, ACCESS_TOKEN_EXPIRE_HOURS, REFRESH_TOKEN_EXPIRE_DAYS

SECRET_KEY = JWT_SECRET
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("$2"):
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    return hashlib.sha256(plain.encode()).hexdigest() == hashed


def needs_bcrypt_upgrade(hashed: str) -> bool:
    return not hashed.startswith("$2")


def validate_password_strength(password: str) -> str | None:
    if len(password) < 8:
        return "密码至少 8 位"
    if len(password) > 128:
        return "密码不能超过 128 位"
    if not re.search(r"[A-Za-z]", password):
        return "密码需包含字母"
    if not re.search(r"[0-9]", password):
        return "密码需包含数字"
    return None


def create_access_token(user_id: int, role: str, phone: str | None = None, username: str | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "user_id": user_id,
        "role": role,
        "exp": expire,
    }
    if phone:
        payload["phone"] = phone
    if username:
        payload["username"] = username
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user(authorization: str = Header(None)) -> dict:
    """依赖注入：从 Authorization header 解析当前用户"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证信息")
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期或无效")
    return payload


def create_refresh_token(user_id: int) -> str:
    from app.core.database import engine
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (:uid, :hash, :exp)"
        ), {"uid": user_id, "hash": token_hash, "exp": expires_at})
        conn.commit()
    return raw


def decode_refresh_token(raw_token: str) -> dict | None:
    from app.core.database import engine
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, user_id, expires_at FROM refresh_tokens "
            "WHERE token_hash = :hash AND expires_at > NOW()"
        ), {"hash": token_hash})
        row = result.fetchone()
    if not row:
        return None
    return {"id": row[0], "user_id": row[1]}


def revoke_refresh_token(token_id: int) -> None:
    from app.core.database import engine
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM refresh_tokens WHERE id = :id"), {"id": token_id})
        conn.commit()


def revoke_all_user_tokens(user_id: int) -> None:
    from app.core.database import engine
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM refresh_tokens WHERE user_id = :uid"), {"uid": user_id})
        conn.commit()

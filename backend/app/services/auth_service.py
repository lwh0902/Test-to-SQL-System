"""认证服务 - 用户查询、创建、密码校验"""

import re

from sqlalchemy import text

from app.core.database import engine
from app.core.auth import verify_password, hash_password, needs_bcrypt_upgrade

_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def get_user_by_username(username: str) -> dict | None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, username, phone, display_name, role, password_hash, status FROM auth_users WHERE username = :username"
        ), {"username": username})
        row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "phone": row[2], "display_name": row[3],
        "role": row[4], "password_hash": row[5], "status": row[6],
    }


def get_user_by_phone(phone: str) -> dict | None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, username, phone, display_name, role, password_hash, status FROM auth_users WHERE phone = :phone"
        ), {"phone": phone})
        row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "phone": row[2], "display_name": row[3],
        "role": row[4], "password_hash": row[5], "status": row[6],
    }


def get_user_by_id(user_id: int) -> dict | None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id, username, phone, display_name, role, status FROM auth_users WHERE id = :id"
        ), {"id": user_id})
        row = result.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "phone": row[2],
        "display_name": row[3], "role": row[4], "status": row[5],
    }


def _upgrade_hash_if_needed(user_id: int, password: str, current_hash: str) -> None:
    if needs_bcrypt_upgrade(current_hash):
        new_hash = hash_password(password)
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE auth_users SET password_hash = :hash WHERE id = :id"
            ), {"hash": new_hash, "id": user_id})
            conn.commit()


def authenticate_by_username(username: str, password: str) -> dict | None:
    user = get_user_by_username(username)
    if not user or user["status"] != "active":
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    _upgrade_hash_if_needed(user["id"], password, user["password_hash"])
    return user


def authenticate_by_phone(phone: str, password: str) -> dict | None:
    user = get_user_by_phone(phone)
    if not user or user["status"] != "active":
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    _upgrade_hash_if_needed(user["id"], password, user["password_hash"])
    return user


def validate_phone(phone: str) -> str | None:
    if not _PHONE_PATTERN.match(phone):
        return "手机号格式不正确（需 11 位中国大陆手机号）"
    return None


def create_user_by_phone(phone: str, password: str, display_name: str, role: str = "tester") -> dict:
    pw_hash = hash_password(password)
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO auth_users (phone, password_hash, display_name, role)
            VALUES (:phone, :password_hash, :display_name, :role)
        """), {
            "phone": phone,
            "password_hash": pw_hash,
            "display_name": display_name,
            "role": role,
        })
        conn.commit()
        user_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    return {"id": user_id, "phone": phone, "display_name": display_name, "role": role}


def check_phone_exists(phone: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id FROM auth_users WHERE phone = :phone"
        ), {"phone": phone})
        return result.fetchone() is not None


def check_username_exists(username: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT id FROM auth_users WHERE username = :username"
        ), {"username": username})
        return result.fetchone() is not None

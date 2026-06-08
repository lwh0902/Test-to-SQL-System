"""AES-256-GCM 加解密 - 用于存储用户数据库连接密码"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import DB_ENCRYPTION_KEY


def _get_key() -> bytes:
    if not DB_ENCRYPTION_KEY:
        raise RuntimeError("DB_ENCRYPTION_KEY 未配置，无法加解密数据库密码")
    return base64.b64decode(DB_ENCRYPTION_KEY)


def encrypt_password(plain: str) -> str:
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plain.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_password(encrypted: str) -> str:
    key = _get_key()
    raw = base64.b64decode(encrypted)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode()

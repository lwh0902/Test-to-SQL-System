import base64
import os

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "datacheck")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "2"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# AES-256-GCM 密钥，base64 编码的 32 字节密钥
DB_ENCRYPTION_KEY = os.getenv("DB_ENCRYPTION_KEY", "")


def validate_security_config() -> None:
    """拒绝以弱密钥或错误加密配置启动服务。"""
    if JWT_SECRET in ("", "change-me-in-production", "datapilot-secret-key-change-in-prod") or len(JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET 必须为至少 32 字符的非默认随机值")
    try:
        key = base64.b64decode(DB_ENCRYPTION_KEY, validate=True)
    except Exception as exc:
        raise RuntimeError("DB_ENCRYPTION_KEY 必须是 base64 编码") from exc
    if len(key) != 32:
        raise RuntimeError("DB_ENCRYPTION_KEY 解码后必须为 32 字节")

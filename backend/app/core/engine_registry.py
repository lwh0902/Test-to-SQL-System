"""引擎注册表 - 按空间管理数据库连接，惰性创建同步 SQLAlchemy Engine"""

import threading
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from app.core.database import engine as system_engine
from app.core.crypto import decrypt_password


class EngineRegistry:
    def __init__(self) -> None:
        self._cache: dict[str, any] = {}
        self._lock = threading.Lock()

    def get_engine(self, space_id: str) -> any:
        with self._lock:
            if space_id in self._cache:
                return self._cache[space_id]

        conn_info = self._get_connection_info(space_id)
        if not conn_info:
            with self._lock:
                self._cache[space_id] = system_engine
            return system_engine

        host, port, db_user, db_password_encrypted, db_name = conn_info
        password = decrypt_password(db_password_encrypted)
        url = (
            f"mysql+pymysql://{quote_plus(str(db_user))}:{quote_plus(str(password))}"
            f"@{host}:{port}/{db_name}?charset=utf8mb4"
        )

        eng = create_engine(url, pool_pre_ping=True, pool_recycle=3600, pool_size=5, max_overflow=10)
        with self._lock:
            self._cache[space_id] = eng
        return eng

    def _get_connection_info(self, space_id: str) -> tuple | None:
        with system_engine.connect() as conn:
            result = conn.execute(text(
                "SELECT dc.host, dc.port, dc.db_user, dc.db_password_encrypted, dc.db_name "
                "FROM analysis_spaces sp "
                "JOIN db_connections dc ON sp.connection_id = dc.id "
                "WHERE sp.id = :space_id AND sp.user_id IS NOT NULL "
                "AND dc.user_id = sp.user_id AND dc.status = 'active'"
            ), {"space_id": space_id})
            return result.fetchone()

    def test_connection(self, host: str, port: int, db_user: str, db_password: str, db_name: str) -> bool:
        url = f"mysql+pymysql://{db_user}:{db_password}@{host}:{port}/{db_name}?charset=utf8mb4"
        eng = create_engine(url, pool_pre_ping=True)
        try:
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        finally:
            eng.dispose()

    def invalidate(self, space_id: str) -> None:
        with self._lock:
            eng = self._cache.pop(space_id, None)
        if eng and eng is not system_engine:
            eng.dispose()

    def discover_schema(self, host: str, port: int, db_user: str, db_password: str, db_name: str) -> dict:
        url = f"mysql+pymysql://{db_user}:{db_password}@{host}:{port}/{db_name}?charset=utf8mb4"
        eng = create_engine(url, pool_pre_ping=True)
        try:
            with eng.connect() as conn:
                tables_result = conn.execute(text(
                    "SELECT TABLE_NAME, TABLE_COMMENT FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = :db ORDER BY TABLE_NAME"
                ), {"db": db_name})
                schema = {}
                for t_row in tables_result.fetchall():
                    table_name = t_row[0]
                    cols_result = conn.execute(text(
                        "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT "
                        "FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl ORDER BY ORDINAL_POSITION"
                    ), {"db": db_name, "tbl": table_name})
                    columns = []
                    for c_row in cols_result.fetchall():
                        columns.append({
                            "name": c_row[0], "type": c_row[1],
                            "nullable": c_row[2] == "YES", "key": c_row[3],
                            "comment": c_row[4],
                        })
                    schema[table_name] = {
                        "comment": t_row[1],
                        "columns": columns,
                    }
            return schema
        finally:
            eng.dispose()


engine_registry = EngineRegistry()

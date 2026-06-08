"""查询执行服务"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.engine_registry import engine_registry

EXPLAIN_MAX_ROWS = 100000
QUERY_TIMEOUT_SECONDS = 30


def execute_query(sql: str, params: dict, space_id: str = "") -> tuple[list[str], list[dict]]:
    """执行 SQL 并返回 (columns, rows)，按 space_id 路由到正确的数据库引擎"""
    if not space_id:
        from app.core.database import engine
        eng = engine
    else:
        eng = engine_registry.get_engine(space_id)

    # EXPLAIN 预检（仅自由模式空间走用户库时）
    if space_id:
        explain_result = _explain_check(eng, sql, params)
        if explain_result:
            raise QuerySafeError(explain_result)

    with eng.connect() as conn:
        result = conn.execute(text(sql), params)
        columns = list(result.keys())
        raw_rows = result.fetchall()

        rows = []
        for row in raw_rows:
            row_dict = {}
            for col, val in zip(columns, row):
                row_dict[col] = _serialize(val)
            rows.append(row_dict)

        return columns, rows


def _explain_check(eng, sql: str, params: dict) -> str | None:
    """EXPLAIN 预检：估算扫描行数，返回错误信息或 None"""
    try:
        with eng.connect() as conn:
            explain_sql = f"EXPLAIN {sql}"
            result = conn.execute(text(explain_sql), params)
            for row in result.fetchall():
                # MySQL EXPLAIN 输出：type=ALL 全表扫描, rows=预估行数
                row_dict = {k: v for k, v in zip(result.keys(), row)}
                examined_rows = row_dict.get("rows", 0)
                if isinstance(examined_rows, (int, float)) and examined_rows > EXPLAIN_MAX_ROWS:
                    return f"查询预计扫描 {int(examined_rows)} 行，超过安全上限 {EXPLAIN_MAX_ROWS} 行，请添加更精确的 WHERE 条件"
    except QuerySafeError:
        raise
    except Exception:
        pass
    return None


def generate_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:12]}"


def _serialize(val):
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    return val


class QuerySafeError(Exception):
    """查询安全错误 — EXPLAIN 预检不通过"""
    pass

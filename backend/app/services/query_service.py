"""查询执行服务"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.database import engine


def execute_query(sql: str, params: dict) -> tuple[list[str], list[dict]]:
    """执行 SQL 并返回 (columns, rows)"""
    with engine.connect() as conn:
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

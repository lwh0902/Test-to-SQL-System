"""GuardedMySQLExecutor — read-only live MySQL execution (Recovery R3)."""

from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.agents.query_outcome import QueryOutcome, QueryOutcomeStatus


_WRITE = re.compile(
    r"(?is)\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE|CREATE|GRANT|REVOKE|LOAD\s+DATA)\b"
)
_MULTI = re.compile(r";\s*\S")
_UNION = re.compile(r"(?is)\bunion\b")


class GuardedMySQLExecutor:
    """Runtime SELECT-only execution for AnalysisSpec-compiled SQL.

    JOIN is allowed (compiler already validated via catalog JoinPolicy).
    FreeformSQLGuard is NOT used here — it rejects JOIN for ad-hoc freeform.
    Never logs password.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 3306,
        user: str,
        password: str = "",
        database: str,
        max_rows: int = 500,
        engine: Engine | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.user = user
        self.database = database
        self.max_rows = min(max(1, int(max_rows)), 500)
        self._password = password or ""
        self._own = engine is None
        self._engine = engine or self._make_engine()

    def _make_engine(self) -> Engine:
        u = quote_plus(self.user or "")
        p = quote_plus(self._password or "")
        url = (
            f"mysql+pymysql://{u}:{p}@{self.host}:{self.port}/"
            f"{self.database}?charset=utf8mb4"
        )
        return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)

    def close(self) -> None:
        if self._own and self._engine is not None:
            self._engine.dispose()

    def execute(self, sql: str, *, trace_id: str | None = None) -> QueryOutcome:
        t0 = time.perf_counter()
        raw = (sql or "").strip().rstrip(";")
        if not raw:
            return QueryOutcome.invalid_request(message="空 SQL", trace_id=trace_id)
        if _WRITE.search(raw):
            return QueryOutcome.sql_rejected(
                message="只读执行器拒绝写 SQL",
                sql=raw[:500],
                trace_id=trace_id,
            )
        if _MULTI.search(raw):
            return QueryOutcome.sql_rejected(
                message="禁止多语句",
                sql=raw[:500],
                trace_id=trace_id,
            )
        if _UNION.search(raw):
            return QueryOutcome.sql_rejected(
                message="禁止 UNION",
                sql=raw[:500],
                trace_id=trace_id,
            )
        if not re.match(r"^\s*SELECT\b", raw, re.I):
            return QueryOutcome.sql_rejected(
                message="仅允许 SELECT",
                sql=raw[:500],
                trace_id=trace_id,
            )

        # Clamp oversized LIMIT (hard cap 500)
        def _clamp_limit(s: str) -> str:
            m = re.search(r"(?is)\blimit\s+(\d+)\s*$", s.strip())
            if m:
                n = int(m.group(1))
                if n > self.max_rows:
                    return re.sub(r"(?is)\blimit\s+\d+\s*$", f"LIMIT {self.max_rows}", s.strip())
                return s
            if not re.search(r"(?i)\blimit\b", s):
                return f"{s} LIMIT {self.max_rows}"
            return s

        raw = _clamp_limit(raw)

        try:
            with self._engine.connect() as conn:
                # session readonly best-effort
                try:
                    conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                except Exception:
                    pass
                result = conn.execute(text(raw))
                cols = list(result.keys()) if result.returns_rows else []
                rows = []
                if result.returns_rows:
                    for i, row in enumerate(result):
                        if i >= self.max_rows:
                            break
                        if hasattr(row, "_mapping"):
                            rows.append({k: _jsonable(v) for k, v in dict(row._mapping).items()})
                        else:
                            rows.append({cols[j]: _jsonable(row[j]) for j in range(len(cols))})
            elapsed = (time.perf_counter() - t0) * 1000
            if rows:
                o = QueryOutcome.success_with_data(
                    rows=rows,
                    columns=cols,
                    sql=raw,
                    message=f"查询成功，返回 {len(rows)} 行",
                    trace_id=trace_id,
                )
            else:
                o = QueryOutcome.success_empty(
                    sql=raw,
                    columns=cols,
                    message="查询成功但结果为空",
                    trace_id=trace_id,
                )
            # stash latency lightly
            if o.allowed_next_actions is not None:
                pass
            o.message = (o.message or "") + f"（{elapsed:.0f}ms）"
            return o
        except Exception as e:
            msg = str(e)
            if "timeout" in msg.lower():
                return QueryOutcome.timeout(message=msg, sql=raw, trace_id=trace_id)
            return QueryOutcome.execution_error(message=msg[:500], sql=raw, trace_id=trace_id)


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    # date/datetime/decimal
    try:
        from decimal import Decimal
        from datetime import date, datetime

        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (date, datetime)):
            return v.isoformat()
    except Exception:
        pass
    return str(v)


def execute_sql_mysql(
    sql: str,
    *,
    host: str,
    port: int = 3306,
    user: str,
    password: str = "",
    database: str,
    max_rows: int = 100,
    trace_id: str | None = None,
    engine: Engine | None = None,
) -> QueryOutcome:
    ex = GuardedMySQLExecutor(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        max_rows=max_rows,
        engine=engine,
    )
    try:
        return ex.execute(sql, trace_id=trace_id)
    finally:
        if engine is None:
            ex.close()

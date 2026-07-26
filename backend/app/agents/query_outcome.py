"""QueryOutcome — single source of truth for query link control (dev_spec §4.5).

Statuses are mutually exclusive. rows_count=0 must never stand in for
PERMISSION_DENIED / SQL_REJECTED / EXECUTION_ERROR / TIMEOUT.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional
import time
import uuid


class QueryOutcomeStatus(str, Enum):
    SUCCESS_WITH_DATA = "SUCCESS_WITH_DATA"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_REQUEST = "INVALID_REQUEST"
    SQL_REJECTED = "SQL_REJECTED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


# Explicit supervisor policy matrix (fault matrix)
FAULT_MATRIX: dict[QueryOutcomeStatus, dict[str, Any]] = {
    QueryOutcomeStatus.SUCCESS_WITH_DATA: {
        "next_actions": ["stop_success", "insight", "clarify"],
        "allows_report": True,  # only with evidence + explicit intent
        "allows_insight": True,
        "user_terminal": False,
        "retryable": False,
    },
    QueryOutcomeStatus.SUCCESS_EMPTY: {
        "next_actions": ["relax_once", "stop_empty"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": True,
    },
    QueryOutcomeStatus.PERMISSION_DENIED: {
        "next_actions": ["stop_denied"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": False,
    },
    QueryOutcomeStatus.INVALID_REQUEST: {
        "next_actions": ["clarify", "explain_limit", "stop_error"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": False,
    },
    QueryOutcomeStatus.SQL_REJECTED: {
        "next_actions": ["explain_limit", "stop_error"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": False,
    },
    QueryOutcomeStatus.EXECUTION_ERROR: {
        "next_actions": ["stop_error"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": False,
    },
    QueryOutcomeStatus.TIMEOUT: {
        "next_actions": ["stop_error"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": True,
    },
    QueryOutcomeStatus.CANCELLED: {
        "next_actions": ["stop_error"],
        "allows_report": False,
        "allows_insight": False,
        "user_terminal": True,
        "retryable": False,
    },
}


def next_actions_for_outcome(outcome: "QueryOutcome", *, relax_used: bool = False) -> list[str]:
    row = FAULT_MATRIX.get(outcome.status) or {}
    acts = list(row.get("next_actions") or [])
    if outcome.status == QueryOutcomeStatus.SUCCESS_EMPTY and relax_used:
        acts = [a for a in acts if a != "relax_once"]
        if "stop_empty" not in acts:
            acts.append("stop_empty")
    return acts


@dataclass
class QueryOutcome:
    status: QueryOutcomeStatus
    message: str = ""
    analysis_spec_id: Optional[str] = None
    sql: Optional[str] = None
    sql_fingerprint: Optional[str] = None
    columns: list[str] = field(default_factory=list)
    rows_count: int = 0
    rows_preview: list[dict] = field(default_factory=list)
    data_time_range: Optional[dict] = None
    error_code: Optional[str] = None
    retryable: bool = False
    allowed_next_actions: list[str] = field(default_factory=list)
    trace_id: Optional[str] = None
    outcome_id: str = field(default_factory=lambda: f"qo_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)

    # --- factories ---

    @classmethod
    def success_with_data(
        cls,
        *,
        rows: list[dict],
        columns: list[str],
        sql: str = "",
        analysis_spec_id: Optional[str] = None,
        message: str = "",
        trace_id: Optional[str] = None,
        data_time_range: Optional[dict] = None,
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.SUCCESS_WITH_DATA,
            message=message or "查询成功",
            analysis_spec_id=analysis_spec_id,
            sql=sql,
            columns=list(columns or []),
            rows_count=len(rows or []),
            rows_preview=list(rows or [])[:20],
            data_time_range=data_time_range,
            retryable=False,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    @classmethod
    def success_empty(
        cls,
        *,
        sql: str = "",
        columns: Optional[list[str]] = None,
        analysis_spec_id: Optional[str] = None,
        message: str = "",
        trace_id: Optional[str] = None,
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.SUCCESS_EMPTY,
            message=message or "查询成功但结果为空",
            analysis_spec_id=analysis_spec_id,
            sql=sql,
            columns=list(columns or []),
            rows_count=0,
            retryable=True,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o, relax_used=False)
        return o

    @classmethod
    def permission_denied(
        cls,
        *,
        message: str = "权限不足",
        analysis_spec_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        error_code: str = "PERMISSION_DENIED",
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.PERMISSION_DENIED,
            message=message,
            analysis_spec_id=analysis_spec_id,
            rows_count=0,
            error_code=error_code,
            retryable=False,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    @classmethod
    def sql_rejected(
        cls,
        *,
        message: str = "SQL 被安全策略拒绝",
        analysis_spec_id: Optional[str] = None,
        sql: str = "",
        trace_id: Optional[str] = None,
        error_code: str = "SQL_REJECTED",
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.SQL_REJECTED,
            message=message,
            analysis_spec_id=analysis_spec_id,
            sql=sql,
            rows_count=0,
            error_code=error_code,
            retryable=False,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    @classmethod
    def execution_error(
        cls,
        *,
        message: str = "查询执行异常",
        analysis_spec_id: Optional[str] = None,
        sql: str = "",
        trace_id: Optional[str] = None,
        error_code: str = "EXECUTION_ERROR",
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.EXECUTION_ERROR,
            message=message,
            analysis_spec_id=analysis_spec_id,
            sql=sql,
            rows_count=0,
            error_code=error_code,
            retryable=False,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    @classmethod
    def timeout(
        cls,
        *,
        message: str = "查询超时",
        analysis_spec_id: Optional[str] = None,
        sql: str = "",
        trace_id: Optional[str] = None,
        error_code: str = "TIMEOUT",
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.TIMEOUT,
            message=message,
            analysis_spec_id=analysis_spec_id,
            sql=sql,
            rows_count=0,
            error_code=error_code,
            retryable=True,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    @classmethod
    def invalid_request(
        cls,
        *,
        message: str = "请求无效",
        analysis_spec_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.INVALID_REQUEST,
            message=message,
            analysis_spec_id=analysis_spec_id,
            rows_count=0,
            error_code="INVALID_REQUEST",
            retryable=False,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    @classmethod
    def cancelled(
        cls,
        *,
        message: str = "已取消",
        analysis_spec_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> "QueryOutcome":
        o = cls(
            status=QueryOutcomeStatus.CANCELLED,
            message=message,
            analysis_spec_id=analysis_spec_id,
            rows_count=0,
            error_code="CANCELLED",
            retryable=False,
            trace_id=trace_id,
        )
        o.allowed_next_actions = next_actions_for_outcome(o)
        return o

    # --- derived ---

    @property
    def is_success_with_data(self) -> bool:
        return self.status == QueryOutcomeStatus.SUCCESS_WITH_DATA and self.rows_count > 0

    @property
    def allows_insight(self) -> bool:
        return bool(FAULT_MATRIX[self.status]["allows_insight"]) and self.is_success_with_data

    @property
    def allows_report(self) -> bool:
        # hard gate: never report without data
        return bool(FAULT_MATRIX[self.status]["allows_report"]) and self.is_success_with_data

    def to_public_dict(self) -> dict[str, Any]:
        """Safe dict for SSE / artifacts (no secrets, no unbounded rows)."""
        return {
            "outcome_id": self.outcome_id,
            "status": self.status.value if isinstance(self.status, QueryOutcomeStatus) else str(self.status),
            "message": (self.message or "")[:500],
            "analysis_spec_id": self.analysis_spec_id,
            "sql": (self.sql or "")[:2000] if self.sql else None,
            "sql_fingerprint": self.sql_fingerprint,
            "columns": list(self.columns or [])[:50],
            "rows_count": int(self.rows_count or 0),
            "data_time_range": self.data_time_range,
            "error_code": self.error_code,
            "retryable": bool(self.retryable),
            "allowed_next_actions": list(self.allowed_next_actions or []),
            "allows_insight": self.allows_insight,
            "allows_report": self.allows_report,
            "trace_id": self.trace_id,
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["allows_insight"] = self.allows_insight
        d["allows_report"] = self.allows_report
        return d


def _msg_indicates_permission(msg: str, code: str = "") -> bool:
    blob = f"{code} {msg}".lower()
    keys = ("权限", "无权", "permission", "denied", "forbidden", "unauthorized")
    return any(k in blob for k in keys)


def _msg_indicates_sql_reject(msg: str, code: str = "") -> bool:
    blob = f"{code} {msg}".lower()
    keys = ("sql", "安全", "reject", "guard", "injection", "只读", "forbidden sql")
    return any(k in blob for k in keys) and ("拒绝" in msg or "reject" in blob or "denied" in blob or "guard" in blob)


def build_outcome_from_agent_state(state: Any) -> QueryOutcome:
    """Map legacy AgentState fields → QueryOutcome (Phase 1 bridge)."""
    response_type = str(getattr(state, "response_type", "") or "")
    message = str(getattr(state, "message", "") or "")
    error_code = str(getattr(state, "error_code", "") or "")
    sql = str(getattr(state, "sql", "") or "")
    rows = list(getattr(state, "rows", None) or [])
    columns = list(getattr(state, "columns", None) or [])
    trace_id = getattr(state, "trace_id", None)

    # Prefer explicit error_code
    code_u = error_code.upper()
    if code_u in {s.value for s in QueryOutcomeStatus}:
        st = QueryOutcomeStatus(code_u)
        if st == QueryOutcomeStatus.PERMISSION_DENIED:
            return QueryOutcome.permission_denied(message=message, trace_id=trace_id)
        if st == QueryOutcomeStatus.SQL_REJECTED:
            return QueryOutcome.sql_rejected(message=message, sql=sql, trace_id=trace_id)
        if st == QueryOutcomeStatus.EXECUTION_ERROR:
            return QueryOutcome.execution_error(message=message, sql=sql, trace_id=trace_id)
        if st == QueryOutcomeStatus.TIMEOUT:
            return QueryOutcome.timeout(message=message, sql=sql, trace_id=trace_id)
        if st == QueryOutcomeStatus.INVALID_REQUEST:
            return QueryOutcome.invalid_request(message=message, trace_id=trace_id)
        if st == QueryOutcomeStatus.CANCELLED:
            return QueryOutcome.cancelled(message=message, trace_id=trace_id)

    if response_type == "error" or response_type == "clarification" and _msg_indicates_permission(message, error_code):
        if _msg_indicates_permission(message, error_code):
            return QueryOutcome.permission_denied(message=message or "权限不足", trace_id=trace_id)
        if _msg_indicates_sql_reject(message, error_code):
            return QueryOutcome.sql_rejected(message=message, sql=sql, trace_id=trace_id)
        if "超时" in message or "timeout" in message.lower():
            return QueryOutcome.timeout(message=message, sql=sql, trace_id=trace_id)
        if response_type == "error":
            return QueryOutcome.execution_error(message=message or "执行错误", sql=sql, trace_id=trace_id)

    if response_type in ("answer", "data_map", "schema_help", "chat") or response_type == "answer":
        if rows:
            return QueryOutcome.success_with_data(
                rows=rows,
                columns=columns,
                sql=sql,
                message=message,
                trace_id=trace_id,
            )
        if sql or response_type == "answer":
            return QueryOutcome.success_empty(
                sql=sql,
                columns=columns,
                message=message or "结果为空",
                trace_id=trace_id,
            )

    if response_type == "clarification":
        return QueryOutcome.invalid_request(message=message or "需要澄清", trace_id=trace_id)

    return QueryOutcome.invalid_request(message=message or "未知状态", trace_id=trace_id)


def build_outcome_from_query_payload(payload: dict | None) -> QueryOutcome:
    """Parse Query agent artifact payload into QueryOutcome."""
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("query_outcome")
    if isinstance(raw, dict) and raw.get("status"):
        try:
            st = QueryOutcomeStatus(str(raw["status"]))
        except ValueError:
            st = None
        if st is not None:
            o = QueryOutcome(
                status=st,
                message=str(raw.get("message") or payload.get("message") or ""),
                analysis_spec_id=raw.get("analysis_spec_id"),
                sql=raw.get("sql") if raw.get("sql") is not None else payload.get("sql"),
                columns=list(raw.get("columns") or payload.get("columns") or []),
                rows_count=int(raw.get("rows_count") if raw.get("rows_count") is not None else len(payload.get("rows") or [])),
                error_code=raw.get("error_code"),
                retryable=bool(raw.get("retryable")),
                trace_id=raw.get("trace_id"),
                allowed_next_actions=list(raw.get("allowed_next_actions") or []),
            )
            if not o.allowed_next_actions:
                o.allowed_next_actions = next_actions_for_outcome(o)
            return o

    # legacy payload
    rows = list(payload.get("rows") or [])
    columns = list(payload.get("columns") or [])
    sql = str(payload.get("sql") or "")
    msg = str(payload.get("message") or payload.get("error") or "")
    err = str(payload.get("error_code") or "")
    if _msg_indicates_permission(msg, err) or err.upper() == "PERMISSION_DENIED":
        return QueryOutcome.permission_denied(message=msg or "权限不足")
    if err.upper() == "SQL_REJECTED" or _msg_indicates_sql_reject(msg, err):
        return QueryOutcome.sql_rejected(message=msg or "SQL拒绝", sql=sql)
    if err.upper() == "TIMEOUT" or "超时" in msg:
        return QueryOutcome.timeout(message=msg or "超时", sql=sql)
    if err.upper() == "EXECUTION_ERROR" or payload.get("status") == "failed":
        if not rows and msg:
            return QueryOutcome.execution_error(message=msg, sql=sql)
    rc = payload.get("rows_count")
    try:
        rc_i = int(rc) if rc is not None else len(rows)
    except Exception:
        rc_i = len(rows)
    if rc_i > 0 or rows:
        return QueryOutcome.success_with_data(rows=rows or [{"_": 1}] * rc_i, columns=columns, sql=sql, message=msg)
    return QueryOutcome.success_empty(sql=sql, columns=columns, message=msg or "结果为空")

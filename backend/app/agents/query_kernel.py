"""v2 Query Kernel — shared by ApplicationService controlled_loop path and QueryHarness.

No LangGraph. Input: question + optional TaskSpec + catalog/connection.
Output: QueryOutcome + bounded rows preview + SQL + optional AnalysisSpec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.agents.analysis_pipeline import run_analysis
from app.agents.query_outcome import QueryOutcome, QueryOutcomeStatus
from app.agents.semantic_catalog import SemanticCatalog, analysis_allowed


@dataclass
class QueryKernelResult:
    outcome: QueryOutcome
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows_preview: list[dict] = field(default_factory=list)
    rows_count: int = 0
    message: str = ""
    analysis_spec: Any = None
    clarify_slots: list[str] = field(default_factory=list)
    action: str = ""  # answer|clarify|refuse|query
    evidence: Any = None
    kernel: str = "analysis_kernel_v2"
    used_graph: bool = False

    def to_query_payload(self, *, preview_limit: int = 100) -> dict[str, Any]:
        rows = list(self.rows_preview or [])[: max(0, min(int(preview_limit), 500))]
        return {
            "columns": list(self.columns or []),
            "rows": rows,
            "rows_count": int(self.rows_count or 0),
            "sql": self.sql or "",
            "message": self.message or "",
            "error_code": getattr(self.outcome, "error_code", None) or "",
            "query_outcome": self.outcome.to_public_dict() if self.outcome else None,
            "kernel": self.kernel,
            "used_graph": False,
            "action": self.action,
            "clarify_slots": list(self.clarify_slots or []),
        }


def _load_catalog(space_id: str, catalog: SemanticCatalog | None = None) -> SemanticCatalog | None:
    if catalog is not None:
        return catalog
    if not space_id:
        return None
    try:
        from app.agents.catalog_repository import get_catalog_repository

        return get_catalog_repository().load(space_id)
    except Exception:
        return None


def _resolve_connection(
    space_id: str,
    *,
    user_id: int,
    mysql_connection: dict | None = None,
) -> dict | None:
    if mysql_connection and mysql_connection.get("database"):
        return mysql_connection
    try:
        from app.services.authorized_connection_service import resolve_authorized_connection

        conn = resolve_authorized_connection(user_id=user_id, space_id=space_id)
        if conn and conn.get("database"):
            return conn
    except Exception:
        pass
    return mysql_connection


def run_query_kernel(
    question: str,
    *,
    space_id: str = "",
    catalog: SemanticCatalog | None = None,
    mysql_connection: dict | None = None,
    task_spec: dict | None = None,
    user_id: int = 0,
    session_id: str = "",
    execute: bool = True,
    trace_id: str | None = None,
    preview_limit: int = 100,
) -> QueryKernelResult:
    """Execute v2 AnalysisSpec plan→compile→guard→MySQL (or clarify/refuse)."""
    q = (question or "").strip()
    ts = task_spec if isinstance(task_spec, dict) else {}
    if ts.get("question") and str(ts.get("question")).strip():
        q = str(ts["question"]).strip()

    cat = _load_catalog(space_id, catalog)
    if cat is None:
        qo = QueryOutcome.invalid_request(message="Catalog 未就绪，无法执行查询")
        return QueryKernelResult(
            outcome=qo,
            message=qo.message or "Catalog 未就绪",
            action="refuse",
        )
    if not analysis_allowed(cat):
        qo = QueryOutcome.invalid_request(message="Catalog 未允许分析（BLOCKED/未就绪）")
        return QueryKernelResult(
            outcome=qo,
            message=qo.message or "Catalog 未允许分析",
            action="refuse",
        )

    conn = _resolve_connection(
        space_id,
        user_id=user_id,
        mysql_connection=mysql_connection,
    ) if execute else None
    result = run_analysis(
        q,
        cat,
        execute=bool(execute and conn),
        mysql_connection=conn,
        trace_id=trace_id,
    )

    if result.action == "clarify":
        qo = QueryOutcome.invalid_request(message=result.answer_text or "请补充查询条件")
        return QueryKernelResult(
            outcome=qo,
            message=result.answer_text or "",
            analysis_spec=result.spec,
            clarify_slots=list(result.clarify_slots or []),
            action="clarify",
        )
    if result.action == "refuse":
        msg = result.answer_text or "请求被拒绝"
        if any(k in msg for k in ("写", "DELETE", "UPDATE", "INSERT", "不安全", "拒绝")):
            qo = QueryOutcome.sql_rejected(message=msg, sql=getattr(result, "sql", "") or "")
        else:
            qo = QueryOutcome.invalid_request(message=msg)
        return QueryKernelResult(
            outcome=qo,
            sql=getattr(result, "sql", "") or "",
            message=msg,
            analysis_spec=result.spec,
            action="refuse",
            evidence=result.evidence,
        )

    outcome = result.outcome
    if outcome is None:
        # planned but not executed
        sql = getattr(result, "sql", "") or ""
        if not execute or not conn:
            qo = QueryOutcome.invalid_request(message="无可用数据库连接，查询未执行")
            return QueryKernelResult(
                outcome=qo,
                sql=sql,
                message=qo.message or "",
                analysis_spec=result.spec,
                action="refuse",
            )
        qo = QueryOutcome.execution_error(message="执行未返回 QueryOutcome", sql=sql)
        return QueryKernelResult(outcome=qo, sql=sql, message=qo.message or "", analysis_spec=result.spec, action="query")

    rows = list(outcome.rows_preview or [])[: max(0, min(int(preview_limit), 500))]
    cols = list(outcome.columns or [])
    msg = result.answer_text or outcome.message or ""
    return QueryKernelResult(
        outcome=outcome,
        sql=outcome.sql or getattr(result, "sql", "") or "",
        columns=cols,
        rows_preview=rows,
        rows_count=int(outcome.rows_count or len(rows)),
        message=msg,
        analysis_spec=result.spec,
        action="answer" if outcome.status == QueryOutcomeStatus.SUCCESS_WITH_DATA else (
            "query" if outcome.status == QueryOutcomeStatus.SUCCESS_EMPTY else "refuse"
        ),
        evidence=result.evidence,
    )

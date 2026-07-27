"""Diagnosis admission gate — Phase 5.

No diagnosis chain without SUCCESS_WITH_DATA + explicit intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.agents.deep_diagnosis import DiagnosisBudget
from app.agents.query_outcome import QueryOutcome, QueryOutcomeStatus


@dataclass
class AdmissionDecision:
    allowed: bool
    reason: str = ""
    enter_diagnosis_count: int = 0  # 1 if allowed else 0
    outcome_status: str = ""


def _status_of(outcome: Any) -> str:
    if outcome is None:
        return ""
    if isinstance(outcome, QueryOutcome):
        return str(outcome.status.value if hasattr(outcome.status, "value") else outcome.status)
    if isinstance(outcome, dict):
        st = outcome.get("status") or (outcome.get("query_outcome") or {}).get("status")
        return str(st or "")
    return str(getattr(outcome, "status", "") or "")


def _rows_of(outcome: Any) -> int:
    if outcome is None:
        return 0
    if isinstance(outcome, QueryOutcome):
        return int(outcome.rows_count or 0)
    if isinstance(outcome, dict):
        if outcome.get("rows_count") is not None:
            try:
                return int(outcome.get("rows_count") or 0)
            except Exception:
                pass
        return len(outcome.get("rows") or [])
    return int(getattr(outcome, "rows_count", 0) or 0)


def admit_diagnosis(
    outcome: Any,
    *,
    explicit_intent: bool = False,
) -> AdmissionDecision:
    """Hard gate before Insight/Report/Review."""
    st = _status_of(outcome).upper()
    rows = _rows_of(outcome)

    if not explicit_intent:
        return AdmissionDecision(
            allowed=False,
            reason="diagnosis_requires_explicit_intent",
            enter_diagnosis_count=0,
            outcome_status=st,
        )

    if st == QueryOutcomeStatus.SUCCESS_WITH_DATA.value or st == "SUCCESS_WITH_DATA":
        if rows <= 0:
            return AdmissionDecision(
                allowed=False,
                reason="success_with_data_but_no_rows",
                enter_diagnosis_count=0,
                outcome_status="SUCCESS_EMPTY",
            )
        return AdmissionDecision(
            allowed=True,
            reason="ok",
            enter_diagnosis_count=1,
            outcome_status="SUCCESS_WITH_DATA",
        )

    return AdmissionDecision(
        allowed=False,
        reason=f"blocked_status:{st or 'missing'}",
        enter_diagnosis_count=0,
        outcome_status=st or "MISSING",
    )


def phase5_budget() -> DiagnosisBudget:
    """Phase 5 hard wall: data diagnosis ≤ 90s; gap fill ≤ 1."""
    return DiagnosisBudget(
        max_agent_calls=12,
        max_rework=2,
        max_requery=1,
        max_query=3,
        max_wall_seconds=90.0,
    )


def outcome_from_seed(seed_query: Optional[dict]) -> Optional[QueryOutcome]:
    if not isinstance(seed_query, dict):
        return None
    qo = seed_query.get("query_outcome")
    if isinstance(qo, dict) and qo.get("status"):
        st = str(qo.get("status")).upper()
        rows = list(seed_query.get("rows") or [])
        cols = list(seed_query.get("columns") or [])
        sql = str(seed_query.get("sql") or "")
        n = int(seed_query.get("rows_count") if seed_query.get("rows_count") is not None else len(rows))
        if st == "SUCCESS_WITH_DATA":
            return QueryOutcome.success_with_data(rows=rows or [{"_": n}], columns=cols or ["_"], sql=sql)
        if st == "SUCCESS_EMPTY":
            return QueryOutcome.success_empty(sql=sql, columns=cols)
        if st == "PERMISSION_DENIED":
            return QueryOutcome.permission_denied(message=str(qo.get("message") or "denied"))
        if st == "SQL_REJECTED":
            return QueryOutcome.sql_rejected(message=str(qo.get("message") or "rejected"), sql=sql)
        if st == "EXECUTION_ERROR":
            return QueryOutcome.execution_error(message=str(qo.get("message") or "error"), sql=sql)
        if st == "TIMEOUT":
            return QueryOutcome.timeout(message="timeout", sql=sql)
    rows = list(seed_query.get("rows") or [])
    n = int(seed_query.get("rows_count") if seed_query.get("rows_count") is not None else len(rows))
    sql = str(seed_query.get("sql") or "")
    cols = list(seed_query.get("columns") or [])
    if n > 0 or rows:
        return QueryOutcome.success_with_data(rows=rows or [{"v": n}], columns=cols or ["v"], sql=sql)
    if sql or cols:
        return QueryOutcome.success_empty(sql=sql, columns=cols)
    return None

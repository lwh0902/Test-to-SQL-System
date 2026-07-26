"""Controlled observe→decide→act loop for analysis turns (Phase 4).

Hub-and-spoke: only this loop decides next_action; agents never call each other.
Plain follow-ups never wake Report/Review/Export/Insight.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.agents.active_analysis_state import ActiveAnalysisState, _spec_fingerprint
from app.agents.analysis_pipeline import compile_and_guard, execute_sql_on_seed
from app.agents.analysis_spec import AnalysisSpec
from app.agents.evidence import EvidenceBundle
from app.agents.query_outcome import QueryOutcome, QueryOutcomeStatus
from app.agents.semantic_catalog import SemanticCatalog
from app.agents.spec_patch import looks_like_followup, patch_or_build


ALLOWED_ACTIONS = {
    "clarify",
    "build_spec",
    "patch_spec",
    "query",
    "relax_once",
    "explain_limit",
    "insight",
    "report",
    "review",
    "stop_success",
    "stop_empty",
    "stop_denied",
    "stop_error",
    "reuse_result",
}

HEAVY_AGENTS = {"report", "review", "export", "insight"}


@dataclass
class ControlledBudget:
    max_actions: int = 8
    max_wall_ms: float = 20_000.0
    max_requery: int = 3


@dataclass
class TurnResult:
    action: str  # terminal-ish: answer|clarify|refuse|stop_*
    spec: Optional[AnalysisSpec] = None
    state: Optional[ActiveAnalysisState] = None
    sql: str = ""
    outcome: Optional[QueryOutcome] = None
    evidence: Optional[EvidenceBundle] = None
    answer_text: str = ""
    clarify_slots: list[str] = field(default_factory=list)
    agents_called: list[str] = field(default_factory=list)
    action_trace: list[str] = field(default_factory=list)
    actions_used: int = 0
    duplicate_skipped: bool = False
    requery_count: int = 0
    wrong_inherit: bool = False
    is_followup: bool = False
    patch_ops: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


def _observe(
    question: str,
    state: Optional[ActiveAnalysisState],
    catalog: SemanticCatalog,
    budget: ControlledBudget,
    actions_used: int,
    t0: float,
) -> dict:
    return {
        "question": question,
        "has_state": bool(state and state.is_valid()),
        "last_fp": state.last_spec_fingerprint if state else "",
        "catalog_tables": len(catalog.tables),
        "actions_left": budget.max_actions - actions_used,
        "wall_left_ms": budget.max_wall_ms - (time.perf_counter() - t0) * 1000,
        "followup_hint": looks_like_followup(question),
    }


def run_turn(
    question: str,
    catalog: SemanticCatalog,
    *,
    state: Optional[ActiveAnalysisState] = None,
    schema_sql: str = "",
    seed_sql: str = "",
    mysql_connection: Optional[dict] = None,
    mysql_engine: Any = None,
    budget: Optional[ControlledBudget] = None,
    session_id: str = "",
    user_id: int = 0,
    space_id: str = "",
    allow_heavy: bool = False,
) -> TurnResult:
    """One user turn: observe → decide → act (bounded)."""
    budget = budget or ControlledBudget()
    t0 = time.perf_counter()
    actions_used = 0
    action_trace: list[str] = []
    agents_called: list[str] = []
    requery = 0

    def _budget_ok() -> bool:
        if actions_used >= budget.max_actions:
            return False
        if (time.perf_counter() - t0) * 1000 > budget.max_wall_ms:
            return False
        return True

    # OBSERVE
    _ = _observe(question, state, catalog, budget, actions_used, t0)

    # DECIDE+ACT: patch_or_build
    if not _budget_ok():
        return TurnResult(
            action="stop_error",
            state=state,
            answer_text="已达动作预算，停止。",
            action_trace=action_trace,
            actions_used=actions_used,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    patch = patch_or_build(question, catalog, state)
    actions_used += 1
    action_trace.append(patch.action)

    if patch.action == "clarify":
        return TurnResult(
            action="clarify",
            state=state,
            answer_text=patch.clarify_message,
            clarify_slots=list(patch.clarify_slots or []),
            agents_called=agents_called,
            action_trace=action_trace,
            actions_used=actions_used,
            wrong_inherit=patch.wrong_inherit,
            is_followup=patch.is_followup,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    if patch.action == "refuse":
        return TurnResult(
            action="refuse",
            state=state,
            answer_text=patch.clarify_message or "请求被拒绝",
            agents_called=agents_called,
            action_trace=action_trace,
            actions_used=actions_used,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    spec = patch.spec
    if spec is None:
        return TurnResult(
            action="clarify",
            state=state,
            answer_text="无法构建分析规格。",
            clarify_slots=["measure"],
            action_trace=action_trace,
            actions_used=actions_used,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Duplicate fingerprint → reuse, no requery
    fp = _spec_fingerprint(spec)
    if state and state.last_spec_fingerprint == fp and state.last_result_preview is not None:
        # even empty preview counts as prior result if sql exists
        if state.last_sql or state.last_answer_text:
            action_trace.append("reuse_result")
            actions_used += 1
            return TurnResult(
                action="answer",
                spec=spec,
                state=state,
                sql=state.last_sql,
                answer_text=state.last_answer_text or "与上一问结果相同，未重复查询。",
                agents_called=agents_called,  # no query agent
                action_trace=action_trace,
                actions_used=actions_used,
                duplicate_skipped=True,
                requery_count=0,
                is_followup=patch.is_followup,
                patch_ops=list(patch.patch_ops or []),
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    # QUERY
    if not _budget_ok() or requery >= budget.max_requery:
        return TurnResult(
            action="stop_error",
            spec=spec,
            state=state,
            answer_text="查询预算耗尽。",
            action_trace=action_trace + ["stop_error"],
            actions_used=actions_used,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    action_trace.append("query")
    actions_used += 1
    requery += 1
    agents_called.append("query")

    # Plain follow-up must not wake heavy agents
    if not allow_heavy:
        for h in HEAVY_AGENTS:
            assert h not in agents_called

    cr = compile_and_guard(spec, catalog)
    if not cr.ok:
        return TurnResult(
            action="refuse",
            spec=spec,
            state=state,
            answer_text="无法生成安全 SQL：" + ";".join(cr.errors[:3]),
            errors=list(cr.errors),
            agents_called=agents_called,
            action_trace=action_trace,
            actions_used=actions_used,
            is_followup=patch.is_followup,
            patch_ops=list(patch.patch_ops or []),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    outcome: Optional[QueryOutcome] = None
    if mysql_connection or mysql_engine is not None:
        from app.agents.guarded_mysql_executor import execute_sql_mysql

        c = mysql_connection or {}
        outcome = execute_sql_mysql(
            cr.sql,
            host=str(c.get("host") or "127.0.0.1"),
            port=int(c.get("port") or 3306),
            user=str(c.get("user") or "root"),
            password=str(c.get("password") or ""),
            database=str(c.get("database") or ""),
            engine=mysql_engine,
        )
    elif schema_sql:
        outcome = execute_sql_on_seed(cr.sql, schema_sql=schema_sql, seed_sql=seed_sql)
    evidence = EvidenceBundle(
        analysis_spec_id=spec.spec_id,
        query_outcome_id=outcome.outcome_id if outcome else None,
        sql=cr.sql,
        time_range=spec.time_range.__dict__ if spec.time_range else None,
        measures=[m.__dict__ for m in spec.measures],
        dimensions=list(spec.dimensions),
        filters=[f.__dict__ for f in spec.filters],
        rows_count=outcome.rows_count if outcome else 0,
        result_preview=list(outcome.rows_preview) if outcome else [],
        catalog_fingerprint=catalog.schema_fingerprint,
        join_sources=list(cr.join_sources),
    )

    # terminal decide
    if outcome is None:
        terminal = "query"
        answer = "已生成查询。" + evidence.answer_footer()
    elif outcome.is_success_with_data:
        terminal = "answer"
        action_trace.append("stop_success")
        val = list(outcome.rows_preview[0].values())[-1]
        answer = f"查询结果为 {val}。" + evidence.answer_footer()
    elif outcome.status == QueryOutcomeStatus.SUCCESS_EMPTY:
        terminal = "stop_empty"
        action_trace.append("stop_empty")
        answer = "查询成功但结果为空。" + evidence.answer_footer()
    elif getattr(outcome, "status", None) and "DENIED" in str(outcome.status):
        terminal = "stop_denied"
        action_trace.append("stop_denied")
        answer = "权限不足，已停止。"
    else:
        terminal = "stop_error"
        action_trace.append("stop_error")
        answer = f"查询失败：{getattr(outcome, 'message', '') or outcome.status}"

    # update state
    cat_fp = catalog.schema_fingerprint or ""
    cat_ver = str(getattr(catalog, "version", "") or "")
    new_state = state
    if new_state is None:
        new_state = ActiveAnalysisState.from_spec(
            spec,
            session_id=session_id,
            user_id=user_id,
            space_id=space_id,
            outcome_id=outcome.outcome_id if outcome else None,
            evidence_id=evidence.bundle_id,
            result_preview=list(outcome.rows_preview) if outcome else [],
            sql=cr.sql,
            answer_text=answer,
            catalog_fingerprint=cat_fp,
            catalog_version=cat_ver,
        )
    else:
        new_state.session_id = session_id or new_state.session_id
        new_state.user_id = user_id or new_state.user_id
        new_state.space_id = space_id or new_state.space_id
        new_state.update_from_result(
            spec,
            outcome_id=outcome.outcome_id if outcome else None,
            evidence_id=evidence.bundle_id,
            result_preview=list(outcome.rows_preview) if outcome else [],
            sql=cr.sql,
            answer_text=answer,
            catalog_fingerprint=cat_fp,
            catalog_version=cat_ver,
        )

    # map stop_success to answer for callers expecting answer
    out_action = "answer" if terminal in ("answer", "stop_success") else terminal
    if terminal == "query":
        out_action = "query"

    return TurnResult(
        action=out_action,
        spec=spec,
        state=new_state,
        sql=cr.sql,
        outcome=outcome,
        evidence=evidence,
        answer_text=answer,
        agents_called=agents_called,
        action_trace=action_trace,
        actions_used=actions_used,
        duplicate_skipped=False,
        requery_count=requery,
        is_followup=patch.is_followup,
        patch_ops=list(patch.patch_ops or []),
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def run_chain(
    turns: list[str],
    catalog: SemanticCatalog,
    *,
    schema_sql: str = "",
    seed_sql: str = "",
    budget: Optional[ControlledBudget] = None,
) -> list[TurnResult]:
    state = None
    results = []
    for q in turns:
        r = run_turn(
            q,
            catalog,
            state=state,
            schema_sql=schema_sql,
            seed_sql=seed_sql,
            budget=budget,
        )
        results.append(r)
        if r.state is not None:
            state = r.state
    return results

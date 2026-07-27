"""Supervisor controlled observe→decide→act orchestration via A2A Dispatcher.

Not a fixed linear pipeline. Each iteration:
  OBSERVE artifact summaries on OrchestratorState
  DECIDE  next_action from allowlist (rules L2; optional flash later)
  ACT     Dispatcher → one agent (or stop/export)

Multi-table / multi-dimension diagnosis = multiple `query` acts driven by
`evidence_gaps` (Insight or Review), under DiagnosisBudget — not free mesh ReAct.

dev_spec:
- Supervisor never bypasses Dispatcher for agent work
- Only Query may hit DB tools
- Export only after Review approved
- Agent interiors stay fixed procedures; only Supervisor loops
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Optional

from app.a2a.contracts import A2AMessage
from app.a2a.dispatcher import Dispatcher
from app.a2a.registry import registry
from app.agents.export import ExportHarness
from app.agents.roles import InsightHarness, QueryHarness, ReportHarness, ReviewHarness
from app.services.agent_runtime_store import save_artifact

for h in (QueryHarness(), InsightHarness(), ReportHarness(), ReviewHarness(), ExportHarness()):
    registry.register(h.name, h.handle)

dispatcher = Dispatcher()

DEFAULT_EXPORT_FORMATS = ("pdf", "docx", "csv")
REPORT_FORMATS = frozenset({"pdf", "docx"})
EVIDENCE_FORMATS = frozenset({"csv", "xlsx"})

# Reasons that indicate missing/weak *query data* → prefer requery over pure rewrite.
# Note: "缺少证据引用" is a report citation issue (rework), NOT a data gap.
_EVIDENCE_GAP_MARKERS = (
    "没有有效查询证据",
    "证据不足",
    "需要补充维度",
    "补充维度",
    "无查询结果",
    "查询结果为空",
    "需要补充查询",
)


NEXT_ACTIONS = frozenset({
    "schema_inventory",
    "query",
    "insight",
    "report",
    "review",
    "rework_report",
    "requery",
    "export",
    "stop_ok",
    "stop_rejected",
    "stop_denied",
    "stop_empty",
    "stop_error",
})


@dataclass
class DiagnosisBudget:
    """Global orchestration limits (not per-agent harness timeout)."""

    max_agent_calls: int = 16
    max_rework: int = 2
    max_requery: int = 1
    max_query: int = 3  # includes initial + gap-filling queries (not counting pure rework)
    max_wall_seconds: float = 300.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_agent_calls": self.max_agent_calls,
            "max_rework": self.max_rework,
            "max_requery": self.max_requery,
            "max_query": self.max_query,
            "max_wall_seconds": self.max_wall_seconds,
        }


@dataclass
class OrchestratorState:
    has_query: bool = False
    has_insight: bool = False
    has_report: bool = False
    has_schema: bool = False
    need_schema: bool = False  # multi-table / unknown schema hint from plan
    review_done: bool = False
    review_approved: bool = False
    review_reasons: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    skip_query: bool = False
    rework_count: int = 0
    requery_count: int = 0
    query_count: int = 0
    agent_calls: int = 0
    started_at: float = field(default_factory=time.monotonic)
    export_done: bool = False
    stop_reason: str | None = None
    # Phase 1: QueryOutcome status drives gates (not rows_count alone)
    query_outcome_status: str | None = None
    relax_used: bool = False  # at most one SUCCESS_EMPTY relax
    # anti-loop: fingerprints of query task_specs already run
    query_fingerprints: list[str] = field(default_factory=list)
    last_decision: dict = field(default_factory=dict)

    # payloads / ids
    q_payload: dict = field(default_factory=dict)
    i_payload: dict = field(default_factory=dict)
    r_payload: dict = field(default_factory=dict)
    review_payload: dict = field(default_factory=dict)
    schema_payload: dict = field(default_factory=dict)
    q_aid: str | None = None
    i_aid: str | None = None
    r_aid: str | None = None
    review_aid: str | None = None
    artifact_ids: list[str] = field(default_factory=list)


def should_skip_query(seed_query: Optional[dict]) -> bool:
    if not seed_query or not isinstance(seed_query, dict):
        return False
    rows = seed_query.get("rows") or []
    if rows:
        return True
    try:
        return int(seed_query.get("rows_count") or 0) > 0 and bool(seed_query.get("columns"))
    except Exception:
        return False


def _normalize_export_formats(formats: Optional[Iterable[str]]) -> list[str]:
    if not formats:
        return list(DEFAULT_EXPORT_FORMATS)
    out: list[str] = []
    for f in formats:
        f = str(f).lower().strip()
        if f in REPORT_FORMATS | EVIDENCE_FORMATS and f not in out:
            out.append(f)
    return out or list(DEFAULT_EXPORT_FORMATS)


def _reasons_indicate_evidence_gap(reasons: list[str]) -> bool:
    blob = " ".join(str(r) for r in reasons).lower()
    return any(m.lower() in blob for m in _EVIDENCE_GAP_MARKERS)


def _normalize_gaps(raw: Any) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    for g in raw:
        if isinstance(g, dict):
            text_g = str(g.get("text") or g.get("gap") or g.get("reason") or "").strip()
        else:
            text_g = str(g).strip()
        if text_g and text_g not in out:
            out.append(text_g[:200])
    return out[:12]


def extract_evidence_gaps(*payloads: dict | None) -> list[str]:
    """Collect machine-readable gaps from Insight / Review payloads (no CoT)."""
    gaps: list[str] = []
    for p in payloads:
        if not isinstance(p, dict):
            continue
        for key in ("evidence_gaps", "gaps", "missing_evidence", "open_questions"):
            for g in _normalize_gaps(p.get(key)):
                if g not in gaps:
                    gaps.append(g)
        for f in p.get("findings") or []:
            if not isinstance(f, dict):
                continue
            if f.get("kind") in ("gap", "insufficient", "hypothesis") and f.get("needs_query"):
                t = str(f.get("text") or "").strip()
                if t and t not in gaps:
                    gaps.append(t[:200])
    return gaps[:12]


def query_task_fingerprint(task_spec: dict | None, question: str = "") -> str:
    """Stable id for anti-loop on repeated identical queries."""
    spec = task_spec if isinstance(task_spec, dict) else {}
    dims = spec.get("dimensions") or spec.get("suggested_dimensions") or ""
    tables = spec.get("tables") or spec.get("suggested_tables") or ""
    if isinstance(dims, list):
        dims = ",".join(str(d) for d in dims)
    if isinstance(tables, list):
        tables = ",".join(str(t) for t in tables)
    raw = "|".join(
        [
            str(spec.get("task_type") or "metric_query"),
            str(spec.get("metric") or ""),
            str(spec.get("table") or tables or ""),
            str(dims),
            str(tables),
            (question or str(spec.get("question") or ""))[:120],
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def observe_artifacts(state: OrchestratorState) -> dict[str, Any]:
    """Build safe observation summary for decide (no raw rows / secrets / CoT)."""
    q = state.q_payload if isinstance(state.q_payload, dict) else {}
    rows_count = q.get("rows_count")
    try:
        rows_count = int(rows_count if rows_count is not None else len(q.get("rows") or []))
    except Exception:
        rows_count = 0
    cols = q.get("columns") or []
    if not isinstance(cols, list):
        cols = []

    gaps = list(state.evidence_gaps or [])
    gaps.extend(extract_evidence_gaps(state.i_payload, state.review_payload))
    seen: set[str] = set()
    uniq_gaps: list[str] = []
    for g in gaps:
        if g not in seen:
            seen.add(g)
            uniq_gaps.append(g)

    schema_tables: list[str] = []
    sp = state.schema_payload if isinstance(state.schema_payload, dict) else {}
    for t in (sp.get("tables") or [])[:20]:
        if isinstance(t, dict) and t.get("name"):
            schema_tables.append(str(t.get("name")))

    return {
        "has_schema": state.has_schema,
        "schema_table_count": len(schema_tables),
        "schema_tables": schema_tables[:12],
        "has_query": state.has_query,
        "query_count": state.query_count,
        "last_query_rows": rows_count,
        "last_query_columns": [str(c) for c in cols[:12]],
        "has_insight": state.has_insight,
        "has_report": state.has_report,
        "review_done": state.review_done,
        "review_approved": state.review_approved,
        "review_reasons": list(state.review_reasons or [])[:8],
        "evidence_gaps": uniq_gaps[:12],
        "rework_count": state.rework_count,
        "requery_count": state.requery_count,
        "export_done": state.export_done,
        "query_fingerprints": list(state.query_fingerprints or [])[-8:],
    }


def _decide_next_step_l2(
    state: OrchestratorState,
    budget: DiagnosisBudget,
    observation: dict | None = None,
) -> str:
    """L2 rule decide from observation + budget. Pure / testable."""
    obs = observation if isinstance(observation, dict) else observe_artifacts(state)
    gaps = list(obs.get("evidence_gaps") or state.evidence_gaps or [])

    if state.agent_calls >= budget.max_agent_calls:
        if state.review_approved:
            action = "export" if not state.export_done else "stop_ok"
        else:
            action = "stop_rejected"
        state.last_decision = {"action": action, "layer": "L2", "reason": "max_agent_calls"}
        return action
    if (time.monotonic() - state.started_at) > budget.max_wall_seconds:
        action = (
            "stop_rejected"
            if not state.review_approved
            else ("export" if not state.export_done else "stop_ok")
        )
        state.last_decision = {"action": action, "layer": "L2", "reason": "max_wall_seconds"}
        return action

    if state.export_done and state.review_approved:
        state.last_decision = {"action": "stop_ok", "layer": "L2", "reason": "complete"}
        return "stop_ok"

    if state.need_schema and not state.has_schema:
        state.last_decision = {
            "action": "schema_inventory",
            "layer": "L2",
            "reason": "need_schema",
        }
        return "schema_inventory"

    if not state.has_query:
        state.last_decision = {"action": "query", "layer": "L2", "reason": "need_query"}
        return "query"

    # --- Phase 1 hard gates from QueryOutcome (before any heavy agent) ---
    qp = state.q_payload if isinstance(state.q_payload, dict) else {}
    q_status = (state.query_outcome_status or "").upper()
    if not q_status and isinstance(qp.get("query_outcome"), dict):
        q_status = str((qp.get("query_outcome") or {}).get("status") or "").upper()
    rows_n = 0
    try:
        rows_n = int(qp.get("rows_count") if qp.get("rows_count") is not None else len(qp.get("rows") or []))
    except Exception:
        rows_n = 0

    # Explicit outcome/payload vs legacy has_query-only states
    explicit_outcome = bool(q_status) or bool(qp.get("query_outcome")) or (
        "rows" in qp or "rows_count" in qp or bool(qp.get("sql"))
    )
    if rows_n > 0:
        has_data = True
        if not q_status:
            q_status = "SUCCESS_WITH_DATA"
    elif q_status == "SUCCESS_WITH_DATA":
        has_data = False  # claimed success but no rows → treat as empty
        q_status = "SUCCESS_EMPTY"
    elif not explicit_outcome and state.has_query:
        # Legacy tests / seed without payload detail: do not force empty-stop
        has_data = True
    else:
        has_data = False

    if q_status == "PERMISSION_DENIED":
        state.last_decision = {
            "action": "stop_denied",
            "layer": "L2",
            "reason": "permission_denied",
        }
        state.stop_reason = "stop_denied"
        return "stop_denied"

    if q_status in ("SQL_REJECTED", "EXECUTION_ERROR", "TIMEOUT", "CANCELLED"):
        state.last_decision = {
            "action": "stop_error",
            "layer": "L2",
            "reason": q_status.lower(),
        }
        state.stop_reason = "stop_error"
        return "stop_error"

    if explicit_outcome and not has_data and q_status in ("", "SUCCESS_EMPTY", "SUCCESS_WITH_DATA"):
        # at most one relax_once on empty
        if not state.relax_used and state.query_count < budget.max_query and state.query_count > 0:
            # only relax if we already ran at least one query (or seed counted)
            if q_status in ("SUCCESS_EMPTY", "") or rows_n == 0:
                state.last_decision = {
                    "action": "query",
                    "layer": "L2",
                    "reason": "relax_once_empty",
                }
                return "query"
        if not has_data:
            state.last_decision = {
                "action": "stop_empty",
                "layer": "L2",
                "reason": "success_empty",
            }
            state.stop_reason = "stop_empty"
            return "stop_empty"

    # After insight: fill multi-table/dimension gaps with another query before report
    if state.has_insight and not state.has_report and gaps and has_data:
        if state.query_count < budget.max_query and state.requery_count < budget.max_requery:
            fp = query_task_fingerprint({"task_type": "gap_fill"}, ";".join(gaps[:3]))
            if fp not in (state.query_fingerprints or []):
                state.last_decision = {
                    "action": "query",
                    "layer": "L2",
                    "reason": "evidence_gaps_before_report",
                    "gaps": gaps[:5],
                }
                return "query"

    if not state.has_insight:
        if not has_data:
            state.last_decision = {
                "action": "stop_empty",
                "layer": "L2",
                "reason": "no_data_for_insight",
            }
            state.stop_reason = "stop_empty"
            return "stop_empty"
        state.last_decision = {"action": "insight", "layer": "L2", "reason": "need_insight"}
        return "insight"

    if not state.has_report:
        # HARD: never report without SUCCESS_WITH_DATA
        if not has_data:
            state.last_decision = {
                "action": "stop_empty",
                "layer": "L2",
                "reason": "no_data_for_report",
            }
            state.stop_reason = "stop_empty"
            return "stop_empty"
        state.last_decision = {"action": "report", "layer": "L2", "reason": "need_report"}
        return "report"

    if not state.review_done:
        state.last_decision = {"action": "review", "layer": "L2", "reason": "need_review"}
        return "review"

    if state.review_approved:
        action = "export" if not state.export_done else "stop_ok"
        state.last_decision = {"action": action, "layer": "L2", "reason": "approved"}
        return action

    reasons = list(obs.get("review_reasons") or state.review_reasons or [])
    if _reasons_indicate_evidence_gap(reasons) and state.requery_count < budget.max_requery:
        if state.query_count < budget.max_query:
            state.last_decision = {
                "action": "requery",
                "layer": "L2",
                "reason": "review_evidence_gap",
            }
            return "requery"
    if state.rework_count < budget.max_rework:
        state.last_decision = {
            "action": "rework_report",
            "layer": "L2",
            "reason": "review_quality",
        }
        return "rework_report"
    state.last_decision = {"action": "stop_rejected", "layer": "L2", "reason": "no_repair_left"}
    return "stop_rejected"


def decide_next_step(
    state: OrchestratorState,
    budget: DiagnosisBudget,
    observation: dict | None = None,
    *,
    llm_complete=None,
    skip_l1: bool = False,
) -> str:
    """Controlled decide: L1 flash (optional) → L2 rules. Allowlisted only."""
    obs = observation if isinstance(observation, dict) else observe_artifacts(state)
    # Hard stops stay on L2 (deterministic)
    l2 = _decide_next_step_l2(state, budget, observation=obs)
    if l2 in ("stop_ok", "stop_rejected", "stop_denied", "stop_empty", "stop_error", "export") or skip_l1:
        return l2
    if llm_complete is None:
        return l2

    # L1 flash: propose next_action from observation only (no CoT / no rows)
    try:
        from app.agents.model_adapter import ModelRequest, ModelResponse, ModelTier, ThinkingLevel
        import json as _json

        system = (
            "你是诊断编排 Supervisor 的 DECIDE 步骤。根据观察摘要选择下一步动作。"
            "只输出 JSON：{\"action\":str,\"reason\":str,\"confidence\":0-1}。"
            f"action 必须是以下之一：{sorted(NEXT_ACTIONS)}。"
            "禁止输出思维链；禁止编造数据；普通查数已完成时不要重复无意义 query。"
            "有 evidence_gaps 且未写报告时优先 query；有报告未审则 review；审过则 export。"
        )
        user = _json.dumps(
            {
                "observation": {
                    k: obs.get(k)
                    for k in (
                        "has_schema",
                        "has_query",
                        "query_count",
                        "last_query_rows",
                        "last_query_columns",
                        "has_insight",
                        "has_report",
                        "review_done",
                        "review_approved",
                        "review_reasons",
                        "evidence_gaps",
                        "rework_count",
                        "requery_count",
                        "export_done",
                    )
                },
                "budget": budget.as_dict(),
                "l2_suggestion": l2,
            },
            ensure_ascii=False,
        )
        resp = llm_complete(
            ModelRequest(
                system=system,
                user=user,
                tier=ModelTier.FLASH,
                thinking=ThinkingLevel.NONE,
                max_tokens=200,
                expect_json=True,
                temperature=0.0,
            )
        )
        payload = None
        if hasattr(resp, "ok") and resp.ok:
            payload = getattr(resp, "json_payload", None)
        if isinstance(payload, dict):
            action = str(payload.get("action") or "").strip()
            if action in NEXT_ACTIONS:
                # Never allow export without approval
                if action == "export" and not state.review_approved:
                    action = l2
                # Never skip budget-safe l2 stop if l2 already stopped — already handled
                state.last_decision = {
                    "action": action,
                    "layer": "L1",
                    "reason": str(payload.get("reason") or "flash")[:120],
                    "confidence": payload.get("confidence"),
                    "l2_fallback": l2,
                }
                return action
    except Exception:
        pass
    # keep l2 decision metadata
    if not state.last_decision:
        state.last_decision = {"action": l2, "layer": "L2", "reason": "l1_unavailable"}
    else:
        state.last_decision.setdefault("layer", "L2")
    return l2



def build_analysis_plan(
    question: str,
    seed_query: Optional[dict] = None,
    export_formats: Optional[Iterable[str]] = None,
    budget: Optional[DiagnosisBudget] = None,
) -> dict[str, Any]:
    skip = should_skip_query(seed_query)
    fmts = _normalize_export_formats(export_formats)
    b = budget or DiagnosisBudget()
    need_schema = any(
        k in (question or "")
        for k in ("多表", "各表", "哪些表", "关联", "join", "维度", "归因", "根因", "渠道和", "还是")
    )
    if skip:
        pipeline = ["insight", "report", "review", "export"]
        mode = "continue_from_query"
    else:
        pipeline = ["query", "insight", "report", "review", "export"]
        mode = "full"
    if need_schema and not skip:
        pipeline = ["schema_inventory"] + pipeline
    return {
        "goal": question,
        "mode": mode,
        "skip_query": skip,
        "need_schema": need_schema,
        "pipeline": pipeline,
        "export_formats": fmts,
        "orchestration": "observe_decide_act",
        "budget": b.as_dict(),
        "edges": [
            "observe->decide->act",
            "schema_inventory->query",
            "query->insight|query",
            "insight->query|report",
            "report->review",
            "review_approved->export",
            "review_rejected->rework_report|requery|stop",
            "requery->insight",
            "rework_report->review",
        ],
        "next_actions": sorted(NEXT_ACTIONS),
    }


def _scope_from_task(task: dict) -> dict:
    return {
        "task_id": task["id"],
        "session_id": task["session_id"],
        "user_id": task["user_id"],
        "space_id": task["space_id"],
    }


def _msg(task: dict, target: str, payload: dict, artifact_ids: list, *, suffix: str = "") -> A2AMessage:
    scope = _scope_from_task(task)
    key = f"{task['id']}:{target}{suffix}"
    return A2AMessage(
        correlation_id=task["id"],
        source_agent="supervisor",
        target_agent=target,
        idempotency_key=key,
        payload=payload,
        artifact_ids=list(artifact_ids),
        **scope,
    )





async def _call_agent(task: dict, target: str, payload: dict, artifact_ids: list, *, suffix: str = ""):
    """Call agent via Dispatcher; return (progress_events, result).

    Prefer live progress queue so SSE is not purely post-batch.
    """
    from app.agents.progress import deliver_with_progress

    msg = _msg(task, target, payload, artifact_ids, suffix=suffix)
    events: list[dict] = []
    result = None

    async def _run():
        return await dispatcher.deliver(msg)

    async for item in deliver_with_progress(_run):
        if item.get("type") == "result":
            result = item.get("value")
        elif item.get("type") == "error":
            raise item.get("error")
        else:
            events.append(item)
    return events, result


async def _yield_call(task, target, payload, artifact_ids, suffix=""):
    """Yield progress events as they arrive, then the result marker."""
    from app.agents.progress import deliver_with_progress

    msg = _msg(task, target, payload, artifact_ids, suffix=suffix)

    async def _run():
        return await dispatcher.deliver(msg)

    async for item in deliver_with_progress(_run):
        if item.get("type") == "result":
            yield {"type": "result", "value": item.get("value")}
        elif item.get("type") == "error":
            raise item.get("error")
        else:
            yield item


async def run_deep_diagnosis(
    task: dict,
    user_role: str,
    seed_query: Optional[dict] = None,
    export_formats: Optional[Iterable[str]] = None,
    budget: Optional[DiagnosisBudget] = None,
) -> AsyncIterator[dict]:
    scope = _scope_from_task(task)
    budget = budget or DiagnosisBudget()
    plan_payload = build_analysis_plan(
        task["question"],
        seed_query=seed_query,
        export_formats=export_formats,
        budget=budget,
    )
    plan = save_artifact(
        **scope,
        artifact_type="AnalysisPlan",
        source_agent="supervisor",
        status="approved",
        payload=plan_payload,
    )
    yield {
        "event": "agent_lifecycle",
        "data": {**scope, "agent": "supervisor", "status": "completed", "artifact_id": plan},
    }

    st = OrchestratorState(
        skip_query=bool(plan_payload["skip_query"]),
        need_schema=bool(plan_payload.get("need_schema")),
        artifact_ids=[plan],
        started_at=time.monotonic(),
    )
    fmts = list(plan_payload["export_formats"])
    call_seq = 0  # for unique idempotency on retries

    # Seed reuse before loop
    if st.skip_query:
        q_payload = {
            "columns": list((seed_query or {}).get("columns") or []),
            "rows": list((seed_query or {}).get("rows") or [])[:100],
            "rows_count": int(
                (seed_query or {}).get("rows_count")
                or len((seed_query or {}).get("rows") or [])
            ),
            "sql": (seed_query or {}).get("sql") or "",
            "seeded": True,
        }
        q_aid = save_artifact(
            **scope,
            artifact_type="QueryResult",
            source_agent="query",
            status="approved",
            payload={k: v for k, v in q_payload.items()},
        )
        st.has_query = True
        st.query_count = max(st.query_count, 1)
        st.q_payload = q_payload
        from app.agents.query_outcome import build_outcome_from_query_payload

        qo_seed = build_outcome_from_query_payload(q_payload)
        st.query_outcome_status = qo_seed.status.value
        st.q_payload["query_outcome"] = qo_seed.to_public_dict()
        st.q_aid = q_aid
        st.artifact_ids.append(q_aid)
        fp = query_task_fingerprint({"task_type": "seed"}, task.get("question") or "")
        if fp not in st.query_fingerprints:
            st.query_fingerprints.append(fp)
        yield {
            "event": "agent_lifecycle",
            "data": {
                **scope,
                "agent": "query",
                "status": "reused",
                "artifact_id": q_aid,
                "query_outcome_status": st.query_outcome_status,
            },
        }

    # ---- observe → decide → act loop ----
    while True:
        obs = observe_artifacts(st)
        def _llm_decide(req):
            try:
                from app.agents.model_adapter import get_model_adapter
                return get_model_adapter().complete(req)
            except Exception as e:
                from app.agents.model_adapter import ModelResponse
                return ModelResponse(ok=False, error=str(e))
        step = decide_next_step(st, budget, observation=obs, llm_complete=_llm_decide)
        if step not in NEXT_ACTIONS:
            st.stop_reason = f"unknown_step:{step}"
            step = "stop_rejected"

        yield {
            "event": "supervisor_decide",
            "data": {
                **scope,
                "action": step,
                "layer": (st.last_decision or {}).get("layer", "L2"),
                "reason": (st.last_decision or {}).get("reason"),
                "budget_left": {
                    "agent_calls": max(0, budget.max_agent_calls - st.agent_calls),
                    "query": max(0, budget.max_query - st.query_count),
                    "rework": max(0, budget.max_rework - st.rework_count),
                    "requery": max(0, budget.max_requery - st.requery_count),
                },
                "observation": {
                    "has_query": obs.get("has_query"),
                    "query_count": obs.get("query_count"),
                    "has_insight": obs.get("has_insight"),
                    "has_report": obs.get("has_report"),
                    "evidence_gaps": obs.get("evidence_gaps") or [],
                    "review_approved": obs.get("review_approved"),
                },
            },
        }

        if step in ("stop_ok", "stop_denied", "stop_empty", "stop_error", "stop_rejected"):
            from app.agents.link_policy import build_diagnosis_terminal_message

            term_msg = build_diagnosis_terminal_message(
                stop_reason=step,
                outcome_status=st.query_outcome_status,
                review_approved=st.review_approved,
                review_reasons=st.review_reasons,
                has_report=st.has_report,
            )
            st.stop_reason = st.stop_reason or step
            if step == "stop_ok":
                yield {
                    "event": "orchestration_done",
                    "data": {
                        **scope,
                        "status": "completed",
                        "agent_calls": st.agent_calls,
                        "stop_reason": st.stop_reason,
                        "terminal_status": st.query_outcome_status,
                        "message": term_msg,
                    },
                }
                return

            reason_event = (
                "budget_exhausted"
                if step == "stop_rejected"
                and (
                    st.agent_calls >= budget.max_agent_calls
                    or (time.monotonic() - st.started_at) > budget.max_wall_seconds
                )
                else (
                    "diagnosis_stopped"
                    if step in ("stop_denied", "stop_empty", "stop_error")
                    else "review_rejected"
                )
            )
            yield {
                "event": reason_event,
                "data": {
                    **scope,
                    "reasons": st.review_reasons,
                    "artifact_id": st.review_aid,
                    "agent_calls": st.agent_calls,
                    "rework_count": st.rework_count,
                    "requery_count": st.requery_count,
                    "stop_reason": st.stop_reason or step,
                    "terminal_status": st.query_outcome_status,
                    "message": term_msg,
                    "status": "stopped",

                },
            }
            return

        if step == "query":
            call_seq += 1
            gaps = list((st.last_decision or {}).get("gaps") or st.evidence_gaps or [])
            if gaps and st.has_insight:
                mode = "gap_fill"
                q_question = task["question"]  # rewritten by GapCompiler below
            else:
                q_question = task["question"]
                mode = "initial"

            # GapCompiler: evidence_gaps → executable task_spec (multi-table fill).
            from app.agents.gap_compiler import compile_gap_task

            if mode == "gap_fill":
                compiled = compile_gap_task(
                    gaps,
                    base_question=task.get("question") or "",
                    schema_payload=st.schema_payload if isinstance(st.schema_payload, dict) else None,
                    prior_query=st.q_payload if isinstance(st.q_payload, dict) else None,
                    task_type="gap_fill_query",
                )
                gap_task = compiled.to_task_spec()
                q_question = compiled.question or q_question
            else:
                gap_task = {
                    "task_type": "metric_query",
                    "evidence_gaps": [],
                    "dimensions": [],
                    "tables": [],
                    "suggested_dimensions": [],
                    "suggested_tables": [],
                    "question": q_question,
                }

            # fingerprint uses compiled spec so identical gap fills anti-loop correctly
            fp = query_task_fingerprint(gap_task, q_question)
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "query",
                    "status": "running",
                    "mode": mode,
                    "task_type": gap_task.get("task_type"),
                    "dimensions": list(gap_task.get("dimensions") or [])[:6],
                    "tables": list(gap_task.get("tables") or [])[:6],
                    "metric": gap_task.get("metric") or "",
                },
            }

            q = None
            async for _item in _yield_call(
                task,
                "query",
                {
                    "question": q_question,
                    "user_role": user_role,
                    "task_type": gap_task.get("task_type") or ("gap_fill_query" if mode == "gap_fill" else "metric_query"),
                    "task_spec": gap_task,
                    "evidence_gaps": gaps,
                    "compiled_gap": mode == "gap_fill",
                },
                list(st.artifact_ids) or [plan],
                suffix=f":{call_seq}",
            ):
                if _item.get("type") == "result":
                    q = _item.get("value")
                else:
                    yield _item
            if q is None:
                raise RuntimeError("q agent returned no result")
            st.agent_calls += 1
            st.has_query = True
            st.query_count += 1
            if fp not in st.query_fingerprints:
                st.query_fingerprints.append(fp)
            # consuming gaps for this fill
            if mode == "gap_fill":
                st.requery_count += 1
                st.evidence_gaps = []
            # mark relax consumption when this query was empty-relax
            if (st.last_decision or {}).get("reason") == "relax_once_empty":
                st.relax_used = True
            st.q_payload = q.get("payload") or {}
            # Phase 1: bind QueryOutcome status (never collapse errors to bare empty)
            from app.agents.query_outcome import build_outcome_from_query_payload

            qo = build_outcome_from_query_payload(st.q_payload)
            st.query_outcome_status = qo.status.value
            if "query_outcome" not in st.q_payload:
                st.q_payload = dict(st.q_payload)
                st.q_payload["query_outcome"] = qo.to_public_dict()
            st.q_aid = q.get("artifact_id")
            if st.q_aid:
                st.artifact_ids.append(st.q_aid)
            # fresh query invalidates downstream synthesis
            st.has_insight = False
            st.has_report = False
            st.review_done = False
            st.review_approved = False
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "query",
                    "status": "completed",
                    "artifact_id": st.q_aid,
                    "mode": mode,
                    "query_count": st.query_count,
                    "query_outcome_status": st.query_outcome_status,
                },
            }
            continue

        if step == "schema_inventory":
            call_seq += 1
            yield {
                "event": "agent_lifecycle",
                "data": {**scope, "agent": "query", "status": "running", "mode": "schema_inventory"},
            }
            # Prefer in-process inventory; fall back to empty schema payload
            try:
                from app.agents.schema_inventory import run_schema_inventory
                inv_result = run_schema_inventory(
                    space_id=task["space_id"],
                    session_id=task.get("session_id"),
                    user_id=task.get("user_id"),
                    question=task.get("question") or "",
                )
                inv = inv_result.inventory
            except Exception:
                inv = {"tables": [], "summary": {}, "space_id": task.get("space_id")}
            s_aid = save_artifact(
                **scope,
                artifact_type="SchemaInventory",
                source_agent="query",
                status="approved",
                payload=inv,
            )
            st.agent_calls += 1
            st.has_schema = True
            st.schema_payload = inv
            st.artifact_ids.append(s_aid)
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "query",
                    "status": "completed",
                    "mode": "schema_inventory",
                    "artifact_id": s_aid,
                },
            }
            yield {
                "event": "artifact_produced",
                "data": {
                    **scope,
                    "artifact_type": "SchemaInventory",
                    "artifact_id": s_aid,
                    "source_agent": "query",
                },
            }
            continue

        if step == "requery":
            st.requery_count += 1
            call_seq += 1
            yield {
                "event": "requery",
                "data": {
                    **scope,
                    "attempt": st.requery_count,
                    "reasons": st.review_reasons,
                },
            }
            yield {
                "event": "agent_lifecycle",
                "data": {**scope, "agent": "query", "status": "running", "mode": "requery"},
            }
            # Focused follow-up question from review reasons
            follow = (
                f"{task['question']}\n"
                f"【补充查询】审查认为证据不足：{'；'.join(st.review_reasons[:5])}。"
                f"请补充相关维度或明细以便诊断。"
            )
            q = None
            async for _item in _yield_call(task,
                    "query",
                    {
                        "question": follow,
                        "user_role": user_role,
                        "requery": True,
                        "review_reasons": st.review_reasons,
                    },
                    list(st.artifact_ids),
                    suffix=f":requery{st.requery_count}",):
                if _item.get("type") == "result":
                    q = _item.get("value")
                else:
                    yield _item
            if q is None:
                raise RuntimeError("q agent returned no result")
            st.agent_calls += 1
            st.has_query = True
            st.query_count += 1
            fp = query_task_fingerprint(
                {"task_type": "requery"}, follow
            )
            if fp not in st.query_fingerprints:
                st.query_fingerprints.append(fp)
            st.q_payload = q.get("payload") or {}
            from app.agents.query_outcome import build_outcome_from_query_payload

            qo_r = build_outcome_from_query_payload(st.q_payload)
            st.query_outcome_status = qo_r.status.value
            st.q_payload = dict(st.q_payload)
            st.q_payload["query_outcome"] = qo_r.to_public_dict()
            st.q_aid = q.get("artifact_id")
            if st.q_aid:
                st.artifact_ids.append(st.q_aid)
            st.has_insight = False
            st.has_report = False
            st.review_done = False
            st.review_approved = False
            st.evidence_gaps = []
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "query",
                    "status": "completed",
                    "artifact_id": st.q_aid,
                    "mode": "requery",
                    "query_count": st.query_count,
                },
            }
            continue

        if step == "insight":
            call_seq += 1
            yield {
                "event": "agent_lifecycle",
                "data": {**scope, "agent": "insight", "status": "running"},
            }
            i = None
            async for _item in _yield_call(task,
                    "insight",
                    {"query": st.q_payload, "question": task["question"]},
                    list(st.artifact_ids),
                    suffix=f":{call_seq}",):
                if _item.get("type") == "result":
                    i = _item.get("value")
                else:
                    yield _item
            if i is None:
                raise RuntimeError("i agent returned no result")
            st.agent_calls += 1
            st.has_insight = True
            st.i_payload = i.get("payload") or {}
            st.i_aid = i.get("artifact_id")
            if st.i_aid:
                st.artifact_ids.append(st.i_aid)
            st.evidence_gaps = extract_evidence_gaps(st.i_payload)
            st.has_report = False
            st.review_done = False
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "insight",
                    "status": "completed",
                    "artifact_id": st.i_aid,
                    "evidence_gaps": list(st.evidence_gaps)[:8],
                },
            }
            continue

        if step in ("report", "rework_report"):
            is_rework = step == "rework_report"
            if is_rework:
                st.rework_count += 1
                yield {
                    "event": "rework",
                    "data": {
                        **scope,
                        "target": "report",
                        "attempt": st.rework_count,
                        "reasons": st.review_reasons,
                    },
                }
            call_seq += 1
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "report",
                    "status": "running",
                    "mode": "rework" if is_rework else "draft",
                },
            }
            payload = {
                "question": task["question"],
                "query": st.q_payload,
                "insight": st.i_payload,
            }
            if is_rework:
                payload["revision_notes"] = list(st.review_reasons)
                payload["review_reasons"] = list(st.review_reasons)
                payload["rework_attempt"] = st.rework_count
            r = None
            async for _item in _yield_call(task,
                    "report",
                    payload,
                    list(st.artifact_ids),
                    suffix=f":{call_seq}",):
                if _item.get("type") == "result":
                    r = _item.get("value")
                else:
                    yield _item
            if r is None:
                raise RuntimeError("r agent returned no result")
            st.agent_calls += 1
            st.has_report = True
            st.r_payload = r.get("payload") or {}
            st.r_aid = r.get("artifact_id")
            if st.r_aid:
                st.artifact_ids.append(st.r_aid)
            st.review_done = False
            st.review_approved = False
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "report",
                    "status": "completed",
                    "artifact_id": st.r_aid,
                    "mode": "rework" if is_rework else "draft",
                },
            }
            continue

        if step == "review":
            call_seq += 1
            yield {
                "event": "agent_lifecycle",
                "data": {**scope, "agent": "review", "status": "running"},
            }
            review = None
            async for _item in _yield_call(task,
                    "review",
                    {
                        "query": st.q_payload,
                        "report": st.r_payload,
                        "insight": st.i_payload,
                    },
                    list(st.artifact_ids),
                    suffix=f":{call_seq}",):
                if _item.get("type") == "result":
                    review = _item.get("value")
                else:
                    yield _item
            if review is None:
                raise RuntimeError("review agent returned no result")
            st.agent_calls += 1
            st.review_done = True
            st.review_payload = review.get("payload") or {}
            st.review_approved = bool(st.review_payload.get("approved"))
            st.review_reasons = list(st.review_payload.get("reasons") or [])
            st.review_aid = review.get("artifact_id")
            if st.review_aid:
                st.artifact_ids.append(st.review_aid)
            yield {
                "event": "agent_lifecycle",
                "data": {
                    **scope,
                    "agent": "review",
                    "status": "completed",
                    "artifact_id": st.review_aid,
                    "approved": st.review_approved,
                },
            }
            if st.review_approved:
                yield {
                    "event": "review_approved",
                    "data": {**scope, "artifact_id": st.review_aid},
                }
            else:
                # do not return yet — router may rework/requery
                yield {
                    "event": "review_rejected",
                    "data": {
                        **scope,
                        "reasons": st.review_reasons,
                        "artifact_id": st.review_aid,
                        "may_rework": st.rework_count < budget.max_rework,
                        "may_requery": st.requery_count < budget.max_requery
                        and _reasons_indicate_evidence_gap(st.review_reasons),
                    },
                }
            continue

        if step == "export":
            for fmt in fmts:
                kind = "report" if fmt in REPORT_FORMATS else "evidence"
                call_seq += 1
                yield {
                    "event": "agent_lifecycle",
                    "data": {
                        **scope,
                        "agent": "export",
                        "status": "running",
                        "format": fmt,
                    },
                }
                exp = None
                async for _item in _yield_call(task,
                        "export",
                        {
                            "review": st.review_payload,
                            "report": st.r_payload,
                            "query": st.q_payload,
                            "kind": kind,
                            "format": fmt,
                        },
                        list(st.artifact_ids),
                        suffix=f":{fmt}:{call_seq}",):
                    if _item.get("type") == "result":
                        exp = _item.get("value")
                    else:
                        yield _item
                if exp is None:
                    raise RuntimeError("exp agent returned no result")
                st.agent_calls += 1
                if exp.get("artifact_id"):
                    st.artifact_ids.append(exp["artifact_id"])
                yield {
                    "event": "agent_lifecycle",
                    "data": {
                        **scope,
                        "agent": "export",
                        "status": "completed",
                        "artifact_id": exp.get("artifact_id"),
                        "format": fmt,
                        "download_url": (exp.get("payload") or {}).get("download_url"),
                    },
                }
                yield {
                    "event": "export_ready",
                    "data": {
                        **scope,
                        "format": fmt,
                        "artifact_id": exp.get("artifact_id"),
                        "download_url": (exp.get("payload") or {}).get("download_url"),
                        "file_name": (exp.get("payload") or {}).get("file_name"),
                    },
                }
            st.export_done = True
            continue

        # unknown step — stop safely
        st.stop_reason = f"unknown_step:{step}"
        yield {
            "event": "review_rejected",
            "data": {**scope, "reasons": [st.stop_reason], "stop_reason": st.stop_reason},
        }
        return

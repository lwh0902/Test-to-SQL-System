"""Phase 5 evidence diagnosis pipeline (admission → insight → gap≤1 → report → review → summary).

Uses injectable agent_call for tests; production can wrap deep_diagnosis dispatcher.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from app.agents.diagnosis_admission import admit_diagnosis, outcome_from_seed, phase5_budget
from app.agents.diagnosis_evidence import (
    extract_claims_from_insight,
    validate_claims,
    validate_report_sections,
)
from app.agents.diagnosis_gaps import GapFillTracker
from app.agents.diagnosis_summary import DiagnosisSummary, build_diagnosis_summary
from app.agents.query_outcome import QueryOutcome


AgentCall = Callable[..., AsyncIterator[dict]]


@dataclass
class DiagnosisPipelineResult:
    admitted: bool
    approved: bool = False
    entered_without_data: bool = False
    stop_reason: str = ""
    summary: Optional[DiagnosisSummary] = None
    agents_called: list[str] = field(default_factory=list)
    gap_duplicate_fills: int = 0
    unfounded_causal_count: int = 0
    key_claim_citation_rate: float = 1.0
    latency_ms: float = 0.0
    message: str = ""
    report_payload: dict = field(default_factory=dict)
    insight_payload: dict = field(default_factory=dict)
    review_payload: dict = field(default_factory=dict)


async def _collect_agent(agent_call: AgentCall, *args, **kwargs) -> dict:
    result = None
    async for item in agent_call(*args, **kwargs):
        if item.get("type") == "result":
            result = item.get("value")
    return result or {}


async def run_evidence_diagnosis(
    *,
    question: str,
    seed_query: Optional[dict] = None,
    explicit_intent: bool = True,
    agent_call: Optional[AgentCall] = None,
    export_formats: tuple | list = (),
    session_id: str = "",
    task_id: str = "diag_task",
    user_id: int = 0,
    space_id: str = "",
) -> DiagnosisPipelineResult:
    t0 = time.perf_counter()
    budget = phase5_budget()
    outcome = outcome_from_seed(seed_query)
    admission = admit_diagnosis(outcome, explicit_intent=explicit_intent)

    if not admission.allowed:
        return DiagnosisPipelineResult(
            admitted=False,
            entered_without_data=False,
            stop_reason=admission.reason,
            message=f"诊断未准入：{admission.reason}",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    if agent_call is None:
        return DiagnosisPipelineResult(
            admitted=True,
            stop_reason="no_agent_call",
            message="已准入但未提供 agent_call",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    agents: list[str] = []
    gap_tracker = GapFillTracker(max_per_gap=1)
    q_payload = dict(seed_query or {})
    if "query_outcome" not in q_payload and outcome:
        q_payload["query_outcome"] = {"status": admission.outcome_status, "rows_count": outcome.rows_count}

    artifact_ids = ["qr_1"]
    task = {
        "question": question,
        "task_id": task_id,
        "session_id": session_id,
        "user_id": user_id,
        "space_id": space_id,
    }

    # INSIGHT
    agents.append("insight")
    ins = await _collect_agent(
        agent_call,
        task,
        "insight",
        {"question": question, "query": q_payload},
        list(artifact_ids),
    )
    i_payload = ins.get("payload") or {}
    if ins.get("artifact_id"):
        artifact_ids.append(str(ins["artifact_id"]))

    claim_v = validate_claims(extract_claims_from_insight(i_payload))
    # apply normalized kinds back
    if claim_v.normalized_claims:
        i_payload = {
            **i_payload,
            "findings": claim_v.normalized_claims,
        }

    gaps = list(i_payload.get("evidence_gaps") or [])
    # GAP FILL at most once per gap
    for g in gaps[:3]:
        if gap_tracker.try_fill(g):
            agents.append("query")
            # optional gap query — synthetic no-op if agent returns empty
            gq = await _collect_agent(
                agent_call,
                task,
                "query",
                {"question": question, "task_type": "gap_fill", "evidence_gaps": [g]},
                list(artifact_ids),
                suffix=":gap",
            )
            if gq.get("artifact_id"):
                artifact_ids.append(str(gq["artifact_id"]))
            gp = gq.get("payload") or {}
            if gp.get("rows") or gp.get("rows_count"):
                q_payload = {**q_payload, **{k: gp[k] for k in ("rows", "rows_count", "columns", "sql") if k in gp}}
        # else duplicate counted inside tracker

    # wall check
    if (time.perf_counter() - t0) > budget.max_wall_seconds:
        return DiagnosisPipelineResult(
            admitted=True,
            approved=False,
            stop_reason="max_wall_seconds",
            agents_called=agents,
            gap_duplicate_fills=gap_tracker.duplicate_fill_count,
            unfounded_causal_count=claim_v.unfounded_causal_count,
            key_claim_citation_rate=claim_v.key_claim_citation_rate,
            message="诊断超时（>90s），未批准报告。",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # REPORT
    agents.append("report")
    rep = await _collect_agent(
        agent_call,
        task,
        "report",
        {"question": question, "query": q_payload, "insight": i_payload},
        list(artifact_ids),
    )
    r_payload = rep.get("payload") or {}
    if rep.get("artifact_id"):
        artifact_ids.append(str(rep["artifact_id"]))

    sec_v = validate_report_sections(r_payload.get("sections") or [], allowed_ids=set(artifact_ids))
    unfounded = max(claim_v.unfounded_causal_count, sec_v.unfounded_causal_count)
    cite_rate = min(claim_v.key_claim_citation_rate, sec_v.key_claim_citation_rate)

    # REVIEW
    agents.append("review")
    rev = await _collect_agent(
        agent_call,
        task,
        "review",
        {
            "question": question,
            "query": q_payload,
            "report": r_payload,
            "insight": i_payload,
        },
        list(artifact_ids),
    )
    review_payload = rev.get("payload") or {}
    # hard overlay: reject if evidence invalid
    if unfounded > 0 or cite_rate < 1.0:
        review_payload = {
            **review_payload,
            "approved": False,
            "reasons": list(review_payload.get("reasons") or [])
            + (["关键结论缺少证据引用"] if cite_rate < 1.0 else [])
            + (["存在无证据因果断言"] if unfounded > 0 else []),
        }

    approved = bool(review_payload.get("approved"))
    summary = build_diagnosis_summary(
        report=r_payload,
        review=review_payload,
        query=q_payload,
        evidence_ids=artifact_ids,
        session_id=session_id,
        task_id=task_id,
        report_artifact_id=str(rep.get("artifact_id") or ""),
    )

    # optional export only if approved — not required for phase5 core
    if approved and export_formats:
        agents.append("export")

    return DiagnosisPipelineResult(
        admitted=True,
        approved=approved,
        entered_without_data=False,
        stop_reason="stop_ok" if approved else "stop_rejected",
        summary=summary,
        agents_called=agents,
        gap_duplicate_fills=gap_tracker.duplicate_fill_count,
        unfounded_causal_count=unfounded,
        key_claim_citation_rate=cite_rate,
        latency_ms=(time.perf_counter() - t0) * 1000,
        message=summary.one_liner,
        report_payload=r_payload,
        insight_payload=i_payload,
        review_payload=review_payload,
    )

"""/api/chat 路由 - 传输适配层：JSON / SSE 均走 AnalysisApplicationService。"""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.application.analysis_service import AnalysisApplicationService, get_analysis_service
from app.application.contracts import TurnRequest
from app.application.sse_protocol import SseTerminalGuard
from app.core.auth import get_current_user
from app.core.database import engine
from app.core.rate_limit import limiter
from app.models.schemas import (
    ChartConfig,
    ChatRequest,
    ChatResponse,
    MetricCandidate,
    QueryIntent,
    TraceStep,
)
from app.services.authorization_service import (
    require_session_access,
    require_space_access,
    require_trace_access,
)
from app.services.trace_store import trace_store

router = APIRouter()


# ======== Trace API ========

@router.get("/api/traces/{trace_id}")
def get_trace(trace_id: str, user: dict = Depends(get_current_user)):
    require_trace_access(trace_id, user["user_id"])
    data = trace_store.get(trace_id)
    if not data:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT trace_id, question, intent, sql_text, rows_count, status, created_at FROM traces WHERE trace_id = :tid"
            ), {"tid": trace_id})
            row = result.fetchone()
            if not row:
                return {"error": "trace not found", "trace_id": trace_id}
            data = {
                "trace_id": row[0], "question": row[1],
                "intent": json.loads(row[2]) if row[2] else None,
                "sql": row[3], "rows_count": row[4],
                "status": row[5], "created_at": str(row[6]),
            }
            steps_result = conn.execute(text(
                "SELECT node, status, input_data, output_data, elapsed_ms FROM trace_steps WHERE trace_id = :tid ORDER BY sort_order"
            ), {"tid": trace_id})
            data["steps"] = [
                {"node": s[0], "status": s[1], "input": json.loads(s[2]) if s[2] else {},
                 "output": json.loads(s[3]) if s[3] else {}, "elapsed_ms": s[4]}
                for s in steps_result.fetchall()
            ]
    return data


@router.get("/api/traces")
def list_traces(limit: int = 20, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT trace_id, question, status, rows_count, created_at FROM traces WHERE user_id = :user_id ORDER BY created_at DESC LIMIT :lim"
        ), {"lim": limit, "user_id": user["user_id"]})
        traces = [
            {"trace_id": r[0], "question": r[1], "status": r[2],
             "rows_count": r[3], "created_at": str(r[4])}
            for r in result.fetchall()
        ]
    if not traces:
        return {"traces": trace_store.list_recent(limit, user["user_id"])}
    return {"traces": traces}


def _turn_request(req: ChatRequest, user: dict) -> TurnRequest:
    return TurnRequest.from_chat(req, user)


def _to_chat_response(public: dict) -> ChatResponse:
    intent = public.get("intent")
    intent_obj = None
    if intent:
        if isinstance(intent, QueryIntent):
            intent_obj = intent
        elif isinstance(intent, dict):
            try:
                intent_obj = QueryIntent(**intent)
            except Exception:
                intent_obj = None

    chart = public.get("chart")
    chart_obj = None
    if chart and isinstance(chart, dict):
        try:
            chart_obj = ChartConfig(**chart)
        except Exception:
            chart_obj = None
    elif isinstance(chart, ChartConfig):
        chart_obj = chart

    candidates = public.get("candidates") or []
    candidate_objs = []
    for c in candidates:
        if isinstance(c, dict):
            try:
                candidate_objs.append(MetricCandidate(**c))
            except Exception:
                pass
        elif isinstance(c, MetricCandidate):
            candidate_objs.append(c)

    trace_steps = []
    for t in public.get("trace") or []:
        if isinstance(t, dict):
            try:
                trace_steps.append(TraceStep(**t))
            except Exception:
                pass
        elif isinstance(t, TraceStep):
            trace_steps.append(t)

    evidence = public.get("evidence") if isinstance(public.get("evidence"), dict) else None
    artifacts = public.get("artifacts") if isinstance(public.get("artifacts"), list) else []
    q_out = public.get("query_outcome") if isinstance(public.get("query_outcome"), dict) else None
    sup = public.get("supervisor_decision") if isinstance(public.get("supervisor_decision"), dict) else None
    tspec = public.get("task_spec") if isinstance(public.get("task_spec"), dict) else None
    clarify_slots = list(public.get("clarify_slots") or [])
    if not clarify_slots and evidence:
        clarify_slots = list(evidence.get("clarify_slots") or [])
    ux_hints = list(public.get("ux_hints") or [])
    if not ux_hints and evidence:
        ux_hints = list(evidence.get("ux_hints") or [])
    next_actions = list(public.get("next_actions") or [])
    if not next_actions and evidence:
        next_actions = list(evidence.get("next_actions") or [])

    return ChatResponse(
        type=public.get("type") or public.get("response_type") or "answer",
        trace_id=public.get("trace_id") or "",
        answer=public.get("answer") or public.get("message") or "",
        message=public.get("message") or public.get("answer") or "",
        intent=intent_obj,
        sql=public.get("sql") or "",
        columns=list(public.get("columns") or []),
        rows=list(public.get("rows") or []),
        rows_count=public.get("rows_count"),
        chart=chart_obj,
        candidates=candidate_objs,
        trace=trace_steps,
        terminal_status=public.get("terminal_status") or None,
        evidence=evidence,
        artifacts=[a for a in artifacts if isinstance(a, dict)],
        query_outcome=q_out,
        supervisor_decision=sup,
        task_spec=tspec,
        clarify_slots=clarify_slots,
        ux_hints=ux_hints,
        next_actions=[a for a in next_actions if isinstance(a, dict)],
        stop_reason=public.get("stop_reason") or None,
        kernel_route=public.get("kernel_route") or None,
        task_id=public.get("task_id"),
        data_map=public.get("data_map") if isinstance(public.get("data_map"), dict) else None,
        db_identity=public.get("db_identity") if isinstance(public.get("db_identity"), dict) else None,
    )


# ======== SSE 流式接口 — 传输适配 ========

@router.post("/api/chat/stream")
@limiter.limit("30/minute")
async def chat_stream(request: Request, req: ChatRequest, user: dict = Depends(get_current_user)):
    require_space_access(req.space_id, user["user_id"])
    require_session_access(req.session_id, user["user_id"], req.space_id)

    turn_req = _turn_request(req, user)
    service = get_analysis_service()

    async def event_generator():
        guard = SseTerminalGuard()
        async for event_name, data in service.handle_turn_stream(turn_req):
            frame = guard.try_emit(event_name, data if isinstance(data, dict) else {})
            if frame is not None:
                yield frame
        # If service forgot complete, emit a minimal terminal once
        if not guard.closed:
            frame = guard.try_emit(
                "complete",
                {
                    "type": "error",
                    "message": "服务未返回终态",
                    "terminal_status": "STOP_ERROR",
                    "kernel_route": "legacy_rollback",
                    "trace_id": "",
                    "rows": [],
                    "rows_count": 0,
                    "columns": [],
                    "sql": "",
                    "candidates": [],
                    "trace": [],
                },
            )
            if frame:
                yield frame

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ======== JSON 接口 — 传输适配 ========

@router.post("/api/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat(request: Request, req: ChatRequest, user: dict = Depends(get_current_user)):
    require_space_access(req.space_id, user["user_id"])
    require_session_access(req.session_id, user["user_id"], req.space_id)

    turn_req = _turn_request(req, user)
    service = get_analysis_service()
    result = await service.handle_turn(turn_req)
    public = result.to_public_dict()
    return _to_chat_response(public)


# Re-export for tests that still patch get_graph at chat module level
def get_graph():
    from app.services.agent import get_graph as _gg
    return _gg()

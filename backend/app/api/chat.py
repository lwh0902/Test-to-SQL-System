"""/api/chat 路由 - 支持 space_id + session_id，SSE 流式和 JSON"""

import os
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.models.schemas import ChatRequest, ChatResponse, TraceStep, QueryIntent, ChartConfig
from app.services.agent import get_graph, AgentState
from app.services.persistence import save_message, save_trace
from app.services.trace_store import trace_store
from app.core.auth import get_current_user
from app.core.database import engine
from app.services.authorization_service import require_space_access, require_session_access, require_trace_access
from app.core.rate_limit import limiter
from fastapi import Request

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


# ======== SSE 流式接口（LangGraph） — 异步 ========

_async_llm_client = None


def _get_async_llm_client():
    global _async_llm_client
    if _async_llm_client is None:
        from anthropic import AsyncAnthropic
        _async_llm_client = AsyncAnthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
    return _async_llm_client


def _should_generate_analysis_answer(response_type: str, has_data: bool, has_plan: bool) -> bool:
    """Only metric/data query answers should get the analysis-summary pass."""
    return not has_plan and response_type == "answer"


@router.post("/api/chat/stream")
@limiter.limit("30/minute")
async def chat_stream(request: Request, req: ChatRequest, user: dict = Depends(get_current_user)):
    require_space_access(req.space_id, user["user_id"])
    require_session_access(req.session_id, user["user_id"], req.space_id)
    graph = get_graph()

    user_role = user.get("role", "tester")
    user_id = user.get("user_id", 1)

    initial_state = AgentState(
        question=req.question,
        user_role=user_role,
        workspace_id=req.workspace_id,
        space_id=req.space_id,
        user_id=user_id,
        session_id=req.session_id,
        selected_metric=req.selected_metric,
        selected_query_type=req.selected_query_type,
    )

    async def event_generator():
        accumulated: dict = {}
        sent_count = 0
        async for output in graph.astream(initial_state):
            for node_name, node_output in output.items():
                if not node_output:
                    continue
                accumulated.update({k: v for k, v in node_output.items() if v is not None and v != "" and v != [] and v != {}})

                events = node_output.get("events", [])
                for evt in events[sent_count:]:
                    yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
                sent_count = len(events)

        has_plan = bool(accumulated.get("plan"))
        response_type = accumulated.get("response_type", "answer")
        has_data = response_type == "answer" and bool(accumulated.get("rows"))
        answer_text = ""

        # 加载历史上下文用于回答生成
        history_lines = []
        if req.session_id:
            from app.services.persistence import load_recent_messages
            history = load_recent_messages(req.session_id, limit=6, user_id=user_id, space_id=req.space_id)
            for msg in history:
                role_label = "用户" if msg["role"] == "user" else "AI"
                line = f"{role_label}: {msg['content']}"
                if msg["role"] == "assistant" and msg.get("meta"):
                    meta = msg["meta"]
                    if isinstance(meta, str):
                        import json as _json
                        meta = _json.loads(meta)
                    rows_count = meta.get("rows_count", 0)
                    if meta.get("sql"):
                        line += f" (执行了SQL，返回{rows_count}条数据)"
                history_lines.append(line)

        if _should_generate_analysis_answer(response_type, has_data, has_plan):
            try:
                client = _get_async_llm_client()

                intent = accumulated.get("intent")
                metric_name = ""
                if intent:
                    if isinstance(intent, QueryIntent):
                        metric_name = intent.metric or ""
                    elif isinstance(intent, dict):
                        metric_name = intent.get("metric", "")

                rows = accumulated.get("rows", [])
                columns = accumulated.get("columns", [])
                data_summary = ""
                if rows and columns:
                    preview_rows = rows[:5]
                    data_summary = "数据列: " + ", ".join(columns) + "\n"
                    for i, row in enumerate(preview_rows):
                        data_summary += f"第{i+1}行: " + ", ".join(f"{c}={row.get(c)}" for c in columns) + "\n"
                    if len(rows) > 5:
                        data_summary += f"...共 {len(rows)} 行\n"
                elif columns:
                    data_summary = f"查询执行成功，列: {', '.join(columns)}，但返回了 0 条数据。\n"

                prompt = f"你是 DataPilot Agent 数据分析助手。\n\n"
                if history_lines:
                    prompt += "历史对话:\n" + "\n".join(history_lines) + "\n\n"
                prompt += f"用户当前查询了指标 '{metric_name}'，查询结果如下：\n\n"
                prompt += f"{data_summary}\n"
                if not rows:
                    prompt += "查询返回了 0 条数据。请结合用户的问题和历史对话，分析可能的原因（如时间范围内无数据、筛选条件过严等），并给出建议。2-3句话即可。\n"
                else:
                    prompt += "请用简洁的中文总结查询结果（2-3句话），包含关键数字和趋势。不要重复原始数据。"

                yield f"event: answer_generating\ndata: {json.dumps({'text': '正在生成分析结论'}, ensure_ascii=False)}\n\n"

                async with client.messages.stream(
                    model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    async for text in stream.text_stream:
                        answer_text += text
                        yield f"event: answer_chunk\ndata: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
            except Exception:
                answer_text = accumulated.get("message", "")
        else:
            answer_text = accumulated.get("message", "")

        complete_data = {
            "type": accumulated.get("response_type", "answer"),
            "trace_id": accumulated.get("trace_id", ""),
            "answer": answer_text,
            "sql": accumulated.get("sql", ""),
            "columns": accumulated.get("columns", []),
            "rows": accumulated.get("rows", []),
            "message": answer_text,
            "candidates": accumulated.get("candidates", []),
            "chart": accumulated.get("chart"),
            "trace": accumulated.get("trace", []),
            "plan": accumulated.get("plan"),
            "plan_results": accumulated.get("plan_results"),
        }

        # data_map 类型：从 trace 中提取结构化数据
        if accumulated.get("response_type") == "data_map":
            for step in (accumulated.get("trace") or []):
                if step.get("node") == "schema_help_responder" and step.get("output"):
                    output = step["output"]
                    if output.get("data_map"):
                        complete_data["data_map"] = output["data_map"]
                    if output.get("db_identity"):
                        complete_data["db_identity"] = output["db_identity"]
                    break
        intent = accumulated.get("intent")
        if intent:
            if isinstance(intent, QueryIntent):
                complete_data["intent"] = intent.model_dump()
            elif isinstance(intent, dict):
                complete_data["intent"] = intent
        yield f"event: complete\ndata: {json.dumps(complete_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ======== JSON 接口 ========

@router.post("/api/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat(request: Request, req: ChatRequest, user: dict = Depends(get_current_user)):
    require_space_access(req.space_id, user["user_id"])
    require_session_access(req.session_id, user["user_id"], req.space_id)
    graph = get_graph()

    user_role = user.get("role", "tester")
    user_id = user.get("user_id", 1)

    initial_state = AgentState(
        question=req.question,
        user_role=user_role,
        workspace_id=req.workspace_id,
        space_id=req.space_id,
        user_id=user_id,
        session_id=req.session_id,
        selected_metric=req.selected_metric,
        selected_query_type=req.selected_query_type,
    )

    result = await graph.ainvoke(initial_state)

    intent = result.get("intent")
    intent_obj = None
    if intent:
        if isinstance(intent, QueryIntent):
            intent_obj = intent
        elif isinstance(intent, dict):
            intent_obj = QueryIntent(**intent)

    chart = result.get("chart")
    chart_obj = None
    if chart and isinstance(chart, dict):
        chart_obj = ChartConfig(**chart)
    elif isinstance(chart, ChartConfig):
        chart_obj = chart

    candidates = result.get("candidates", [])
    candidate_objs = []
    for c in candidates:
        if isinstance(c, dict):
            from app.models.schemas import MetricCandidate
            candidate_objs.append(MetricCandidate(**c))
        else:
            candidate_objs.append(c)

    trace_steps = []
    for t in result.get("trace", []):
        if isinstance(t, dict):
            trace_steps.append(TraceStep(**t))
        else:
            trace_steps.append(t)

    return ChatResponse(
        type=result.get("response_type", "answer"),
        trace_id=result.get("trace_id", ""),
        answer=result.get("message", ""),
        message=result.get("message", ""),
        intent=intent_obj,
        sql=result.get("sql", ""),
        columns=result.get("columns", []),
        rows=result.get("rows", []),
        chart=chart_obj,
        candidates=candidate_objs,
        trace=trace_steps,
    )

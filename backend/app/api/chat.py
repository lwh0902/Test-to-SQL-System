"""/api/chat 路由 - 支持 space_id + session_id，SSE 流式和 JSON"""

import os
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.models.schemas import ChatRequest, ChatResponse, TraceStep, QueryIntent, ChartConfig
from app.services.agent import build_graph, AgentState
from app.services.persistence import save_message, save_trace
from app.services.trace_store import trace_store
from app.core.database import engine

router = APIRouter()


# ======== Trace API ========

@router.get("/api/traces/{trace_id}")
def get_trace(trace_id: str):
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
def list_traces(limit: int = 20):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT trace_id, question, status, rows_count, created_at FROM traces ORDER BY created_at DESC LIMIT :lim"
        ), {"lim": limit})
        traces = [
            {"trace_id": r[0], "question": r[1], "status": r[2],
             "rows_count": r[3], "created_at": str(r[4])}
            for r in result.fetchall()
        ]
    if not traces:
        return {"traces": trace_store.list_recent(limit)}
    return {"traces": traces}


# ======== SSE 流式接口（LangGraph） ========

@router.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    graph = build_graph()

    initial_state = AgentState(
        question=req.question,
        user_role=req.user_role,
        workspace_id=req.workspace_id,
        space_id=req.space_id,
        user_id=req.user_id,
        session_id=req.session_id,
        selected_metric=req.selected_metric,
        selected_query_type=req.selected_query_type,
    )

    def event_generator():
        accumulated: dict = {}
        sent_count = 0
        for output in graph.stream(initial_state):
            for node_name, node_output in output.items():
                if not node_output:
                    continue
                accumulated.update({k: v for k, v in node_output.items() if v is not None and v != "" and v != [] and v != {}})

                events = node_output.get("events", [])
                for evt in events[sent_count:]:
                    yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
                sent_count = len(events)

        # 如果有数据结果且 plan 没有生成过回答，用 LLM 流式生成回答
        has_plan = bool(accumulated.get("plan"))
        has_data = (accumulated.get("response_type") == "answer"
                    and accumulated.get("rows") and len(accumulated.get("rows", [])) > 0)
        answer_text = ""

        if has_data and not has_plan:
            try:
                from anthropic import Anthropic
                client = Anthropic(
                    api_key=os.getenv("LLM_API_KEY"),
                    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
                )

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
                    # 只传前 5 行给 LLM，避免 token 过多
                    preview_rows = rows[:5]
                    data_summary = "数据列: " + ", ".join(columns) + "\n"
                    for i, row in enumerate(preview_rows):
                        data_summary += f"第{i+1}行: " + ", ".join(f"{c}={row.get(c)}" for c in columns) + "\n"
                    if len(rows) > 5:
                        data_summary += f"...共 {len(rows)} 行\n"

                prompt = (
                    f"你是 DataPilot Agent 数据分析助手。用户查询了指标 '{metric_name}'，查询结果如下：\n\n"
                    f"{data_summary}\n"
                    f"请用简洁的中文总结查询结果（2-3句话），包含关键数字和趋势。不要重复原始数据。"
                )

                stream = client.messages.create(
                    model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                    stream=True,
                )

                for event in stream:
                    if event.type == "content_block_delta" and hasattr(event, "delta"):
                        chunk = event.delta.text if hasattr(event.delta, "text") else ""
                        if chunk:
                            answer_text += chunk
                            yield f"event: answer_chunk\ndata: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
            except Exception:
                # LLM 失败就用 query_executor 的基础回答
                answer_text = accumulated.get("message", "")
        elif has_plan:
            # Plan-and-Execute: summary_agent 已生成 message，按 chunk 流式发出
            answer_text = accumulated.get("message", "")
            if answer_text:
                yield f"event: answer_chunk\ndata: {json.dumps({'text': answer_text}, ensure_ascii=False)}\n\n"
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


# ======== JSON 接口（复用同一 graph） ========

@router.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    graph = build_graph()

    initial_state = AgentState(
        question=req.question,
        user_role=req.user_role,
        workspace_id=req.workspace_id,
        space_id=req.space_id,
        user_id=req.user_id,
        session_id=req.session_id,
        selected_metric=req.selected_metric,
        selected_query_type=req.selected_query_type,
    )

    result = graph.invoke(initial_state)

    # graph.invoke 返回 dict，提取字段构造 ChatResponse
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

"""持久化服务 - 保存消息和 trace 到数据库

从 chat.py 提取，同时兼容 ChatRequest（JSON 接口）和 AgentState（SSE/Graph 接口）。
"""

import json
import uuid

from sqlalchemy import text

from app.core.database import engine


def load_recent_messages(session_id: str | None, limit: int = 6) -> list[dict]:
    """从 chat_messages 取最近 N 条消息（用于上下文）"""
    if not session_id:
        return []
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT role, content, meta FROM chat_messages
            WHERE session_id = :sid
            ORDER BY created_at DESC LIMIT :lim
        """), {"sid": session_id, "lim": limit})
        messages = []
        for r in reversed(list(result)):  # 按时间正序返回
            msg = {"role": r[0], "content": r[1]}
            if r[2]:
                meta = r[2] if isinstance(r[2], dict) else json.loads(r[2])
                msg["meta"] = meta
            messages.append(msg)
        return messages


def load_working_memory(session_id: str | None) -> dict | None:
    """加载 session 的 working_memory"""
    if not session_id:
        return None
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT working_memory FROM chat_sessions WHERE id = :sid"
        ), {"sid": session_id})
        row = result.fetchone()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return None


def update_working_memory(session_id: str | None, memory: dict):
    """更新 session 的 working_memory"""
    if not session_id:
        return
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE chat_sessions SET working_memory = :wm WHERE id = :sid"
        ), {"wm": json.dumps(memory, ensure_ascii=False), "sid": session_id})
        conn.commit()


def auto_rename_session(session_id: str | None, question: str):
    """如果 session 标题是默认的"新对话"，用 LLM 自动生成标题"""
    if not session_id:
        return
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT title FROM chat_sessions WHERE id = :sid"
        ), {"sid": session_id})
        row = result.fetchone()
        if not row or row[0] != "新对话":
            return

    # 用 LLM 生成标题（同步调用，简单快速）
    import os
    from anthropic import Anthropic

    try:
        client = Anthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=32,
            system="根据用户的问题生成一个简短的对话标题，不超过10个字，不要加引号，只输出标题。",
            messages=[{"role": "user", "content": question}],
        )
        title = ""
        for block in response.content:
            if hasattr(block, "text"):
                title = block.text.strip()
                break
        if title:
            with engine.connect() as conn:
                conn.execute(text(
                    "UPDATE chat_sessions SET title = :title WHERE id = :sid"
                ), {"title": title[:50], "sid": session_id})
                conn.commit()
    except Exception:
        pass


def save_message(session_id: str | None, role: str, content: str, meta: dict | None = None):
    """保存单条消息到 chat_messages，并更新 session 时间戳"""
    if not session_id:
        return
    msg_id = uuid.uuid4().hex[:16]
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO chat_messages (id, session_id, role, content, meta)
            VALUES (:id, :sid, :role, :content, :meta)
        """), {
            "id": msg_id, "sid": session_id, "role": role,
            "content": content,
            "meta": json.dumps(meta, ensure_ascii=False) if meta else None,
        })
        conn.execute(text("UPDATE chat_sessions SET updated_at = NOW() WHERE id = :sid"), {"sid": session_id})
        conn.commit()


def save_trace(
    session_id: str | None,
    user_id: int,
    space_id: str,
    trace_id: str,
    question: str,
    intent: dict | None,
    sql: str,
    rows_count: int,
    status: str,
    trace_steps: list[dict],
):
    """保存 trace 和 trace_steps 到数据库"""
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO traces (trace_id, session_id, user_id, space_id, question, intent, sql_text, rows_count, status)
            VALUES (:tid, :sid, :uid, :space_id, :question, :intent, :sql, :rows, :status)
        """), {
            "tid": trace_id, "sid": session_id, "uid": user_id,
            "space_id": space_id, "question": question,
            "intent": json.dumps(intent, ensure_ascii=False) if intent else None,
            "sql": sql, "rows": rows_count, "status": status,
        })
        for i, step in enumerate(trace_steps):
            conn.execute(text("""
                INSERT INTO trace_steps (trace_id, node, status, output_data, sort_order)
                VALUES (:tid, :node, :status, :output, :sort)
            """), {
                "tid": trace_id, "node": step.get("node", ""), "status": step.get("status", "done"),
                "output": json.dumps(step.get("output", {}), ensure_ascii=False),
                "sort": i,
            })
        conn.commit()


def persist_from_agent_state(state):
    """从 AgentState 保存完整的消息和 trace（persister 节点调用）"""
    from app.services.trace_store import trace_store

    session_id = state.session_id
    if not session_id:
        return

    # 1. 保存用户消息
    save_message(session_id, "user", state.question)

    # 2. 构建 assistant 消息的 meta
    meta = {
        "type": state.response_type,
        "trace_id": state.trace_id,
        "sql": state.sql,
        "columns": state.columns,
        "rows_count": len(state.rows),
    }
    if state.chart:
        meta["chart"] = state.chart
    if state.candidates:
        meta["candidates"] = state.candidates
    if state.intent:
        meta["intent"] = state.intent.model_dump()

    # 3. 保存 assistant 消息
    save_message(session_id, "assistant", state.message, meta)

    # 4. 保存 trace
    trace_status = (
        "clarification" if state.response_type == "clarification"
        else "error" if state.response_type == "error"
        else "success"
    )
    save_trace(
        session_id=session_id,
        user_id=state.user_id,
        space_id=state.space_id,
        trace_id=state.trace_id,
        question=state.question,
        intent=state.intent.model_dump() if state.intent else None,
        sql=state.sql,
        rows_count=len(state.rows),
        status=trace_status,
        trace_steps=state.trace,
    )

    # 5. 保存到内存 trace_store（兼容）
    trace_store.save(state.trace_id, {
        "trace_id": state.trace_id, "question": state.question,
        "intent": state.intent.model_dump() if state.intent else None,
        "steps": state.trace,
        "sql": state.sql, "rows_count": len(state.rows),
    })

    # 6. 更新 working_memory（结构化，不调 LLM）
    if state.intent and state.response_type == "answer":
        new_memory = {}
        # 保留已有的偏好
        if state.working_memory:
            new_memory.update(state.working_memory)
        # 用当前 intent 覆盖
        new_memory["last_metric"] = state.intent.metric
        new_memory["last_query_type"] = state.intent.query_type
        if state.intent.time_range:
            new_memory["last_time_range"] = state.intent.time_range.model_dump()
        new_memory["last_dimensions"] = state.intent.dimensions
        new_memory["last_filters"] = state.intent.filters
        update_working_memory(session_id, new_memory)

    # 7. 自动生成 session 标题
    auto_rename_session(session_id, state.question)

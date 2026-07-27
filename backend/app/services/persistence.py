"""持久化服务 - 保存消息和 trace 到数据库

从 chat.py 提取，同时兼容 ChatRequest（JSON 接口）和 AgentState（SSE/Graph 接口）。
"""

import json
import uuid
from typing import Any

from sqlalchemy import text

from app.core.database import engine


def load_recent_messages(session_id: str | None, limit: int = 6, user_id: int | None = None, space_id: str | None = None) -> list[dict]:
    """从 chat_messages 取最近 N 条消息（用于上下文）"""
    if not session_id:
        return []
    with engine.connect() as conn:
        ownership = "" if user_id is None or space_id is None else " AND session_id IN (SELECT id FROM chat_sessions WHERE id = :sid AND user_id = :user_id AND space_id = :space_id)"
        result = conn.execute(text("""
            SELECT role, content, meta FROM chat_messages
            WHERE session_id = :sid
        """ + ownership + " ORDER BY created_at DESC, id DESC LIMIT :lim"), {"sid": session_id, "lim": limit, "user_id": user_id, "space_id": space_id})
        messages = []
        for r in reversed(list(result)):  # 按时间正序返回
            msg = {"role": r[0], "content": r[1]}
            if r[2]:
                meta = r[2] if isinstance(r[2], dict) else json.loads(r[2])
                msg["meta"] = meta
            messages.append(msg)
        return messages


def load_working_memory(session_id: str | None, user_id: int | None = None, space_id: str | None = None) -> dict | None:
    """加载 session 的 working_memory"""
    if not session_id:
        return None
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT working_memory FROM chat_sessions WHERE id = :sid" + (" AND user_id = :user_id AND space_id = :space_id" if user_id is not None and space_id is not None else "")
        ), {"sid": session_id, "user_id": user_id, "space_id": space_id})
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


def _session_owned(
    conn,
    session_id: str,
    *,
    user_id: int | None = None,
    space_id: str | None = None,
) -> bool:
    """True when session exists and matches optional user/space isolation keys."""
    if user_id is None and space_id is None:
        row = conn.execute(
            text("SELECT id FROM chat_sessions WHERE id = :sid"),
            {"sid": session_id},
        ).fetchone()
        return bool(row)
    clauses = ["id = :sid"]
    params: dict = {"sid": session_id}
    if user_id is not None:
        clauses.append("user_id = :user_id")
        params["user_id"] = int(user_id)
    if space_id is not None:
        clauses.append("space_id = :space_id")
        params["space_id"] = str(space_id)
    row = conn.execute(
        text(f"SELECT id FROM chat_sessions WHERE {' AND '.join(clauses)}"),
        params,
    ).fetchone()
    return bool(row)


def save_message(
    session_id: str | None,
    role: str,
    content: str,
    meta: dict | None = None,
    *,
    user_id: int | None = None,
    space_id: str | None = None,
):
    """保存单条消息到 chat_messages，并更新 session 时间戳。

    When user_id/space_id are provided, refuse writes to sessions outside that
    isolation boundary (user × session × space).
    """
    if not session_id:
        return
    msg_id = uuid.uuid4().hex[:16]
    with engine.connect() as conn:
        if not _session_owned(conn, session_id, user_id=user_id, space_id=space_id):
            return
        conn.execute(text("""
            INSERT INTO chat_messages (id, session_id, role, content, meta)
            VALUES (:id, :sid, :role, :content, :meta)
        """), {
            "id": msg_id, "sid": session_id, "role": role,
            "content": content or "",
            "meta": json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
        })
        if user_id is not None and space_id is not None:
            conn.execute(
                text(
                    "UPDATE chat_sessions SET updated_at = NOW() "
                    "WHERE id = :sid AND user_id = :user_id AND space_id = :space_id"
                ),
                {"sid": session_id, "user_id": int(user_id), "space_id": str(space_id)},
            )
        else:
            conn.execute(
                text("UPDATE chat_sessions SET updated_at = NOW() WHERE id = :sid"),
                {"sid": session_id},
            )
        conn.commit()


def persist_turn_result(
    *,
    session_id: str | None,
    user_id: int,
    space_id: str,
    question: str,
    result: Any,
    rename: bool = True,
) -> bool:
    """Persist one user turn + assistant TurnResult under user/session/space isolation.

    Returns True when both messages were written. Safe no-op when session_id missing
    or session is not owned by (user_id, space_id).
    """
    if not session_id or not question:
        return False
    # Accept TurnResult dataclass or public dict
    if hasattr(result, "to_public_dict") and callable(getattr(result, "to_public_dict")):
        public = result.to_public_dict()
    elif isinstance(result, dict):
        public = result
    else:
        return False

    answer = (
        public.get("answer")
        or public.get("message")
        or public.get("content")
        or ""
    )
    if not isinstance(answer, str):
        answer = str(answer)

    rows = list(public.get("rows") or [])
    raw_trace = list(public.get("trace") or [])
    _META_ROWS_CAP = 200
    meta: dict[str, Any] = {
        "type": public.get("type") or public.get("response_type") or "answer",
        "trace_id": public.get("trace_id") or "",
        "sql": public.get("sql") or "",
        "columns": list(public.get("columns") or []),
        "rows_count": int(public.get("rows_count") or len(rows) or 0),
        "rows": rows[:_META_ROWS_CAP],
        "terminal_status": public.get("terminal_status") or "",
        "stop_reason": public.get("stop_reason") or "",
        "kernel_route": public.get("kernel_route") or "",
        "trace": [
            {
                "node": (s or {}).get("node") if isinstance(s, dict) else None,
                "status": (s or {}).get("status") if isinstance(s, dict) else None,
                "elapsed_ms": (s or {}).get("elapsed_ms") if isinstance(s, dict) else None,
            }
            for s in raw_trace
            if isinstance(s, dict) or s is not None
        ],
    }
    for key in (
        "chart",
        "candidates",
        "intent",
        "data_map",
        "db_identity",
        "analysis_spec",
        "query_outcome",
        "evidence",
        "artifacts",
        "task_id",
        "clarify_slots",
        "ux_hints",
        "next_actions",
        "supervisor_decision",
        "task_spec",
        "active_state_version",
    ):
        val = public.get(key)
        if val not in (None, "", [], {}):
            meta[key] = val

    # Ownership-gated writes
    with engine.connect() as conn:
        if not _session_owned(conn, session_id, user_id=int(user_id), space_id=str(space_id)):
            return False

    save_message(
        session_id,
        "user",
        question,
        user_id=int(user_id),
        space_id=str(space_id),
    )
    save_message(
        session_id,
        "assistant",
        answer,
        meta,
        user_id=int(user_id),
        space_id=str(space_id),
    )
    if rename:
        try:
            auto_rename_session(session_id, question)
        except Exception:
            pass
    return True


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


def _calc_result_status(state) -> str:
    """计算上一轮查询的结果状态"""
    if state.response_type == "error":
        return "error"
    if state.response_type == "clarification":
        return "clarification"
    if len(state.rows) == 0:
        return "no_data"
    return "success"


def _extract_target(state) -> str | None:
    """从 state 提取操作目标（指标名或表名）"""
    if getattr(state, "table_target", None):
        return state.table_target
    if state.intent and state.intent.metric:
        return state.intent.metric
    return None


def persist_from_agent_state(state):
    """从 AgentState 保存完整的消息和 trace（persister 节点调用）"""
    from app.services.trace_store import trace_store

    session_id = state.session_id
    if not session_id:
        return

    # 1. 保存用户消息
    save_message(session_id, "user", state.question)

    # 2. 构建 assistant 消息的 meta
    # rows 有上限地写入 meta，保证刷新历史后图表/数据表/单值卡可恢复；
    # trace 只存精简版（node/status/elapsed），完整 trace 走 /api/traces。
    _META_ROWS_CAP = 200
    rows = getattr(state, "rows", None) or []
    raw_trace = getattr(state, "trace", None) or []
    meta = {
        "type": state.response_type,
        "trace_id": state.trace_id,
        "sql": state.sql,
        "columns": state.columns,
        "rows_count": len(rows),
        "rows": rows[:_META_ROWS_CAP],
        "trace": [
            {"node": s.get("node"), "status": s.get("status"), "elapsed_ms": s.get("elapsed_ms")}
            for s in raw_trace
        ],
    }
    if state.chart:
        meta["chart"] = state.chart
    if state.candidates:
        meta["candidates"] = state.candidates
    if state.intent:
        meta["intent"] = state.intent.model_dump()

    # data_map 类型：从 trace 中提取结构化数据
    if state.response_type == "data_map":
        for step in state.trace:
            if step.get("node") == "schema_help_responder" and step.get("output"):
                output = step["output"]
                if output.get("data_map"):
                    meta["data_map"] = output["data_map"]
                if output.get("db_identity"):
                    meta["db_identity"] = output["db_identity"]
                break

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
        "user_id": state.user_id,
        "intent": state.intent.model_dump() if state.intent else None,
        "steps": state.trace,
        "sql": state.sql, "rows_count": len(state.rows),
    })

    # 6. 更新 working_memory（任务帧）
    if state.response_type in ("answer", "chat", "schema_help", "data_map"):
        new_memory = {}
        if state.working_memory:
            new_memory.update(state.working_memory)

        # 只有查询型 answer 才覆盖 frame，说明类 answer 保留上一轮 frame
        if state.response_type == "answer" and state.route not in ("database_profile",):
            new_memory["last_route"] = state.route
            new_memory["last_target"] = _extract_target(state)
            new_memory["last_target_type"] = "table" if getattr(state, "table_target", None) else "metric"
            new_memory["last_sql"] = state.sql
            new_memory["last_params"] = state.params
            new_memory["last_result_status"] = _calc_result_status(state)
            new_memory["last_rows_count"] = len(state.rows)
            new_memory["last_response_type"] = state.response_type
            new_memory["last_error"] = state.error_code or None
            if state.intent:
                new_memory["last_metric"] = state.intent.metric
                new_memory["last_query_type"] = state.intent.query_type
                if state.intent.time_range:
                    new_memory["last_time_range"] = state.intent.time_range.model_dump()
                new_memory["last_dimensions"] = state.intent.dimensions
                new_memory["last_filters"] = state.intent.filters

        update_working_memory(session_id, new_memory)

    # 7. 自动生成 session 标题
    auto_rename_session(session_id, state.question)

    # 8. 每六个用户轮次压缩一次；摘要仅以当前 session/user/space 读取。
    try:
        from app.services.session_memory_service import refresh_session_memory
        messages = load_recent_messages(session_id, limit=12, user_id=state.user_id, space_id=state.space_id)
        refresh_session_memory(session_id, state.user_id, state.space_id, messages)
    except Exception:
        pass

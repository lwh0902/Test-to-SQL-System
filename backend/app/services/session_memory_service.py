"""会话隔离的长期记忆：以摘要延续单个会话，不跨会话召回。"""

from __future__ import annotations

import os

from sqlalchemy import text

from app.core.database import engine


def load_session_memory(session_id: str | None, user_id: int, space_id: str) -> str | None:
    if not session_id:
        return None
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT summary, source_turns FROM session_memories
            WHERE session_id = :session_id AND user_id = :user_id AND space_id = :space_id
        """), {"session_id": session_id, "user_id": user_id, "space_id": space_id}).fetchone()
    return row[0] if row else None


def should_refresh_session_memory(user_turns: int, source_turns: int | None) -> bool:
    return user_turns >= 6 and user_turns % 6 == 0 and source_turns != user_turns


def refresh_session_memory(session_id: str | None, user_id: int, space_id: str, messages: list[dict]) -> None:
    if not session_id:
        return
    with engine.connect() as conn:
        user_turns = conn.execute(text("""
            SELECT COUNT(*) FROM chat_messages
            WHERE session_id = :session_id AND role = 'user'
        """), {"session_id": session_id}).scalar() or 0
        row = conn.execute(text("""
            SELECT summary, source_turns FROM session_memories
            WHERE session_id = :session_id AND user_id = :user_id AND space_id = :space_id
        """), {"session_id": session_id, "user_id": user_id, "space_id": space_id}).fetchone()
    existing_summary = row[0] if row else None
    source_turns = row[1] if row else None
    if not should_refresh_session_memory(user_turns, source_turns):
        return

    summary = _summarize(existing_summary, messages[-12:])
    if not summary:
        return
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO session_memories (session_id, user_id, space_id, summary, source_turns)
            VALUES (:session_id, :user_id, :space_id, :summary, :source_turns)
            ON DUPLICATE KEY UPDATE summary = VALUES(summary), source_turns = VALUES(source_turns), updated_at = CURRENT_TIMESTAMP
        """), {
            "session_id": session_id, "user_id": user_id, "space_id": space_id,
            "summary": summary[:4000], "source_turns": user_turns,
        })
        conn.commit()


def delete_session_memory(session_id: str, user_id: int) -> None:
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM session_memories WHERE session_id = :session_id AND user_id = :user_id"), {
            "session_id": session_id, "user_id": user_id,
        })
        conn.commit()


def _summarize(existing_summary: str | None, messages: list[dict]) -> str:
    transcript = "\n".join(
        f"{'用户' if item.get('role') == 'user' else '助手'}: {item.get('content', '')[:500]}"
        for item in messages
    )
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("LLM_API_KEY"), base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"))
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"), max_tokens=400,
            system=("压缩同一个会话的长期上下文。仅保留稳定业务口径、已确认偏好、未完成问题和关键结论；"
                    "不要保留原始数据行、SQL、参数、凭据、手机号或其他敏感字段。只输出摘要。"),
            messages=[{"role": "user", "content": f"已有摘要：{existing_summary or '无'}\n\n最近对话：\n{transcript}"}],
        )
        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                return block.text.strip()
    except Exception:
        pass
    return existing_summary or "该会话已积累分析上下文；请以最近对话和当前任务帧为准。"

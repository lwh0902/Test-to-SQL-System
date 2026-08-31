"""Small durable V2 run/event fact store.

The store deliberately knows nothing about workflow scheduling.  It allocates a
monotonic sequence per run, commits the event, and returns the committed public
fact for the caller to project over SSE.
"""

from __future__ import annotations

import copy
import asyncio
import json
import threading
import time
import uuid
from typing import Any

from sqlalchemy import text

from app.core.database import engine as default_engine


_FORBIDDEN = frozenset({
    "prompt", "system_prompt", "chain_of_thought", "cot", "reasoning", "thinking",
    "password", "api_key", "token", "secret", "credentials", "raw_memory", "raw_rows",
    "rows", "private_memory", "hidden",
})


def _safe_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            if str(key).lower() in _FORBIDDEN:
                continue
            out[str(key)] = _safe_payload(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_safe_payload(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def _event_dict(
    *, run_id: str, seq: int, kind: str, status: str, agent: str | None = None,
    step: str | None = None, artifact_id: str | None = None,
    public_payload: dict | None = None, created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "seq": int(seq),
        "kind": str(kind or "run"),
        "status": str(status or "running"),
        "agent": str(agent or ""),
        "step": str(step or ""),
        "artifact_id": str(artifact_id or ""),
        "public_payload": _safe_payload(public_payload or {}),
        "created_at": created_at or str(int(time.time() * 1000)),
    }


class InMemoryRunStore:
    """Test double with the same scope and sequence semantics as ``RunStore``."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def create_run(self, *, session_id: str, user_id: int, space_id: str, question: str) -> dict:
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        run = {
            "run_id": run_id, "session_id": session_id, "user_id": int(user_id),
            "space_id": space_id, "question": question, "status": "running", "next_seq": 0,
        }
        with self._lock:
            self._runs[run_id] = run
            self._events[run_id] = []
        return copy.deepcopy(run)

    def append_event(self, run_id: str, *, kind: str, status: str, agent: str | None = None,
                     step: str | None = None, artifact_id: str | None = None,
                     public_payload: dict | None = None) -> dict:
        with self._lock:
            run = self._runs[run_id]
            run["next_seq"] += 1
            event = _event_dict(
                run_id=run_id, seq=run["next_seq"], kind=kind, status=status, agent=agent,
                step=step, artifact_id=artifact_id, public_payload=public_payload,
            )
            self._events[run_id].append(event)
        return copy.deepcopy(event)

    def list_events(self, run_id: str, *, user_id: int, after_seq: int = 0) -> list[dict]:
        run = self._runs.get(run_id)
        if not run or int(run["user_id"]) != int(user_id):
            return []
        return [copy.deepcopy(e) for e in self._events[run_id] if e["seq"] > int(after_seq)]

    def get_run(self, run_id: str, *, user_id: int) -> dict | None:
        run = self._runs.get(run_id)
        if not run or int(run["user_id"]) != int(user_id):
            return None
        return copy.deepcopy(run)

    def finish_run(self, run_id: str, *, status: str, terminal_status: str = "") -> None:
        with self._lock:
            run = self._runs[run_id]
            run["status"] = status
            run["terminal_status"] = terminal_status


class RunStore:
    """MySQL implementation used by the production V2 factory."""

    def __init__(self, db_engine: Any = None) -> None:
        self.engine = db_engine or default_engine

    def create_run(self, *, session_id: str, user_id: int, space_id: str, question: str) -> dict:
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        with self.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO analysis_runs (run_id, session_id, user_id, space_id, question, status, next_seq)
                VALUES (:run_id, :session_id, :user_id, :space_id, :question, 'running', 0)
            """), {
                "run_id": run_id, "session_id": session_id, "user_id": int(user_id),
                "space_id": space_id, "question": question,
            })
            conn.commit()
        return {
            "run_id": run_id, "session_id": session_id, "user_id": int(user_id),
            "space_id": space_id, "question": question, "status": "running", "next_seq": 0,
        }

    def append_event(self, run_id: str, *, kind: str, status: str, agent: str | None = None,
                     step: str | None = None, artifact_id: str | None = None,
                     public_payload: dict | None = None) -> dict:
        payload = _safe_payload(public_payload or {})
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT next_seq FROM analysis_runs WHERE run_id=:run_id FOR UPDATE"), {
                "run_id": run_id,
            }).fetchone()
            if not row:
                raise KeyError(f"unknown run_id: {run_id}")
            seq = int(row[0]) + 1
            conn.execute(text("UPDATE analysis_runs SET next_seq=:seq WHERE run_id=:run_id"), {
                "seq": seq, "run_id": run_id,
            })
            conn.execute(text("""
                INSERT INTO run_events (run_id, seq, kind, status, agent, step, artifact_id, public_payload)
                VALUES (:run_id, :seq, :kind, :status, :agent, :step, :artifact_id, :public_payload)
            """), {
                "run_id": run_id, "seq": seq, "kind": str(kind or "run"),
                "status": str(status or "running"), "agent": agent or None, "step": step or None,
                "artifact_id": artifact_id or None, "public_payload": json.dumps(payload, ensure_ascii=False),
            })
            conn.commit()
        return _event_dict(
            run_id=run_id, seq=seq, kind=kind, status=status, agent=agent, step=step,
            artifact_id=artifact_id, public_payload=payload,
        )

    def list_events(self, run_id: str, *, user_id: int, after_seq: int = 0) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT e.seq, e.kind, e.status, e.agent, e.step, e.artifact_id, e.public_payload, e.created_at
                FROM run_events e JOIN analysis_runs r ON r.run_id=e.run_id
                WHERE e.run_id=:run_id AND r.user_id=:user_id AND e.seq>:after_seq
                ORDER BY e.seq
            """), {"run_id": run_id, "user_id": int(user_id), "after_seq": int(after_seq)}).fetchall()
        return [
            _event_dict(
                run_id=run_id, seq=row[0], kind=row[1], status=row[2], agent=row[3], step=row[4],
                artifact_id=row[5], public_payload=(row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")),
                created_at=str(row[7]),
            ) for row in rows
        ]

    def get_run(self, run_id: str, *, user_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT run_id, session_id, user_id, space_id, question, status, terminal_status, next_seq
                FROM analysis_runs WHERE run_id=:run_id AND user_id=:user_id
            """), {"run_id": run_id, "user_id": int(user_id)}).fetchone()
        if not row:
            return None
        return {
            "run_id": row[0], "session_id": row[1], "user_id": row[2], "space_id": row[3],
            "question": row[4], "status": row[5], "terminal_status": row[6] or "", "next_seq": int(row[7]),
        }

    def finish_run(self, run_id: str, *, status: str, terminal_status: str = "") -> None:
        with self.engine.connect() as conn:
            conn.execute(text("""
                UPDATE analysis_runs SET status=:status, terminal_status=:terminal_status, completed_at=NOW()
                WHERE run_id=:run_id
            """), {"run_id": run_id, "status": status, "terminal_status": terminal_status or None})
            conn.commit()


_DEFAULT_STORE: RunStore | None = None


def get_run_store() -> RunStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = RunStore()
    return _DEFAULT_STORE


class RunEventChannel:
    """Commit events then make them available to one active SSE producer."""

    def __init__(self, store: Any, run_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.store = store
        self.run_id = run_id
        self.loop = loop
        self.queue: asyncio.Queue[dict] = asyncio.Queue()

    def emit(self, *, kind: str, status: str, agent: str | None = None,
             step: str | None = None, artifact_id: str | None = None,
             public_payload: dict | None = None) -> dict:
        event = self.store.append_event(
            self.run_id, kind=kind, status=status, agent=agent, step=step,
            artifact_id=artifact_id, public_payload=public_payload,
        )
        # append_event has committed before this callback is scheduled.
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self.loop:
            self.queue.put_nowait(event)
        else:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
        return event

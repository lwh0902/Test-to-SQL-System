"""Database-backed active analysis state for V2 follow-up turns.

The state is scoped by ``session_id × user_id × space_id`` and uses an
optimistic version so concurrent follow-ups cannot silently overwrite each
other. Only the existing scrubbed ActiveAnalysisState payload is stored.
"""

from __future__ import annotations

import copy
import json
import threading
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agents.active_analysis_state import ActiveAnalysisState, serialize_state, slim_state_payload, state_from_dict
from app.agents.semantic_snapshot import SemanticSnapshot, snapshot_asdict
from app.agents.session_query_store import load_current_query, persist_current_query
from app.core.database import engine as default_engine


def _copy_state(state: ActiveAnalysisState) -> ActiveAnalysisState:
    copied = state_from_dict(copy.deepcopy(serialize_state(state)))
    if copied is None:  # pragma: no cover - ActiveAnalysisState always serializes
        raise ValueError("invalid active analysis state")
    return copied


class InMemoryAnalysisStateRepository:
    """Semantics-preserving test implementation of the MySQL repository."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, int, str], ActiveAnalysisState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(session_id: str, user_id: int, space_id: str) -> tuple[str, int, str]:
        return (str(session_id), int(user_id), str(space_id))

    def save(self, state: ActiveAnalysisState, *, expected_version: int | None = None) -> dict[str, Any]:
        if not state or not state.session_id:
            return {"ok": False, "error": "missing_session_id"}
        expected = int(expected_version if expected_version is not None else max(0, int(state.version or 1) - 1))
        key = self._key(state.session_id, state.user_id, state.space_id)
        with self._lock:
            prior = self._items.get(key)
            if prior is None:
                if expected != 0:
                    return {"ok": False, "error": "state_version_conflict"}
            elif int(prior.version) != expected:
                return {"ok": False, "error": "state_version_conflict"}
            saved = _copy_state(state)
            saved.version = expected + 1
            self._items[key] = saved
        state.version = saved.version
        return {"ok": True, "version": saved.version}

    def load(self, session_id: str | None, user_id: int, space_id: str, *, catalog_fingerprint: str | None = None) -> Optional[ActiveAnalysisState]:
        if not session_id:
            return None
        state = self._items.get(self._key(session_id, user_id, space_id))
        if state is None or not state.is_valid():
            return None
        if catalog_fingerprint and state.catalog_fingerprint and state.catalog_fingerprint != catalog_fingerprint:
            return None
        return _copy_state(state)

    def delete(self, session_id: str, user_id: int, space_id: str) -> bool:
        return self._items.pop(self._key(session_id, user_id, space_id), None) is not None

    def exists(self, session_id: str, user_id: int, space_id: str) -> bool:
        return self._key(session_id, user_id, space_id) in self._items


class AnalysisStateRepository:
    """MySQL repository used by the production V2 service."""

    def __init__(self, db_engine: Any = None) -> None:
        self.engine = db_engine or default_engine

    def save(self, state: ActiveAnalysisState, *, expected_version: int | None = None) -> dict[str, Any]:
        if not state or not state.session_id:
            return {"ok": False, "error": "missing_session_id"}
        expected = int(expected_version if expected_version is not None else max(0, int(state.version or 1) - 1))
        payload = slim_state_payload(state)
        params = {
            "session_id": state.session_id,
            "user_id": int(state.user_id),
            "space_id": state.space_id,
            "expected": expected,
            "new_version": expected + 1,
            "catalog_fingerprint": state.catalog_fingerprint or "",
            "catalog_version": state.catalog_version or "",
            "valid_until": float(state.valid_until or 0),
            "payload": json.dumps(payload, ensure_ascii=False),
        }
        snapshot = SemanticSnapshot.from_dict(state.semantic_snapshot if isinstance(state.semantic_snapshot, dict) else None)
        query_id = ""
        with self.engine.connect() as conn:
            if expected == 0:
                try:
                    conn.execute(text("""
                        INSERT INTO active_analysis_states
                          (session_id, user_id, space_id, version, catalog_fingerprint, catalog_version, valid_until, state_payload)
                        VALUES
                          (:session_id, :user_id, :space_id, :new_version, :catalog_fingerprint, :catalog_version, :valid_until, :payload)
                    """), params)
                except IntegrityError:
                    conn.rollback()
                    return {"ok": False, "error": "state_version_conflict"}
            else:
                result = conn.execute(text("""
                    UPDATE active_analysis_states
                    SET version=:new_version, catalog_fingerprint=:catalog_fingerprint,
                        catalog_version=:catalog_version, valid_until=:valid_until, state_payload=:payload
                    WHERE session_id=:session_id AND user_id=:user_id AND space_id=:space_id AND version=:expected
                """), params)
                if result.rowcount != 1:
                    conn.rollback()
                    return {"ok": False, "error": "state_version_conflict"}
            if snapshot is not None:
                query_id = persist_current_query(
                    conn,
                    session_id=state.session_id,
                    user_id=int(state.user_id),
                    space_id=state.space_id,
                    version=expected + 1,
                    snapshot=snapshot,
                    catalog_fingerprint=state.catalog_fingerprint or "",
                    sql_text=state.last_sql or "",
                    chart_type="",
                )
            conn.commit()
        state.version = expected + 1
        return {"ok": True, "version": state.version, "query_id": query_id}

    def load(self, session_id: str | None, user_id: int, space_id: str, *, catalog_fingerprint: str | None = None) -> Optional[ActiveAnalysisState]:
        if not session_id:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT version, catalog_fingerprint, state_payload
                FROM active_analysis_states
                WHERE session_id=:session_id AND user_id=:user_id AND space_id=:space_id
            """), {"session_id": session_id, "user_id": int(user_id), "space_id": space_id}).fetchone()
            current = None
            try:
                current = load_current_query(
                    conn, session_id=session_id, user_id=int(user_id), space_id=space_id
                )
            except Exception:
                current = None
        if not row and current is None:
            return None
        stored_fingerprint = str((row[1] if row else "") or "")
        if catalog_fingerprint and stored_fingerprint and stored_fingerprint != catalog_fingerprint:
            if current is None or (current.get("catalog_fingerprint") and current["catalog_fingerprint"] != catalog_fingerprint):
                return None
        payload = {}
        if row is not None:
            raw_payload = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
        state = state_from_dict(payload) if payload.get("session_id") else ActiveAnalysisState(
            session_id=session_id, user_id=int(user_id), space_id=space_id
        )
        if state is None:
            return None
        if current is not None:
            state.semantic_snapshot = snapshot_asdict(current["snapshot"])
            state.last_sql = current.get("sql_text") or state.last_sql
            state.last_analysis_spec_id = current.get("query_id") or state.last_analysis_spec_id
            if current.get("catalog_fingerprint"):
                state.catalog_fingerprint = current["catalog_fingerprint"]
        if catalog_fingerprint and state.catalog_fingerprint and state.catalog_fingerprint != catalog_fingerprint:
            return None
        if int(state.user_id or 0) != int(user_id) or (state.space_id or "") != (space_id or ""):
            return None
        if not state.is_valid():
            return None
        state.session_id = session_id
        state.user_id = int(user_id)
        state.space_id = space_id
        state.version = int((row[0] if row else current.get("version") if current else 0) or 0)
        return state

    def delete(self, session_id: str, user_id: int, space_id: str) -> bool:
        with self.engine.connect() as conn:
            result = conn.execute(text("""
                DELETE FROM active_analysis_states
                WHERE session_id=:session_id AND user_id=:user_id AND space_id=:space_id
            """), {"session_id": session_id, "user_id": int(user_id), "space_id": space_id})
            conn.commit()
        return result.rowcount == 1

    def exists(self, session_id: str, user_id: int, space_id: str) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT 1 FROM active_analysis_states
                WHERE session_id=:session_id AND user_id=:user_id AND space_id=:space_id
            """), {"session_id": session_id, "user_id": int(user_id), "space_id": space_id}).fetchone()
        return bool(row)


_DEFAULT: AnalysisStateRepository | None = None
_DEFAULT_LOCK = threading.Lock()


def default_repository() -> AnalysisStateRepository:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = AnalysisStateRepository()
        return _DEFAULT


def get_analysis_state_repository() -> AnalysisStateRepository:
    return default_repository()

"""Durable A2A Dispatcher with outbox + lease semantics.

dev_spec / handover:
- Idempotent delivery via idempotency_key
- States: queued → running → completed | failed | retryable
- Lease claim for multi-worker safe retry (locked_by + lease_until)
- Supervisor only invokes agents through this Dispatcher
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.a2a.contracts import A2AMessage
from app.a2a.registry import AgentRegistry, registry
from app.core.database import engine

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "failed"})
RETRYABLE_STATUSES = frozenset({"retryable"})
_TRANSIENT_EXC = (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_load(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8")
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


class Dispatcher:
    def __init__(self, agent_registry: AgentRegistry = registry):
        self.registry = agent_registry

    # ----- persistence -----
    def _save(
        self,
        message: A2AMessage,
        result: Optional[dict] = None,
        *,
        locked_by: Optional[str] = None,
        lease_until: Optional[datetime] = None,
        last_error: Optional[str] = None,
        bump_attempt: bool = False,
        clear_lease: bool = False,
    ) -> None:
        payload = json.dumps(message.payload, ensure_ascii=False)
        artifact_ids = json.dumps(message.artifact_ids, ensure_ascii=False)
        result_payload = json.dumps(result, ensure_ascii=False) if result is not None else None
        with engine.connect() as conn:
            # Prefer full schema with lease columns; fall back if migration not applied.
            try:
                conn.execute(
                    text(
                        """
                        INSERT INTO a2a_messages (
                            id, correlation_id, task_id, session_id, user_id, space_id,
                            source_agent, target_agent, idempotency_key, status,
                            locked_by, lease_until, attempt_count, last_error,
                            payload, artifact_ids, result_payload
                        ) VALUES (
                            :id, :correlation_id, :task_id, :session_id, :user_id, :space_id,
                            :source_agent, :target_agent, :idempotency_key, :status,
                            :locked_by, :lease_until, :attempt_count, :last_error,
                            :payload, :artifact_ids, :result_payload
                        )
                        ON DUPLICATE KEY UPDATE
                            status = VALUES(status),
                            payload = VALUES(payload),
                            artifact_ids = VALUES(artifact_ids),
                            result_payload = COALESCE(VALUES(result_payload), result_payload),
                            locked_by = CASE WHEN :clear_lease THEN NULL ELSE COALESCE(VALUES(locked_by), locked_by) END,
                            lease_until = CASE WHEN :clear_lease THEN NULL ELSE COALESCE(VALUES(lease_until), lease_until) END,
                            last_error = COALESCE(VALUES(last_error), last_error),
                            attempt_count = attempt_count + :bump,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "id": message.message_id,
                        "correlation_id": message.correlation_id,
                        "task_id": message.task_id,
                        "session_id": message.session_id,
                        "user_id": message.user_id,
                        "space_id": message.space_id,
                        "source_agent": message.source_agent,
                        "target_agent": message.target_agent,
                        "idempotency_key": message.idempotency_key,
                        "status": message.status,
                        "locked_by": None if clear_lease else locked_by,
                        "lease_until": None if clear_lease else lease_until,
                        "attempt_count": 1 if bump_attempt else 0,
                        "last_error": last_error,
                        "payload": payload,
                        "artifact_ids": artifact_ids,
                        "result_payload": result_payload,
                        "clear_lease": 1 if clear_lease else 0,
                        "bump": 1 if bump_attempt else 0,
                    },
                )
                conn.commit()
            except Exception:
                # Legacy schema without lease columns
                logger.debug("lease-aware save failed; falling back", exc_info=True)
                row_payload = message.payload
                if result is not None:
                    row_payload = {**message.payload, "_dispatcher_result": result}
                conn.execute(
                    text(
                        """
                        INSERT INTO a2a_messages (
                            id, correlation_id, task_id, session_id, user_id, space_id,
                            source_agent, target_agent, idempotency_key, status, payload, artifact_ids
                        ) VALUES (
                            :id, :correlation_id, :task_id, :session_id, :user_id, :space_id,
                            :source_agent, :target_agent, :idempotency_key, :status, :payload, :artifact_ids
                        )
                        ON DUPLICATE KEY UPDATE
                            status = VALUES(status),
                            payload = VALUES(payload),
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "id": message.message_id,
                        "correlation_id": message.correlation_id,
                        "task_id": message.task_id,
                        "session_id": message.session_id,
                        "user_id": message.user_id,
                        "space_id": message.space_id,
                        "source_agent": message.source_agent,
                        "target_agent": message.target_agent,
                        "idempotency_key": message.idempotency_key,
                        "status": message.status,
                        "payload": json.dumps(row_payload, ensure_ascii=False),
                        "artifact_ids": artifact_ids,
                    },
                )
                conn.commit()

    def _find_by_idempotency(self, idempotency_key: str) -> Optional[dict]:
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT id, status, payload, artifact_ids, target_agent, task_id,
                               session_id, user_id, space_id, source_agent, correlation_id,
                               idempotency_key,
                               COALESCE(result_payload, JSON_EXTRACT(payload, '$._dispatcher_result')) AS result_payload
                        FROM a2a_messages
                        WHERE idempotency_key = :k
                        LIMIT 1
                        """
                    ),
                    {"k": idempotency_key},
                ).fetchone()
            if not row:
                return None
            payload = _json_load(row[2]) or {}
            result = _json_load(row[12]) if len(row) > 12 else None
            if result is None and isinstance(payload, dict):
                result = payload.get("_dispatcher_result")
            return {
                "id": row[0],
                "status": row[1],
                "payload": payload if isinstance(payload, dict) else {},
                "artifact_ids": _json_load(row[3]) or [],
                "target_agent": row[4],
                "task_id": row[5],
                "session_id": row[6],
                "user_id": row[7],
                "space_id": row[8],
                "source_agent": row[9],
                "correlation_id": row[10],
                "idempotency_key": row[11] if len(row) > 11 else idempotency_key,
                "result": result,
            }
        except Exception:
            logger.debug("a2a_messages lookup failed", exc_info=True)
            return None

    def _load_result(self, prior: dict) -> Optional[dict]:
        return prior.get("result") if prior else None

    def message_from_row(self, row: dict) -> A2AMessage:
        payload = row.get("payload") or {}
        if isinstance(payload, dict):
            payload = {k: v for k, v in payload.items() if k != "_dispatcher_result"}
        arts = row.get("artifact_ids") or []
        if isinstance(arts, str):
            arts = json.loads(arts)
        return A2AMessage(
            message_id=row.get("id") or row.get("message_id") or f"msg_retry",
            correlation_id=row.get("correlation_id") or row.get("task_id"),
            task_id=row["task_id"],
            session_id=row["session_id"],
            user_id=int(row["user_id"]),
            space_id=row["space_id"],
            source_agent=row.get("source_agent") or "supervisor",
            target_agent=row["target_agent"],
            idempotency_key=row["idempotency_key"],
            status=row.get("status") or "queued",
            payload=payload if isinstance(payload, dict) else {},
            artifact_ids=list(arts) if isinstance(arts, list) else [],
        )

    def claim_with_lease(
        self,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 30,
    ) -> list[dict]:
        """Atomically claim retryable or expired-lease messages for this worker.

        Lease expiry comparisons use MySQL NOW() so app/DB timezone drift cannot
        leave messages permanently locked.
        """
        claimed: list[dict] = []
        try:
            with engine.connect() as conn:
                # Select candidates — all time math on MySQL server clock
                rows = conn.execute(
                    text(
                        """
                        SELECT id, status, payload, artifact_ids, target_agent, task_id,
                               session_id, user_id, space_id, source_agent, correlation_id,
                               idempotency_key, locked_by, lease_until, attempt_count
                        FROM a2a_messages
                        WHERE (
                            -- fresh / retryable work without active lease
                            (status IN ('retryable', 'queued')
                             AND (locked_by IS NULL OR lease_until IS NULL OR lease_until < NOW()))
                            OR
                            -- reclaim expired lease on in-flight work
                            (status = 'running'
                             AND lease_until IS NOT NULL
                             AND lease_until < NOW())
                        )
                        ORDER BY updated_at ASC
                        LIMIT :lim
                        FOR UPDATE
                        """
                    ),
                    {"lim": limit},
                ).fetchall()

                for r in rows:
                    mid = r[0]
                    res = conn.execute(
                        text(
                            """
                            UPDATE a2a_messages
                            SET locked_by = :wid,
                                lease_until = DATE_ADD(NOW(), INTERVAL :secs SECOND),
                                status = 'running',
                                attempt_count = attempt_count + 1,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                              AND (
                                locked_by IS NULL
                                OR lease_until IS NULL
                                OR lease_until < NOW()
                                OR locked_by = :wid
                              )
                              AND status IN ('retryable', 'queued', 'running')
                            """
                        ),
                        {"wid": worker_id, "secs": int(lease_seconds), "id": mid},
                    )
                    if res.rowcount != 1:
                        continue
                    payload = _json_load(r[2]) or {}
                    arts = _json_load(r[3]) or []
                    claimed.append(
                        {
                            "id": r[0],
                            "status": "running",
                            "payload": payload if isinstance(payload, dict) else {},
                            "artifact_ids": arts if isinstance(arts, list) else [],
                            "target_agent": r[4],
                            "task_id": r[5],
                            "session_id": r[6],
                            "user_id": r[7],
                            "space_id": r[8],
                            "source_agent": r[9],
                            "correlation_id": r[10],
                            "idempotency_key": r[11],
                            "locked_by": worker_id,
                            "lease_seconds": int(lease_seconds),
                        }
                    )
                conn.commit()
        except Exception:
            logger.debug("claim_with_lease failed", exc_info=True)
            # Fallback: old claim_retryable path
            return self._claim_retryable_legacy(limit=limit)
        return claimed

    def _claim_retryable_legacy(self, limit: int = 10) -> list[dict]:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, status, payload, artifact_ids, target_agent, task_id,
                               session_id, user_id, space_id, source_agent, correlation_id,
                               idempotency_key
                        FROM a2a_messages
                        WHERE status = 'retryable'
                        ORDER BY updated_at ASC
                        LIMIT :lim
                        """
                    ),
                    {"lim": limit},
                ).fetchall()
                for r in rows:
                    conn.execute(
                        text(
                            "UPDATE a2a_messages SET status='queued', updated_at=CURRENT_TIMESTAMP WHERE id=:id AND status='retryable'"
                        ),
                        {"id": r[0]},
                    )
                conn.commit()
            out = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "status": "retryable",
                        "payload": _json_load(r[2]) or {},
                        "artifact_ids": _json_load(r[3]) or [],
                        "target_agent": r[4],
                        "task_id": r[5],
                        "session_id": r[6],
                        "user_id": r[7],
                        "space_id": r[8],
                        "source_agent": r[9],
                        "correlation_id": r[10],
                        "idempotency_key": r[11],
                    }
                )
            return out
        except Exception:
            return []

    def claim_retryable(self, limit: int = 10) -> list[dict]:
        """Backward-compatible entry; prefers lease claim with anonymous worker."""
        return self.claim_with_lease(worker_id="legacy-claim", limit=limit, lease_seconds=30)

    def release_lease(
        self,
        message_id: str,
        worker_id: str,
        *,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        try:
            with engine.connect() as conn:
                if success:
                    conn.execute(
                        text(
                            """
                            UPDATE a2a_messages
                            SET locked_by = NULL, lease_until = NULL, last_error = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id AND (locked_by = :wid OR locked_by IS NULL)
                            """
                        ),
                        {"id": message_id, "wid": worker_id},
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE a2a_messages
                            SET locked_by = NULL,
                                lease_until = NULL,
                                status = 'retryable',
                                last_error = :err,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id AND (locked_by = :wid OR locked_by IS NULL)
                            """
                        ),
                        {"id": message_id, "wid": worker_id, "err": (error or "")[:500]},
                    )
                conn.commit()
        except Exception:
            logger.debug("release_lease failed", exc_info=True)

    # ----- delivery -----
    async def deliver(self, message: A2AMessage, force: bool = False) -> dict:
        # 1) Idempotent replay (skip when force redelivery after claim)
        if not force:
            prior = self._find_by_idempotency(message.idempotency_key)
            if prior and prior.get("status") == "completed":
                cached = self._load_result(prior)
                if cached is not None:
                    logger.info(
                        "A2A idempotent hit key=%s status=completed", message.idempotency_key
                    )
                    return cached

        queued = message.model_copy(update={"status": "queued"})
        self._save(queued)

        running = message.model_copy(update={"status": "running"})
        self._save(running, bump_attempt=True)

        try:
            handler = self.registry.get(message.target_agent)
            result = await handler(message)
            completed = message.model_copy(update={"status": "completed"})
            self._save(
                completed,
                result=result if isinstance(result, dict) else {"value": result},
                clear_lease=True,
            )
            return result
        except Exception as exc:
            is_transient = isinstance(exc, _TRANSIENT_EXC)
            status = "retryable" if is_transient else "failed"
            failed = message.model_copy(update={"status": status})
            self._save(failed, last_error=str(exc)[:500], clear_lease=True)
            logger.exception(
                "A2A deliver failed key=%s target=%s status=%s",
                message.idempotency_key,
                message.target_agent,
                status,
            )
            raise

"""Production agent_call adapter: diagnosis_pipeline → A2A Dispatcher → Harness.

Deterministic agents are NOT used here. Tests may inject their own agent_call
or register in-memory handlers on the registry.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from app.a2a.contracts import A2AMessage
from app.a2a.dispatcher import Dispatcher
from app.a2a.registry import registry as default_registry
from app.agents.progress import deliver_with_progress

EVIDENCE_CLASS = "production_dispatcher"
TRANSPORT = "a2a_dispatcher"


def _ensure_harness_registered() -> None:
    """Idempotent register of role harnesses on default registry."""
    try:
        from app.agents.export import ExportHarness
        from app.agents.roles import InsightHarness, QueryHarness, ReportHarness, ReviewHarness

        for h in (
            QueryHarness(),
            InsightHarness(),
            ReportHarness(),
            ReviewHarness(),
            ExportHarness(),
        ):
            try:
                default_registry.get(h.name)
            except ValueError:
                default_registry.register(h.name, h.handle)
    except Exception:
        pass


def build_a2a_message(
    task: dict,
    target: str,
    payload: dict,
    artifact_ids: list,
    *,
    suffix: str = "",
) -> A2AMessage:
    task_id = str(task.get("task_id") or task.get("id") or "diag_task")
    session_id = str(task.get("session_id") or "")
    user_id = int(task.get("user_id") or 0)
    space_id = str(task.get("space_id") or "")
    key = f"{task_id}:{target}{suffix}"
    return A2AMessage(
        correlation_id=task_id,
        source_agent="supervisor",
        target_agent=target,
        idempotency_key=key,
        payload=dict(payload or {}),
        artifact_ids=list(artifact_ids or []),
        task_id=task_id,
        session_id=session_id,
        user_id=user_id,
        space_id=space_id,
    )


async def dispatcher_agent_call(
    task: dict,
    target: str,
    payload: dict,
    artifact_ids: list,
    suffix: str = "",
    *,
    dispatcher: Optional[Dispatcher] = None,
) -> AsyncIterator[dict]:
    """Async generator compatible with diagnosis_pipeline.AgentCall.

    Yields progress events then {"type":"result","value": harness_result}.
    """
    _ensure_harness_registered()
    d = dispatcher or Dispatcher(agent_registry=default_registry)
    msg = build_a2a_message(task, target, payload, artifact_ids, suffix=suffix)

    # Fail closed if target not registered — no legacy Graph fallback
    try:
        d.registry.get(target)
    except ValueError as e:
        yield {
            "type": "result",
            "value": {
                "artifact_id": "",
                "payload": {
                    "error": str(e),
                    "stop_reason": "STOP_ERROR",
                    "unregistered_target": target,
                },
                "error": str(e),
                "stop_reason": "STOP_ERROR",
            },
        }
        return

    async def _run():
        return await d.deliver(msg)

    async for item in deliver_with_progress(_run):
        if item.get("type") == "result":
            val = item.get("value")
            if isinstance(val, dict):
                # stamp transport metadata (non-sensitive)
                payload_out = val.get("payload") if isinstance(val.get("payload"), dict) else {}
                if isinstance(payload_out, dict):
                    payload_out = {
                        **payload_out,
                        "_transport": TRANSPORT,
                        "_evidence_class": EVIDENCE_CLASS,
                    }
                    val = {**val, "payload": payload_out}
            yield {"type": "result", "value": val}
        elif item.get("type") == "error":
            err = item.get("error")
            yield {
                "type": "result",
                "value": {
                    "artifact_id": "",
                    "payload": {
                        "error": str(err),
                        "stop_reason": "STOP_ERROR",
                    },
                    "error": str(err),
                    "stop_reason": "STOP_ERROR",
                },
            }
        else:
            yield item


# alias
production_agent_call = dispatcher_agent_call

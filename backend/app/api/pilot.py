"""Pilot status, flags, and feedback API (Phase 6)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.application.kernel_route import resolve_kernel_route
from app.pilot.feedback import PilotFeedback, get_feedback_store
from app.pilot.flags import apply_rollback, default_flags, is_enabled
from app.pilot.metrics import PilotMetrics, evaluate_pilot_success
from app.pilot.scope import explain_support_boundary, is_in_support_scope
from app.pilot.security_checklist import run_security_checklist

router = APIRouter(prefix="/api/pilot", tags=["pilot"])


class FeedbackIn(BaseModel):
    session_id: str
    trace_id: str
    category: str = "general"
    message: str = Field(..., min_length=1, max_length=2000)
    understood_failure: bool = False
    user_id: int = 0


class RollbackIn(BaseModel):
    snapshot_id: str = "baseline_phase5"


@router.get("/status")
def pilot_status() -> dict[str, Any]:
    flags = default_flags()
    sec = run_security_checklist()
    metrics = PilotMetrics()  # live metrics not wired — honest empty
    success = evaluate_pilot_success(metrics)
    return {
        "status": success.status,
        "status_code": success.status_code,
        "shippable": False,
        "release_status": "not_production",
        "flags": flags.to_dict(),
        "security_passed": sec.passed,
        "kill_switch": flags.kill_switch,
        "analysis_enabled": is_enabled(flags, "analysis_kernel"),
        "kernel_route": resolve_kernel_route(flags),
    }


@router.get("/flags")
def get_flags() -> dict[str, Any]:
    return default_flags().to_dict()


@router.post("/rollback")
def rollback(body: RollbackIn) -> dict[str, Any]:
    flags = apply_rollback(default_flags(), snapshot_id=body.snapshot_id)
    return {
        "ok": True,
        "flags": flags.to_dict(),
        "message": flags.kill_message,
    }


@router.post("/feedback")
def submit_feedback(body: FeedbackIn) -> dict[str, Any]:
    store = get_feedback_store()
    fb = store.submit(
        PilotFeedback(
            user_id=body.user_id,
            session_id=body.session_id,
            trace_id=body.trace_id,
            category=body.category,
            message=body.message,
            understood_failure=body.understood_failure,
        )
    )
    return {"ok": True, "feedback_id": fb.feedback_id, "trace_id": fb.trace_id}


@router.get("/scope")
def scope_check(q: str = "") -> dict[str, Any]:
    return {
        "in_scope": is_in_support_scope(q),
        "message": explain_support_boundary(q),
    }

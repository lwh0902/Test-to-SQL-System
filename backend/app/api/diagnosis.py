import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.rate_limit import limiter
from app.services.authorization_service import require_session_access, require_space_access
from app.services.agent_runtime_store import create_task, list_artifacts
from app.agents.deep_diagnosis import DiagnosisBudget, run_deep_diagnosis

router = APIRouter(prefix="/api/diagnosis", tags=["diagnosis"])


class DiagnosisRequest(BaseModel):
    question: str
    space_id: str
    session_id: str
    # Optional: continue from an existing query result (skip Query agent)
    seed_query: Optional[dict[str, Any]] = None
    # Optional: which exports to produce after approval
    export_formats: Optional[list[str]] = Field(default=None)
    # Optional orchestration budget knobs
    max_rework: Optional[int] = None
    max_requery: Optional[int] = None
    max_agent_calls: Optional[int] = None


@router.post("/stream")
@limiter.limit("10/minute")
async def diagnosis_stream(
    request: Request,
    req: DiagnosisRequest,
    user: dict = Depends(get_current_user),
):
    require_space_access(req.space_id, user["user_id"])
    require_session_access(req.session_id, user["user_id"], req.space_id)
    task = create_task(req.session_id, user["user_id"], req.space_id, req.question)
    task.update(
        {
            "session_id": req.session_id,
            "user_id": user["user_id"],
            "space_id": req.space_id,
            "question": req.question,
        }
    )

    async def stream():
        yield f"event: task_created\ndata: {json.dumps({'task_id': task['id']}, ensure_ascii=False)}\n\n"
        budget = DiagnosisBudget()
        if req.max_rework is not None:
            budget.max_rework = max(0, int(req.max_rework))
        if req.max_requery is not None:
            budget.max_requery = max(0, int(req.max_requery))
        if req.max_agent_calls is not None:
            budget.max_agent_calls = max(1, int(req.max_agent_calls))
        async for item in run_deep_diagnosis(
            task,
            user.get("role", "tester"),
            seed_query=req.seed_query,
            export_formats=req.export_formats,
            budget=budget,
        ):
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False, default=str)}\n\n"
        artifacts = list_artifacts(task["id"], req.session_id, user["user_id"], req.space_id)
        yield f"event: complete\ndata: {json.dumps({'task_id': task['id'], 'artifacts': artifacts}, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{task_id}/artifacts")
def diagnosis_artifacts(
    task_id: str,
    session_id: str,
    space_id: str,
    user: dict = Depends(get_current_user),
):
    require_space_access(space_id, user["user_id"])
    require_session_access(session_id, user["user_id"], space_id)
    return {"artifacts": list_artifacts(task_id, session_id, user["user_id"], space_id)}

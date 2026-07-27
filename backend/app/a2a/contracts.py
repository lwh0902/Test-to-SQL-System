from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator

class A2AMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    correlation_id: str
    task_id: str
    session_id: str
    user_id: int
    space_id: str
    source_agent: str
    target_agent: str
    idempotency_key: str
    schema_version: str = "1.0"
    status: str = "queued"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    artifact_ids: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)

    @field_validator("task_id", "session_id", "space_id", "source_agent", "target_agent", "idempotency_key")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("A2A scope/identity fields must be non-empty")
        return v

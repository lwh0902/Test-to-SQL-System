"""Pilot feedback entry with error Trace binding (Phase 6)."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class PilotFeedback:
    user_id: int
    session_id: str
    trace_id: str
    category: str
    message: str
    understood_failure: bool = False
    created_at: float = field(default_factory=time.time)
    feedback_id: str = field(default_factory=lambda: f"fb_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FeedbackStore:
    """In-memory store for tests / local pilot; replace with DB later."""

    def __init__(self) -> None:
        self._items: list[PilotFeedback] = []

    def submit(self, fb: PilotFeedback) -> PilotFeedback:
        if not fb.trace_id:
            raise ValueError("trace_id required")
        self._items.append(fb)
        return fb

    def list_for_session(self, session_id: str) -> list[PilotFeedback]:
        return [x for x in self._items if x.session_id == session_id]

    def count(self) -> int:
        return len(self._items)

    def understood_failure_rate(self) -> float:
        if not self._items:
            return 0.0
        ok = sum(1 for x in self._items if x.understood_failure)
        return ok / len(self._items)


# process-global default for API wiring
_default_store: Optional[FeedbackStore] = None


def get_feedback_store() -> FeedbackStore:
    global _default_store
    if _default_store is None:
        _default_store = FeedbackStore()
    return _default_store

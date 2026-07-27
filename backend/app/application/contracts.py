"""Canonical turn contracts (Recovery R1a / dev_spec §3.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Preview hard caps (dev_spec §3.2)
DEFAULT_ROWS_PREVIEW = 100
HARD_ROWS_PREVIEW_CAP = 500

KERNEL_LEGACY = "legacy_rollback"
KERNEL_V2 = "analysis_kernel_v2"


def preview_rows(rows: list | None, limit: int = DEFAULT_ROWS_PREVIEW) -> list:
    if not rows:
        return []
    cap = min(max(1, int(limit)), HARD_ROWS_PREVIEW_CAP)
    return list(rows)[:cap]


@dataclass
class TurnRequest:
    question: str
    user_id: int
    user_role: str = "tester"
    space_id: str = "tech_quality"
    session_id: str | None = None
    workspace_id: str = "default"
    selected_metric: str | None = None
    selected_query_type: str | None = None
    request_id: str | None = None

    @classmethod
    def from_chat(cls, req: Any, user: dict) -> "TurnRequest":
        return cls(
            question=getattr(req, "question", "") or "",
            user_id=int(user.get("user_id") or 0),
            user_role=str(user.get("role") or "tester"),
            space_id=getattr(req, "space_id", None) or "tech_quality",
            session_id=getattr(req, "session_id", None),
            workspace_id=getattr(req, "workspace_id", None) or "default",
            selected_metric=getattr(req, "selected_metric", None),
            selected_query_type=getattr(req, "selected_query_type", None),
            request_id=getattr(req, "request_id", None),
        )


@dataclass
class TurnResult:
    response_type: str = "answer"
    message: str = ""
    trace_id: str = ""
    terminal_status: str = ""
    stop_reason: str = ""
    sql: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    rows_count: int = 0
    chart: Any = None
    candidates: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    intent: Any = None
    artifacts: list = field(default_factory=list)
    task_id: str | None = None
    analysis_spec: Any = None
    query_outcome: Any = None
    evidence: Any = None
    active_state_version: int | None = None
    kernel_route: str = KERNEL_LEGACY
    data_map: Any = None
    db_identity: Any = None
    plan: Any = None
    plan_results: Any = None
    answer: str | None = None  # alias of message for ChatResponse
    supervisor_decision: Any = None  # R5.5 safe dict
    task_spec: Any = None  # R5.5 TaskSpec dict
    clarify_slots: list = field(default_factory=list)
    ux_hints: list = field(default_factory=list)
    next_actions: list = field(default_factory=list)  # [{id,label}]

    def __post_init__(self) -> None:
        if self.rows_count is None or self.rows_count == 0:
            if isinstance(self.rows, list) and self.rows:
                self.rows_count = len(self.rows)
        if self.answer is None:
            self.answer = self.message
        if self.kernel_route not in {KERNEL_LEGACY, KERNEL_V2, "unknown"}:
            # allow unknown during early R1; normalize empty
            if not self.kernel_route:
                self.kernel_route = KERNEL_LEGACY

    @staticmethod
    def _public_analysis_spec(spec: Any) -> Any:
        if spec is None:
            return None
        if hasattr(spec, "to_dict") and callable(getattr(spec, "to_dict")):
            try:
                return spec.to_dict()
            except Exception:
                pass
        if isinstance(spec, dict):
            # nested dataclasses may still sit inside __dict__ dumps
            out: dict[str, Any] = {}
            for k, v in spec.items():
                if hasattr(v, "to_dict") and callable(getattr(v, "to_dict")):
                    out[k] = v.to_dict()
                elif isinstance(v, list):
                    out[k] = [
                        (i.to_dict() if hasattr(i, "to_dict") and callable(getattr(i, "to_dict")) else (
                            asdict(i) if hasattr(i, "__dataclass_fields__") else i
                        ))
                        for i in v
                    ]
                elif hasattr(v, "__dataclass_fields__"):
                    out[k] = asdict(v)
                else:
                    out[k] = v
            return out
        if hasattr(spec, "__dataclass_fields__"):
            try:
                return asdict(spec)
            except Exception:
                return spec
        return spec

    def to_public_dict(self, *, rows_preview: int = DEFAULT_ROWS_PREVIEW) -> dict[str, Any]:
        """JSON/SSE complete payload — business fields only."""
        rows_full = self.rows if isinstance(self.rows, list) else []
        count = int(self.rows_count or len(rows_full) or 0)
        out: dict[str, Any] = {
            "type": self.response_type,
            "response_type": self.response_type,
            "trace_id": self.trace_id or "",
            "answer": self.answer if self.answer is not None else self.message,
            "message": self.message or "",
            "sql": self.sql or "",
            "columns": list(self.columns or []),
            "rows": preview_rows(rows_full, rows_preview),
            "rows_count": count,
            "chart": self.chart,
            "candidates": list(self.candidates or []),
            "trace": list(self.trace or []),
            "terminal_status": self.terminal_status or "",
            "stop_reason": self.stop_reason or "",
            "kernel_route": self.kernel_route or KERNEL_LEGACY,
            "artifacts": list(self.artifacts or []),
            "task_id": self.task_id,
            "analysis_spec": self._public_analysis_spec(self.analysis_spec),
            "query_outcome": self.query_outcome,
            "evidence": self.evidence,
            "active_state_version": self.active_state_version,
            "plan": self.plan,
            "plan_results": self.plan_results,
            "supervisor_decision": self.supervisor_decision,
            "task_spec": self.task_spec,
            "clarify_slots": list(self.clarify_slots or []),
            "ux_hints": list(self.ux_hints or []),
            "next_actions": list(self.next_actions or []),
        }
        if self.intent is not None:
            if hasattr(self.intent, "model_dump"):
                out["intent"] = self.intent.model_dump()
            elif isinstance(self.intent, dict):
                out["intent"] = self.intent
            else:
                out["intent"] = self.intent
        if self.data_map is not None:
            out["data_map"] = self.data_map
        if self.db_identity is not None:
            out["db_identity"] = self.db_identity
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

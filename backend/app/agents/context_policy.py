"""Agent private context policy (dev_spec).

Each agent gets an AgentContextPackage:
- full task/session/user/space scope
- scrubbed private working memory (task+agent scoped)
- scrubbed experience memory (agent+space)
- allowed_inputs shaped from A2A payload (bounded; no credentials)
- artifact_ids only (not unlimited upstream chat)

Private memory MUST NOT store raw rows, credentials, CoT, prompts, or another agent's hidden reasoning.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.a2a.contracts import A2AMessage

# Keys never allowed in private memory or non-query durable side state
FORBIDDEN_MEMORY_KEYS = frozenset({
    "rows",
    "raw_rows",
    "password",
    "api_key",
    "secret",
    "token",
    "credentials",
    "credential",
    "chain_of_thought",
    "cot",
    "reasoning",
    "prompt",
    "system_prompt",
    "private_memory",
    "raw_memory",
    "hidden",
})

# Nested dict dropped if it contains these
_NESTED_POISON = frozenset({"rows", "raw_rows", "password", "api_key", "credentials", "chain_of_thought"})

# Per-role how many preview rows may appear in *allowed_inputs* (message-facing), never in memory
_PREVIEW_LIMITS = {
    "query": 0,  # query produces rows as artifact, not as memory
    "insight": 20,
    "report": 15,
    "review": 12,
    "export": 0,
    "supervisor": 0,
}

_MAX_EXPERIENCE_NOTES = 20
_MAX_DIGEST_ITEMS = 12
_MAX_STR = 2000


def scrub_memory(memory: Optional[dict]) -> dict:
    """Deep scrub for private working/experience memory."""
    if not memory or not isinstance(memory, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in memory.items():
        lk = str(k).lower()
        if lk in FORBIDDEN_MEMORY_KEYS:
            continue
        cleaned = _scrub_value(v)
        if cleaned is _DROP:
            continue
        out[str(k)] = cleaned
    return out


class _DropType:
    pass


_DROP = _DropType()


def _scrub_value(v: Any) -> Any:
    if isinstance(v, dict):
        # drop whole nested object if it looks like row-bearing / credential blob
        keys = {str(x).lower() for x in v.keys()}
        if keys & _NESTED_POISON:
            return _DROP
        nested = scrub_memory(v)
        return nested
    if isinstance(v, list):
        items = []
        for item in v[:50]:
            if isinstance(item, dict):
                keys = {str(x).lower() for x in item.keys()}
                if keys & _NESTED_POISON:
                    continue
                # allow compact digests: {text: ...} etc.
                cleaned = scrub_memory(item)
                if cleaned:
                    items.append(cleaned)
            elif isinstance(item, str):
                items.append(item[:_MAX_STR])
            elif isinstance(item, (int, float, bool)) or item is None:
                items.append(item)
            else:
                items.append(str(item)[:200])
        return items
    if isinstance(v, str):
        return v[:_MAX_STR]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return str(v)[:200]


def _strip_forbidden_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_MEMORY_KEYS and str(k).lower() != "rows":
                # rows handled by caller for preview
                if str(k).lower() in ("password", "api_key", "secret", "token", "credentials", "credential"):
                    continue
                if str(k).lower() in (
                    "chain_of_thought",
                    "cot",
                    "reasoning",
                    "prompt",
                    "system_prompt",
                ):
                    continue
            if str(k).lower() in ("password", "api_key", "secret", "token", "credentials", "credential"):
                continue
            out[k] = _strip_forbidden_keys(v)
        return out
    if isinstance(obj, list):
        return [_strip_forbidden_keys(x) for x in obj[:100]]
    return obj


def shape_allowed_inputs(agent_name: str, payload: Optional[dict]) -> dict:
    """Shape A2A payload into role-allowed inputs (bounded, scrubbed secrets)."""
    payload = payload or {}
    if not isinstance(payload, dict):
        return {}
    name = (agent_name or "").lower()
    limit = _PREVIEW_LIMITS.get(name, 10)
    data = _strip_forbidden_keys(deepcopy(payload))

    def _bound_query(q: Any) -> Any:
        if not isinstance(q, dict):
            return q
        q = dict(q)
        # never pass credentials-like
        for bad in list(q.keys()):
            if str(bad).lower() in FORBIDDEN_MEMORY_KEYS and str(bad).lower() != "rows":
                if str(bad).lower() in (
                    "password",
                    "api_key",
                    "secret",
                    "token",
                    "credentials",
                    "chain_of_thought",
                    "prompt",
                    "system_prompt",
                ):
                    q.pop(bad, None)
        rows = q.get("rows")
        if isinstance(rows, list):
            if limit <= 0:
                q.pop("rows", None)
            else:
                q["rows"] = rows[:limit]
        return q

    if "query" in data:
        data["query"] = _bound_query(data.get("query"))
    # Non-query/export: strip accidental rows on report/insight blobs
    if name not in ("query",):
        for key in ("report", "insight", "review"):
            blob = data.get(key)
            if isinstance(blob, dict) and "rows" in blob:
                blob = dict(blob)
                blob.pop("rows", None)
                data[key] = blob
    if name == "export":
        # export may need query rows for csv/xlsx — keep bounded copy under query only
        if isinstance(data.get("query"), dict):
            q = dict(data["query"])
            rows = q.get("rows") if isinstance(q.get("rows"), list) else []
            q["rows"] = rows[:500]
            data["query"] = q
    return data


def compact_digest(items: Iterable[Any], *, limit: int = _MAX_DIGEST_ITEMS) -> list[str]:
    out: list[str] = []
    for it in items:
        if len(out) >= limit:
            break
        if isinstance(it, dict):
            text = it.get("text") or it.get("title") or it.get("content") or ""
            text = str(text).strip()
            if text:
                out.append(text[:300])
        elif isinstance(it, str) and it.strip():
            out.append(it.strip()[:300])
    return out


@dataclass
class AgentContextPackage:
    """Per-agent private execution context (safe to feed model as JSON subset)."""

    agent_name: str
    skill_name: str
    scope: dict[str, Any]
    artifact_ids: list[str] = field(default_factory=list)
    working: dict[str, Any] = field(default_factory=dict)
    experience: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)

    def as_model_bundle(self) -> dict[str, Any]:
        """Bounded bundle injected into LLM user payload — no CoT, no secrets."""
        return {
            "agent": self.agent_name,
            "skill": self.skill_name,
            "scope": {
                "task_id": self.scope.get("task_id"),
                "session_id": self.scope.get("session_id"),
                "user_id": self.scope.get("user_id"),
                "space_id": self.scope.get("space_id"),
            },
            "artifact_ids": list(self.artifact_ids),
            "working_memory": scrub_memory(self.working),
            "experience_memory": scrub_memory(self.experience),
            "allowed_inputs": self.inputs,
            "allowed_tools": list(self.allowed_tools),
            "policies": list(self.policies),
        }


def build_agent_context_package(
    *,
    agent_name: str,
    message: A2AMessage,
    skill_name: str = "",
    working: Optional[dict] = None,
    experience: Optional[dict] = None,
    allowed_tools: Iterable[str] = (),
    policies: Iterable[str] = (),
) -> AgentContextPackage:
    return AgentContextPackage(
        agent_name=agent_name,
        skill_name=skill_name or agent_name,
        scope={
            "task_id": message.task_id,
            "session_id": message.session_id,
            "user_id": message.user_id,
            "space_id": message.space_id,
        },
        artifact_ids=[str(x) for x in (message.artifact_ids or [])],
        working=scrub_memory(working),
        experience=scrub_memory(experience),
        inputs=shape_allowed_inputs(agent_name, message.payload if isinstance(message.payload, dict) else {}),
        allowed_tools=[str(t) for t in allowed_tools],
        policies=[str(p) for p in policies],
    )


def merge_working(prev: Optional[dict], updates: dict) -> dict:
    base = scrub_memory(prev)
    base.update(scrub_memory(updates))
    return scrub_memory(base)


def append_experience_note(prev: Optional[dict], note: str, *, meta: Optional[dict] = None) -> dict:
    exp = scrub_memory(prev)
    notes = list(exp.get("notes") or [])
    text = str(note or "").strip()[:400]
    if text:
        entry: dict[str, Any] = {"text": text}
        if meta:
            entry["meta"] = scrub_memory(meta)
        # de-dup exact text
        if not any(isinstance(n, dict) and n.get("text") == text for n in notes):
            notes.append(entry)
    exp["notes"] = notes[-_MAX_EXPERIENCE_NOTES:]
    return scrub_memory(exp)

"""Safe agent progress events for SSE observability (no raw CoT).

dev_spec: frontend may observe lifecycle/progress without private prompts,
credentials, raw memory, or chain-of-thought.
"""

from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Optional

PROGRESS_FORBIDDEN_KEYS = frozenset({
    "prompt",
    "system_prompt",
    "chain_of_thought",
    "cot",
    "reasoning",
    "thinking",
    "credentials",
    "password",
    "api_key",
    "token",
    "secret",
    "raw_memory",
    "raw_rows",
    "rows",
    "private_memory",
    "hidden",
})

_STEP_SUMMARIES = {
    ("insight", "observe"): "整理查询结果与私有上下文",
    ("insight", "extract"): "提取候选发现",
    ("insight", "verify"): "核对发现是否被证据支持",
    ("insight", "label"): "标记事实与假设",
    ("insight", "commit"): "写入洞察工件",
    ("report", "outline"): "生成报告大纲",
    ("report", "draft"): "起草六段正文",
    ("report", "cite_check"): "检查证据引用",
    ("report", "revise"): "按审查意见修订",
    ("report", "commit"): "写入报告工件",
    ("review", "rules_gate"): "执行硬性规则门禁",
    ("review", "critic"): "质量审查",
    ("review", "commit"): "写入审查结果",
    ("export", "render"): "渲染导出文件",
    ("export", "commit"): "写入导出工件",
    ("query", "run"): "执行受控查询",
    ("query", "commit"): "写入查询结果",
}


def default_step_summary(agent: str, step: str, status: str = "running") -> str:
    base = _STEP_SUMMARIES.get((agent, step)) or f"{agent}.{step}"
    if status == "completed":
        return base.replace("中", "完成") if "中" in base else f"{base}（完成）"
    if status == "failed":
        return f"{base}（失败）"
    return base


def sanitize_progress(data: dict) -> dict:
    """Strip forbidden keys and keep a bounded safe payload."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    allowed_top = {
        "agent",
        "step",
        "status",
        "summary",
        "attempt",
        "procedure",
        "task_id",
        "session_id",
        "user_id",
        "space_id",
        "artifact_id",
        "artifact_type",
        "counts",
        "mode",
    }
    for k, v in data.items():
        lk = str(k).lower()
        if lk in PROGRESS_FORBIDDEN_KEYS:
            continue
        if k not in allowed_top:
            # allow only simple scalar extras under meta-like keys
            if k in ("error_code", "ok"):
                out[k] = v
            continue
        if isinstance(v, str):
            out[k] = v[:300]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, dict):
            # counts only: shallow scalars
            nested = {}
            for nk, nv in list(v.items())[:20]:
                if str(nk).lower() in PROGRESS_FORBIDDEN_KEYS:
                    continue
                if isinstance(nv, (int, float, bool, str)) or nv is None:
                    nested[str(nk)] = nv if not isinstance(nv, str) else nv[:80]
            out[k] = nested
        elif isinstance(v, list):
            out[k] = [str(x)[:80] for x in v[:10]]
        else:
            out[k] = str(v)[:120]
    # ensure summary exists when step known
    if "summary" not in out and out.get("agent") and out.get("step"):
        out["summary"] = default_step_summary(
            str(out["agent"]), str(out["step"]), str(out.get("status") or "running")
        )
    return out


@dataclass
class ProgressBuffer:
    items: list[dict] = field(default_factory=list)
    queue: Optional[asyncio.Queue] = None

    def emit(self, event: str, data: dict) -> None:
        safe = sanitize_progress(data)
        item = {"event": event, "data": safe}
        self.items.append(item)
        if self.queue is not None:
            try:
                self.queue.put_nowait(item)
            except Exception:
                pass


_progress_ctx: contextvars.ContextVar[Optional[ProgressBuffer]] = contextvars.ContextVar(
    "agent_progress_buffer", default=None
)


@contextmanager
def use_progress_buffer(buf: Optional[ProgressBuffer] = None) -> Iterator[ProgressBuffer]:
    buffer = buf or ProgressBuffer()
    token = _progress_ctx.set(buffer)
    try:
        yield buffer
    finally:
        _progress_ctx.reset(token)


def get_progress_buffer() -> Optional[ProgressBuffer]:
    return _progress_ctx.get()


def emit_agent_progress(data: dict) -> None:
    """Emit a sanitized agent_progress event to the current buffer (if any)."""
    buf = get_progress_buffer()
    if buf is None:
        return
    payload = dict(data or {})
    payload.setdefault("status", "running")
    if payload.get("agent") and payload.get("step") and not payload.get("summary"):
        payload["summary"] = default_step_summary(
            str(payload["agent"]), str(payload["step"]), str(payload.get("status") or "running")
        )
    buf.emit("agent_progress", payload)


def emit_artifact_produced(data: dict) -> None:
    buf = get_progress_buffer()
    if buf is None:
        return
    buf.emit("artifact_produced", data)


async def deliver_with_progress(
    coro_factory: Callable[[], Awaitable[Any]] | Awaitable[Any],
    *,
    poll_interval: float = 0.03,
) -> AsyncIterator[dict]:
    """Run an awaitable while draining progress events for SSE.

    Yields:
      {"event": "agent_progress"|"artifact_produced", "data": {...}}
      finally {"type": "result", "value": <return>} or {"type": "error", "error": ...}
    """
    buf = ProgressBuffer(queue=asyncio.Queue())
    token = _progress_ctx.set(buf)

    async def _runner():
        try:
            if callable(coro_factory) and not asyncio.iscoroutine(coro_factory):
                result = await coro_factory()  # type: ignore[misc]
            else:
                result = await coro_factory  # type: ignore[misc]
            return ("ok", result)
        except Exception as exc:
            return ("err", exc)

    task = asyncio.create_task(_runner())
    try:
        while True:
            # drain queue
            while True:
                try:
                    item = buf.queue.get_nowait()  # type: ignore[union-attr]
                    yield item
                except asyncio.QueueEmpty:
                    break
            if task.done():
                break
            await asyncio.sleep(poll_interval)
        # final drain
        while True:
            try:
                item = buf.queue.get_nowait()  # type: ignore[union-attr]
                yield item
            except asyncio.QueueEmpty:
                break
        # also yield any items not queued (sync emit without queue race)
        for item in buf.items:
            # already yielded via queue; skip duplicates by identity is hard —
            # consumers tolerate duplicate progress; skip bulk re-yield.
            pass
        status, val = task.result()
        if status == "ok":
            yield {"type": "result", "value": val}
        else:
            yield {"type": "error", "error": val}
            raise val
    finally:
        _progress_ctx.reset(token)
        if not task.done():
            task.cancel()

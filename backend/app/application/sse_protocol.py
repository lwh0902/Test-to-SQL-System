"""SSE terminal protocol — exactly-once external `complete` (dev_spec §3.3)."""

from __future__ import annotations

import json
from typing import Any


class SseProtocolError(RuntimeError):
    """Raised when complete is duplicated or events follow complete."""


class SseTerminalGuard:
    """Serialize SSE frames and enforce single terminal `complete`."""

    def __init__(self) -> None:
        self._complete_count = 0
        self._closed = False

    @property
    def complete_count(self) -> int:
        return self._complete_count

    @property
    def closed(self) -> bool:
        return self._closed

    def emit(self, event: str, data: dict[str, Any] | None = None) -> str:
        name = (event or "").strip() or "message"
        if self._closed:
            raise SseProtocolError(f"event after complete forbidden: {name}")
        if name == "complete":
            if self._complete_count >= 1:
                raise SseProtocolError("duplicate complete")
            self._complete_count = 1
            self._closed = True
        payload = data if isinstance(data, dict) else {}
        return (
            f"event: {name}\n"
            f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        )

    def try_emit(self, event: str, data: dict[str, Any] | None = None) -> str | None:
        """Like emit but returns None instead of raising (for adapter soft-drop)."""
        try:
            return self.emit(event, data)
        except SseProtocolError:
            return None

"""Trace 存储 - 内存字典实现

短期方案，后续可换 Redis 或数据库。
"""

import threading
from datetime import datetime


class TraceStore:
    def __init__(self):
        self._traces: dict[str, dict] = {}
        self._lock = threading.Lock()

    def save(self, trace_id: str, data: dict) -> None:
        with self._lock:
            self._traces[trace_id] = {
                **data,
                "saved_at": datetime.now().isoformat(),
            }

    def get(self, trace_id: str) -> dict | None:
        with self._lock:
            return self._traces.get(trace_id)

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            traces = sorted(
                self._traces.values(),
                key=lambda t: t.get("saved_at", ""),
                reverse=True,
            )
            return traces[:limit]


# 全局单例
trace_store = TraceStore()

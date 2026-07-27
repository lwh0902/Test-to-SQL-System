"""In-process space → MySQL connection facts (R3).

Passwords stay in memory only — never written to CatalogRepository JSON.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

_lock = threading.Lock()
_REGISTRY: dict[str, dict[str, Any]] = {}


def register_space_connection(space_id: str, conn: dict[str, Any]) -> None:
    key = (space_id or "").strip()
    if not key:
        return
    safe = {
        "host": conn.get("host") or "127.0.0.1",
        "port": int(conn.get("port") or 3306),
        "user": conn.get("user") or "root",
        "password": conn.get("password") or "",
        "database": conn.get("database") or conn.get("db_name") or "",
    }
    with _lock:
        _REGISTRY[key] = safe


def get_space_connection(space_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        c = _REGISTRY.get((space_id or "").strip())
        return dict(c) if c else None


def clear_space_connection(space_id: str) -> None:
    with _lock:
        _REGISTRY.pop((space_id or "").strip(), None)


def clear_all_connections() -> None:
    with _lock:
        _REGISTRY.clear()

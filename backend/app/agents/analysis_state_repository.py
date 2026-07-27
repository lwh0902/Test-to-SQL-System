"""ActiveAnalysisState repository — session-scoped persistence (Recovery R4).

Filesystem-backed by default (same pattern as CatalogRepository).
Keyed by session_id + user_id + space_id. No raw SQL credentials / CoT / full rows.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.agents.active_analysis_state import (
    ActiveAnalysisState,
    serialize_state,
    state_from_dict,
)


def _default_root() -> Path:
    env = os.getenv("DATAPILOT_ANALYSIS_STATE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "eval" / "analysis_state_store"


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (s or "x"))


class AnalysisStateRepository:
    """Persist ActiveAnalysisState for multi-turn follow-up across restart/refresh."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, session_id: str, user_id: int, space_id: str) -> Path:
        name = f"{_safe(session_id)}__u{int(user_id)}__{_safe(space_id)}.json"
        return self.root / name

    def save(self, state: ActiveAnalysisState) -> dict[str, Any]:
        if not state or not state.session_id:
            return {"ok": False, "error": "missing session_id"}
        # strip large previews for disk (keep short)
        payload = serialize_state(state) or {}
        preview = list(payload.get("last_result_preview") or [])
        if len(preview) > 20:
            payload["last_result_preview"] = preview[:20]
        # never store passwords / long free text CoT
        payload.pop("password", None)
        payload["_repo"] = {
            "saved_at": time.time(),
            "session_id": state.session_id,
            "user_id": state.user_id,
            "space_id": state.space_id,
        }
        path = self._path(state.session_id, state.user_id, state.space_id)
        with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(path), "version": state.turn_count}

    def load(
        self,
        session_id: str | None,
        user_id: int,
        space_id: str,
        *,
        catalog_fingerprint: str | None = None,
    ) -> Optional[ActiveAnalysisState]:
        if not session_id:
            return None
        path = self._path(session_id, user_id, space_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        data.pop("_repo", None)
        st = state_from_dict(data)
        if not st or not st.is_valid():
            return None
        # owner check
        if int(st.user_id or 0) != int(user_id) or (st.space_id or "") != (space_id or ""):
            return None
        # catalog version / fingerprint gate
        if catalog_fingerprint:
            stored = getattr(st, "catalog_fingerprint", "") or ""
            if stored and stored != catalog_fingerprint:
                return None
        return st

    def delete(self, session_id: str, user_id: int, space_id: str) -> bool:
        path = self._path(session_id, user_id, space_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def exists(self, session_id: str, user_id: int, space_id: str) -> bool:
        return self._path(session_id, user_id, space_id).is_file()


_DEFAULT: AnalysisStateRepository | None = None
_DEFAULT_LOCK = threading.Lock()


def default_repository() -> AnalysisStateRepository:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = AnalysisStateRepository()
        return _DEFAULT


def get_analysis_state_repository() -> AnalysisStateRepository:
    return default_repository()

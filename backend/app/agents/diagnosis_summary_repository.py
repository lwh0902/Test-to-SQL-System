"""DiagnosisSummary repository — session writeback for R5 follow-up cite."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.agents.diagnosis_summary import (
    DiagnosisSummary,
    bundle_from_dict,
    bundle_to_dict,
)


def _default_root() -> Path:
    env = os.getenv("DATAPILOT_DIAGNOSIS_SUMMARY_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "eval" / "diagnosis_summary_store"


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (s or "x"))


class DiagnosisSummaryRepository:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, session_id: str, user_id: int, space_id: str) -> Path:
        return self.root / f"{_safe(session_id)}__u{int(user_id)}__{_safe(space_id)}.json"

    def save(
        self,
        summary: DiagnosisSummary,
        *,
        user_id: int = 0,
        space_id: str = "",
        extra: dict | None = None,
    ) -> dict[str, Any]:
        if not summary or not summary.session_id:
            return {"ok": False, "error": "missing session_id"}
        payload = bundle_to_dict(summary) or {}
        payload["_repo"] = {
            "saved_at": time.time(),
            "user_id": user_id,
            "space_id": space_id or summary.session_id,
        }
        if extra:
            payload["_extra"] = extra
        path = self._path(summary.session_id, user_id, space_id or "")
        with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(path)}

    def load(self, session_id: str | None, user_id: int, space_id: str) -> Optional[DiagnosisSummary]:
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
        data.pop("_extra", None)
        return bundle_from_dict(data)

    def load_full(self, session_id: str | None, user_id: int, space_id: str) -> Optional[dict]:
        if not session_id:
            return None
        path = self._path(session_id, user_id, space_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def delete(self, session_id: str, user_id: int, space_id: str) -> bool:
        path = self._path(session_id, user_id, space_id)
        if path.is_file():
            path.unlink()
            return True
        return False


_DEFAULT: DiagnosisSummaryRepository | None = None
_LOCK = threading.Lock()


def default_repository() -> DiagnosisSummaryRepository:
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is None:
            _DEFAULT = DiagnosisSummaryRepository()
        return _DEFAULT


def get_diagnosis_summary_repository() -> DiagnosisSummaryRepository:
    return default_repository()

"""Session-scoped storage for queries paused on time confirmation."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class PendingTimeConfirmation:
    original_question: str
    start: str
    end: str
    reason: str
    prompt: str
    semantic_model_version: str = ""
    created_at: str = ""


class PendingQueryRepository:
    def __init__(self, root: Path | str | None = None, *, ttl_seconds: int = 1800):
        self.root = Path(root or os.getenv("DATAPILOT_PENDING_QUERY_DIR") or (
            Path(__file__).resolve().parents[3] / "eval" / "pending_query_store"
        ))
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(1, int(ttl_seconds))

    @staticmethod
    def _safe(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)

    def _path(self, session_id: str, user_id: int, space_id: str) -> Path:
        return self.root / f"{self._safe(session_id)}__u{int(user_id)}__{self._safe(space_id)}.json"

    def save(self, session_id: str, user_id: int, space_id: str, pending: PendingTimeConfirmation) -> None:
        payload = asdict(pending)
        if not payload.get("created_at"):
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        target = self._path(session_id, user_id, space_id)
        fd, raw_path = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=self.root)
        temp = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink()

    def load(self, session_id: str, user_id: int, space_id: str) -> PendingTimeConfirmation | None:
        target = self._path(session_id, user_id, space_id)
        if not target.is_file():
            return None
        try:
            pending = PendingTimeConfirmation(**json.loads(target.read_text(encoding="utf-8")))
            created = datetime.fromisoformat(pending.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
            if age > self.ttl_seconds:
                target.unlink(missing_ok=True)
                return None
            return pending
        except Exception:
            return None

    def delete(self, session_id: str, user_id: int, space_id: str) -> bool:
        target = self._path(session_id, user_id, space_id)
        if not target.exists():
            return False
        target.unlink()
        return True


_default_repository: PendingQueryRepository | None = None


def get_pending_query_repository() -> PendingQueryRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = PendingQueryRepository()
    return _default_repository

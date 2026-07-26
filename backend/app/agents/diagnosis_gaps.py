"""Evidence gap fill tracker — at most once per gap fingerprint (Phase 5)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


def gap_fingerprint(gap: str) -> str:
    g = re.sub(r"\s+", " ", (gap or "").strip().lower())
    return hashlib.sha256(g.encode("utf-8")).hexdigest()[:16]


@dataclass
class GapFillTracker:
    max_per_gap: int = 1
    filled: dict[str, int] = field(default_factory=dict)
    duplicate_fill_count: int = 0
    attempted: list[str] = field(default_factory=list)

    def can_fill(self, gap: str) -> bool:
        fp = gap_fingerprint(gap)
        return self.filled.get(fp, 0) < self.max_per_gap

    def mark_filled(self, gap: str) -> None:
        fp = gap_fingerprint(gap)
        self.filled[fp] = self.filled.get(fp, 0) + 1
        self.attempted.append(gap[:200])

    def try_fill(self, gap: str) -> bool:
        """Return True if this fill is allowed and mark it; else count duplicate."""
        if self.can_fill(gap):
            self.mark_filled(gap)
            return True
        self.duplicate_fill_count += 1
        return False

    def filter_new_gaps(self, gaps: list[str]) -> list[str]:
        out = []
        for g in gaps or []:
            if self.can_fill(g):
                out.append(g)
        return out

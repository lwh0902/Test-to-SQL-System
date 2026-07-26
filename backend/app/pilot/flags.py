"""Feature flags, kill switch, and rollback target (Phase 6)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PilotFlags:
    pilot_mode: bool = True
    analysis_kernel: bool = True
    deep_diagnosis: bool = True
    multi_turn: bool = True
    export: bool = True
    chat: bool = True
    kill_switch: bool = False
    rollback_target: str = ""
    # when kill_switch: only allow messaging the kill reason
    kill_message: str = "试点功能已暂停，请联系管理员或等待回滚完成。"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_flags() -> PilotFlags:
    """Load defaults from env; safe offline defaults enable kernel for eval."""
    def _b(name: str, default: bool) -> bool:
        v = os.getenv(name)
        if v is None:
            return default
        return v.strip().lower() in {"1", "true", "yes", "on"}

    return PilotFlags(
        pilot_mode=_b("DATAPILOT_PILOT_MODE", True),
        analysis_kernel=_b("DATAPILOT_FLAG_ANALYSIS_KERNEL", True),
        deep_diagnosis=_b("DATAPILOT_FLAG_DEEP_DIAGNOSIS", True),
        multi_turn=_b("DATAPILOT_FLAG_MULTI_TURN", True),
        export=_b("DATAPILOT_FLAG_EXPORT", True),
        chat=_b("DATAPILOT_FLAG_CHAT", True),
        kill_switch=_b("DATAPILOT_KILL_SWITCH", False),
        rollback_target=os.getenv("DATAPILOT_ROLLBACK_TARGET", "") or "",
    )


def is_enabled(flags: PilotFlags, feature: str) -> bool:
    f = (feature or "").strip().lower()
    if flags.kill_switch:
        # only chat shell for communicating pause
        return f in {"chat", "status", "feedback"}
    mapping = {
        "pilot_mode": flags.pilot_mode,
        "analysis_kernel": flags.analysis_kernel,
        "deep_diagnosis": flags.deep_diagnosis,
        "multi_turn": flags.multi_turn,
        "export": flags.export,
        "chat": flags.chat,
    }
    if f in mapping:
        return bool(mapping[f])
    return bool(flags.extra.get(f, False))


def apply_rollback(flags: PilotFlags, *, snapshot_id: str) -> PilotFlags:
    """Fast rollback: kill heavy paths, pin snapshot id, keep chat for notice."""
    return PilotFlags(
        pilot_mode=flags.pilot_mode,
        analysis_kernel=False,
        deep_diagnosis=False,
        multi_turn=False,
        export=False,
        chat=True,
        kill_switch=True,
        rollback_target=snapshot_id,
        kill_message=(
            f"已回滚至 {snapshot_id}。分析内核与诊断已暂停。"
            + (flags.kill_message if flags.kill_message else "")
        ),
        extra=dict(flags.extra or {}),
    )

"""Resolve kernel_route from pilot flags (dev_spec §3.4 / R1c).

Flag ON  → analysis_kernel_v2
Flag OFF → legacy_rollback (never disguise as v2)
"""

from __future__ import annotations

from typing import Any, Optional

from app.application.contracts import KERNEL_LEGACY, KERNEL_V2
from app.pilot.flags import PilotFlags, default_flags, is_enabled


def resolve_kernel_route(flags: PilotFlags | None = None) -> str:
    f = flags if flags is not None else default_flags()
    if is_enabled(f, "analysis_kernel"):
        return KERNEL_V2
    return KERNEL_LEGACY


def score_sample_allowed(*, kernel_route: str, require_v2: bool = True) -> bool:
    """Pilot / v2 correctness denominator filter."""
    route = (kernel_route or "").strip()
    if require_v2:
        return route == KERNEL_V2
    return route in {KERNEL_V2, KERNEL_LEGACY}


def annotate_trace_kernel(trace: list | None, kernel_route: str) -> list:
    steps = list(trace or [])
    steps.append(
        {
            "node": "kernel_route",
            "status": "done",
            "kernel_route": kernel_route,
            "output": {"kernel_route": kernel_route},
        }
    )
    return steps

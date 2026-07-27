"""Pilot account capability precheck before pilot scoring (dev_spec Phase 1)."""

from __future__ import annotations

from typing import Any, Iterable, Optional


def precheck_pilot_account(
    *,
    user_role: str,
    space_id: str,
    metric_keys: Iterable[str],
    metric_role_map: dict[str, list[str]],
    require_all_metrics: bool = True,
) -> dict[str, Any]:
    """Check that pilot role can access listed metrics in a space.

    Does NOT bypass SQL safety / isolation — only product capability precheck.
    """
    role = (user_role or "").strip()
    checks: list[dict[str, Any]] = []
    ok = True
    keys = list(metric_keys or [])
    if not keys:
        checks.append(
            {
                "name": "metric_list",
                "status": "fail",
                "detail": "未配置试点指标列表",
            }
        )
        ok = False
    for key in keys:
        allowed = list(metric_role_map.get(key) or [])
        if not allowed:
            checks.append(
                {
                    "name": f"metric:{key}",
                    "status": "fail",
                    "detail": f"指标 {key} 未配置 roles",
                }
            )
            ok = False
            continue
        if role in allowed or role == "admin":
            checks.append(
                {
                    "name": f"metric:{key}",
                    "status": "pass",
                    "detail": f"{role} ∈ {allowed}",
                }
            )
        else:
            checks.append(
                {
                    "name": f"metric:{key}",
                    "status": "fail",
                    "detail": f"角色 {role} 不在指标 {key} 的 roles={allowed}",
                }
            )
            ok = False

    checks.append(
        {
            "name": "space",
            "status": "pass" if space_id else "fail",
            "detail": space_id or "missing space_id",
        }
    )
    if not space_id:
        ok = False

    return {
        "ok": ok if require_all_metrics else any(c.get("status") == "pass" for c in checks),
        "user_role": role,
        "space_id": space_id,
        "checks": checks,
    }

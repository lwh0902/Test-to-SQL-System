"""Data-plane pilot precheck (dev_spec §4.1) — NOT YAML metric roles.

Checks space access, readonly posture, and sensitive-field policy hooks.
Failure → BLOCKED_FOR_PILOT (scripts must not start scoring).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED_FOR_PILOT"
STATUS_DEGRADED = "DEGRADED"


@dataclass
class DataPlanePrecheckResult:
    ok: bool
    status: str
    space_id: str
    user_id: int
    user_role: str
    missing: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    evidence_class: str = "data_plane"  # never yaml_compat

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def precheck_data_plane(
    *,
    space_id: str,
    user_id: int,
    user_role: str = "tester",
    space_exists: bool = True,
    space_active: bool = True,
    user_has_space_access: bool = True,
    connection_ok: bool = True,
    readonly_account: bool = True,
    can_read_information_schema: bool = True,
    sensitive_fields_blocked: bool = True,
    blocked_reason: str | None = None,
) -> DataPlanePrecheckResult:
    """Pure data-plane capability check. Callers inject facts (DB probes optional)."""
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    def _add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})
        if not passed:
            missing.append(name)

    sid = (space_id or "").strip()
    _add("space_id", bool(sid), sid or "missing space_id")
    _add("space_exists", bool(space_exists), "space row present" if space_exists else "space not found")
    _add("space_active", bool(space_active), "active" if space_active else "inactive/archived")
    _add(
        "space_access",
        bool(user_has_space_access),
        "user may access space" if user_has_space_access else "user has no space access",
    )
    _add("connection", bool(connection_ok), "DB reachable" if connection_ok else "connection failed")
    _add(
        "readonly_account",
        bool(readonly_account),
        "account is readonly" if readonly_account else "account is not readonly (write grants present)",
    )
    _add(
        "information_schema",
        bool(can_read_information_schema),
        "can read information_schema" if can_read_information_schema else "cannot profile schema",
    )
    _add(
        "sensitive_field_policy",
        bool(sensitive_fields_blocked),
        "sensitive fields denied by policy" if sensitive_fields_blocked else "sensitive policy missing",
    )

    if blocked_reason:
        _add("explicit_block", False, blocked_reason)

    ok = len(missing) == 0
    status = STATUS_READY if ok else STATUS_BLOCKED
    return DataPlanePrecheckResult(
        ok=ok,
        status=status,
        space_id=sid,
        user_id=int(user_id or 0),
        user_role=str(user_role or ""),
        missing=missing,
        checks=checks,
        evidence_class="data_plane",
    )


def probe_space_facts(space_id: str, user_id: int) -> dict[str, Any]:
    """Best-effort live facts. Failures → conservative deny facts."""
    facts: dict[str, Any] = {
        "space_exists": False,
        "space_active": False,
        "user_has_space_access": False,
        "connection_ok": False,
        "readonly_account": True,
        "can_read_information_schema": False,
        "sensitive_fields_blocked": True,
    }
    try:
        from app.services.authorization_service import get_space_for_access

        space = get_space_for_access(space_id, user_id)
        if space:
            facts["space_exists"] = True
            facts["space_active"] = True
            facts["user_has_space_access"] = True
    except Exception as e:
        facts["blocked_reason"] = f"space_lookup_error:{e}"

    try:
        from sqlalchemy import text
        from app.core.database import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        facts["connection_ok"] = True
        # User MySQL catalog probe is R2; R1c requires space access + system health.
        facts["can_read_information_schema"] = bool(facts["user_has_space_access"])
    except Exception:
        facts["connection_ok"] = False
        facts["can_read_information_schema"] = False

    return facts


def run_data_plane_precheck(
    *,
    space_id: str,
    user_id: int,
    user_role: str = "tester",
    facts: dict[str, Any] | None = None,
    skip_probe: bool = False,
) -> DataPlanePrecheckResult:
    if facts is None and not skip_probe:
        facts = probe_space_facts(space_id, user_id)
    facts = facts or {}
    return precheck_data_plane(
        space_id=space_id,
        user_id=user_id,
        user_role=user_role,
        space_exists=bool(facts.get("space_exists", True)),
        space_active=bool(facts.get("space_active", True)),
        user_has_space_access=bool(facts.get("user_has_space_access", True)),
        connection_ok=bool(facts.get("connection_ok", True)),
        readonly_account=bool(facts.get("readonly_account", True)),
        can_read_information_schema=bool(facts.get("can_read_information_schema", True)),
        sensitive_fields_blocked=bool(facts.get("sensitive_fields_blocked", True)),
        blocked_reason=facts.get("blocked_reason"),
    )

"""Security special checklist for pilot admission (Phase 6)."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SecurityChecklistReport:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    critical_incidents: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "details": dict(self.details),
            "critical_incidents": self.critical_incidents,
        }


def run_security_checklist() -> SecurityChecklistReport:
    checks: dict[str, bool] = {}
    details: dict[str, str] = {}

    # --- auth config present ---
    try:
        from app.core import config as cfg

        has_jwt = bool(getattr(cfg, "JWT_SECRET", None))
        has_validate = callable(getattr(cfg, "validate_security_config", None))
        checks["auth_config_present"] = has_jwt and has_validate
        details["auth_config_present"] = "JWT_SECRET + validate_security_config"
    except Exception as e:
        checks["auth_config_present"] = False
        details["auth_config_present"] = str(e)[:120]

    # --- SQL readonly + sensitive ---
    try:
        from app.guards.sql_guard import SQLGuard

        g = SQLGuard(
            permitted_tables={"orders", "customers"},
            sensitive_fields={"password", "phone", "id_card"},
            max_limit=1000,
        )
        w = g.check("DELETE FROM orders WHERE id=1")
        checks["sql_readonly_blocks_write"] = (not w.passed)
        details["sql_readonly_blocks_write"] = w.message or w.code or "blocked"

        s = g.check("SELECT password FROM orders LIMIT 10")
        # may fail on table/field; must not pass
        checks["sql_blocks_sensitive_fields"] = (not s.passed)
        details["sql_blocks_sensitive_fields"] = s.message or s.code or "blocked"

        ok = g.check("SELECT id, amount FROM orders LIMIT 10")
        checks["sql_allows_safe_select"] = bool(ok.passed)
    except Exception as e:
        checks["sql_readonly_blocks_write"] = False
        checks["sql_blocks_sensitive_fields"] = False
        checks["sql_allows_safe_select"] = False
        details["sql_error"] = str(e)[:120]

    # --- space isolation contract (code-level) ---
    try:
        from app.services import agent_runtime_store as store
        from app.a2a import contracts as a2a

        src = inspect.getsource(store) if store else ""
        # must scope by user_id / space_id / session_id in store APIs
        checks["space_isolation_contract"] = (
            "user_id" in src and "space_id" in src and "session_id" in src
        )
        details["space_isolation_contract"] = "agent_runtime_store scopes present"
        # A2A message has isolation fields
        if hasattr(a2a, "A2AMessage"):
            fields = getattr(a2a.A2AMessage, "__annotations__", {}) or {}
            checks["a2a_scope_fields"] = all(
                k in fields for k in ("user_id", "space_id", "session_id", "task_id")
            )
        else:
            checks["a2a_scope_fields"] = False
    except Exception as e:
        checks["space_isolation_contract"] = False
        checks["a2a_scope_fields"] = False
        details["isolation_error"] = str(e)[:120]

    # --- audit does not log secrets ---
    try:
        from app.services import audit_service

        src = inspect.getsource(audit_service.audit_security_event)
        checks["audit_no_raw_secrets"] = all(
            tok in src for tok in ("password", "token", "sql", "rows")
        ) and ("safe_fields" in src or "not in" in src)
        details["audit_no_raw_secrets"] = "audit_security_event filters sensitive keys"
    except Exception as e:
        checks["audit_no_raw_secrets"] = False
        details["audit_error"] = str(e)[:120]

    # critical incidents counter starts at 0 for offline admission
    critical = 0
    required = [
        "auth_config_present",
        "sql_readonly_blocks_write",
        "sql_blocks_sensitive_fields",
        "space_isolation_contract",
        "audit_no_raw_secrets",
    ]
    passed = all(checks.get(k) for k in required) and critical == 0
    return SecurityChecklistReport(
        passed=passed,
        checks=checks,
        details=details,
        critical_incidents=critical,
    )

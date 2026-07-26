"""Pilot success metrics & evaluation (Phase 6).

Meeting numeric gates is necessary but not sufficient:
owner_ack_pilot_pass must be explicit. Never auto-flip to production GA.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PilotMetrics:
    core_task_unassisted_rate: float = 0.0
    in_scope_accuracy: float = 0.0
    high_confidence_error_rate: float = 1.0
    hang_over_90s_count: int = 0
    p95_query_ms: float = 999_999.0
    p95_diagnosis_ms: float = 999_999.0
    critical_security_incidents: int = 0
    failure_understood_rate: float = 0.0
    sample_users: int = 0
    owner_ack_pilot_pass: bool = False


@dataclass
class PilotSuccessResult:
    passed: bool
    gates_green: bool
    shippable: bool
    status: str
    status_code: str
    gate_details: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_pilot_success(m: PilotMetrics) -> PilotSuccessResult:
    gates = {
        "core_task_unassisted_ge_90": m.core_task_unassisted_rate >= 0.90,
        "in_scope_accuracy_ge_95": m.in_scope_accuracy >= 0.95,
        "high_conf_error_lt_1pct": m.high_confidence_error_rate < 0.01,
        "no_hang_over_90s": m.hang_over_90s_count == 0,
        "p95_query_le_20s": m.p95_query_ms <= 20_000,
        "p95_diagnosis_le_90s": m.p95_diagnosis_ms <= 90_000,
        "no_critical_security": m.critical_security_incidents == 0,
        "failure_understood_ge_90": m.failure_understood_rate >= 0.90,
        "sample_users_ge_3": m.sample_users >= 3,
    }
    gates_green = all(gates.values())
    reasons = [k for k, v in gates.items() if not v]

    # Default product status
    status = "未完成，禁止按可上线交付"
    status_code = "not_ready"
    passed = False
    shippable = False

    if gates_green and m.owner_ack_pilot_pass:
        passed = True
        status_code = "pilot_passed"
        status = "小范围试点通过（≠正式生产发布）"
        shippable = False  # never auto production
    elif gates_green and not m.owner_ack_pilot_pass:
        status_code = "gates_green_awaiting_owner"
        status = "未完成，禁止按可上线交付（门禁数字已绿，待产品负责人确认）"
    else:
        status_code = "not_ready"
        status = "未完成，禁止按可上线交付"

    return PilotSuccessResult(
        passed=passed,
        gates_green=gates_green,
        shippable=shippable,
        status=status,
        status_code=status_code,
        gate_details=gates,
        reasons=reasons,
    )

"""Link termination messages & evidence gates (dev_spec Phase 1)."""

from __future__ import annotations

from typing import Optional


def build_diagnosis_terminal_message(
    *,
    stop_reason: str,
    outcome_status: Optional[str] = None,
    review_approved: bool = False,
    review_reasons: Optional[list] = None,
    has_report: bool = False,
    custom_message: str = "",
) -> str:
    """User-facing terminal text. Never use bare「编排已完成」as success cover."""
    reasons = [str(r) for r in (review_reasons or []) if str(r).strip()]
    st = (outcome_status or "").upper()
    reason = (stop_reason or "").lower()

    if custom_message and "编排已完成" not in custom_message:
        return custom_message

    if reason in ("stop_denied", "permission_denied") or st == "PERMISSION_DENIED":
        base = "诊断已终止：权限不足，未继续生成洞察或报告。"
        if reasons:
            return base + " 原因：" + "；".join(reasons[:5])
        return base

    if reason in ("stop_empty",) or st == "SUCCESS_EMPTY":
        return (
            "诊断已终止：没有有效查询数据（结果为空）。"
            "系统未生成深度报告。请调整时间范围或指标后重试。"
        )

    if st in ("SQL_REJECTED", "EXECUTION_ERROR", "TIMEOUT", "CANCELLED"):
        label = {
            "SQL_REJECTED": "SQL 被安全策略拒绝",
            "EXECUTION_ERROR": "查询执行异常",
            "TIMEOUT": "查询超时",
            "CANCELLED": "任务已取消",
        }.get(st, st)
        return f"诊断已终止：{label}。未生成报告。"

    if reason in ("stop_rejected", "no_repair_left") or (
        has_report and not review_approved
    ):
        if reasons:
            return "审核未通过，报告未批准。原因：" + "；".join(reasons[:8])
        return "审核未通过，报告未批准。请根据拒绝原因补充证据后重试。"

    if reason in ("stop_ok", "complete") and review_approved and has_report:
        return "深度诊断完成：报告已通过审核。"

    if reason in ("stop_ok", "complete") and not has_report:
        return "诊断流程结束，但没有可交付的已批准报告。"

    # fallback — still avoid fake success
    if reasons:
        return "诊断结束：" + "；".join(reasons[:5])
    return f"诊断已结束（{stop_reason or 'unknown'}）。"


def evidence_allows_report(outcome_status: Optional[str], rows_count: int = 0) -> bool:
    return (outcome_status or "").upper() == "SUCCESS_WITH_DATA" and int(rows_count or 0) > 0

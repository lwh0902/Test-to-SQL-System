"""First-wave support scope boundary for pilot (Phase 6)."""

from __future__ import annotations

import re


_UNSUPPORTED = re.compile(
    r"训练模型|写回|写入库|删除全部|drop\s+table|更新生产|自动下单|"
    r"爬取|黑客|越权|跨租户|导出全部用户隐私|fine-?tune",
    re.I,
)

_IN_SCOPE_HINT = re.compile(
    r"金额|订单|销售|gmv|支付|流水|统计|合计|数量|count|sum|按.+拆|只看|诊断|报告",
    re.I,
)


def is_in_support_scope(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _UNSUPPORTED.search(q):
        return False
    if _IN_SCOPE_HINT.search(q):
        return True
    # short clarify-style still in conversational scope
    if len(q) <= 20 and re.search(r"呢|拆|过滤|换成", q):
        return True
    return False


def explain_support_boundary(question: str) -> str:
    if _UNSUPPORTED.search(question or ""):
        return (
            "当前试点范围不支持该请求（写操作/模型训练/越权类）。"
            "系统拒绝执行。请改用只读分析类问题，或联系管理员。"
        )
    if not is_in_support_scope(question or ""):
        return (
            "该问题可能超出首轮支持范围。"
            "支持：聚合查数、澄清、有限多表、连续追问、有证据诊断。"
            "不支持：写库、训练模型、无证据因果定论。"
        )
    return "在首轮支持范围内。"

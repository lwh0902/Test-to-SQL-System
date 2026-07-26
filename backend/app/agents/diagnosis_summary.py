"""Diagnosis report summary for session writeback & follow-up (Phase 5)."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class DiagnosisSummary:
    one_liner: str = ""
    next_steps: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    approved: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    report_ref: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    query_sql: str = ""
    rows_count: int = 0
    session_id: str = ""
    task_id: str = ""
    created_at: float = field(default_factory=time.time)
    summary_id: str = field(default_factory=lambda: f"ds_{uuid.uuid4().hex[:12]}")

    def answer_about(self, question: str) -> str:
        q = (question or "").strip()
        if re.search(r"一句话|结论|总结|summary|one.?line", q, re.I):
            return self.one_liner or "暂无已批准结论。"
        if re.search(r"下一步|接下来|还查|next", q, re.I):
            if self.next_steps:
                return "下一步建议：" + "；".join(self.next_steps[:5])
            return "暂无下一步建议；可对已批准报告中的假设做补查。"
        if re.search(r"假设|风险", q):
            return "；".join(self.hypotheses[:5]) or "未记录待验证假设。"
        if re.search(r"发现|finding", q, re.I):
            return "；".join(self.key_findings[:5]) or self.one_liner
        # default cite one-liner + next
        parts = []
        if self.one_liner:
            parts.append(self.one_liner)
        if self.next_steps:
            parts.append("下一步：" + self.next_steps[0])
        return " ".join(parts) or "暂无诊断摘要。"


def _section_map(report: dict | None) -> dict[str, dict]:
    out = {}
    for s in (report or {}).get("sections") or []:
        if isinstance(s, dict) and s.get("title"):
            out[str(s["title"])] = s
    return out


def build_diagnosis_summary(
    *,
    report: dict | None,
    review: dict | None = None,
    query: dict | None = None,
    evidence_ids: list[str] | None = None,
    session_id: str = "",
    task_id: str = "",
    report_artifact_id: str = "",
) -> DiagnosisSummary:
    review = review or {}
    query = query or {}
    sections = _section_map(report)
    approved = bool(review.get("approved"))
    reasons = [str(r) for r in (review.get("reasons") or []) if str(r).strip()]

    findings_sec = sections.get("关键发现") or {}
    advice_sec = sections.get("业务影响与建议") or {}
    hypo_sec = sections.get("待验证假设与风险") or {}

    key_findings = []
    for c in findings_sec.get("claims") or []:
        if isinstance(c, dict) and c.get("text"):
            key_findings.append(str(c["text"])[:200])
    content = str(findings_sec.get("content") or "").strip()
    if content and not key_findings:
        # split lines
        for line in re.split(r"[\n；;。]", content):
            line = line.strip()
            if line:
                key_findings.append(line[:200])
            if len(key_findings) >= 3:
                break

    hypotheses = []
    for line in re.split(r"[\n；;]", str(hypo_sec.get("content") or "")):
        line = line.strip()
        if line:
            hypotheses.append(line[:200])

    next_steps = []
    advice = str(advice_sec.get("content") or "").strip()
    if advice:
        for line in re.split(r"[\n；;]", advice):
            line = line.strip()
            if line:
                next_steps.append(line[:160])
            if len(next_steps) >= 3:
                break
    if not next_steps and hypotheses:
        next_steps.append("验证：" + hypotheses[0][:80])
    if not next_steps:
        next_steps.append("按关键维度补查后复审报告")

    if approved and key_findings:
        one = key_findings[0]
        if len(one) > 80:
            one = one[:77] + "…"
        one_liner = f"一句话结论：{one}"
    elif approved:
        one_liner = "一句话结论：诊断报告已批准，详见关键发现章节。"
    elif reasons:
        one_liner = "报告未批准：" + "；".join(reasons[:3])
    else:
        one_liner = "诊断未形成已批准结论。"

    eids = list(evidence_ids or [])
    for s in (report or {}).get("sections") or []:
        if isinstance(s, dict):
            for e in s.get("evidence_ids") or []:
                if str(e) not in eids:
                    eids.append(str(e))

    return DiagnosisSummary(
        one_liner=one_liner,
        next_steps=next_steps[:5],
        key_findings=key_findings[:8],
        hypotheses=hypotheses[:8],
        approved=approved,
        reject_reasons=reasons[:8],
        report_ref=report_artifact_id or (f"report:{(task_id or 'local')}"),
        evidence_ids=eids[:20],
        query_sql=str(query.get("sql") or "")[:500],
        rows_count=int(query.get("rows_count") or len(query.get("rows") or []) or 0),
        session_id=session_id,
        task_id=task_id,
    )


def bundle_to_dict(summary: Optional[DiagnosisSummary]) -> Optional[dict[str, Any]]:
    if summary is None:
        return None
    return asdict(summary)


def bundle_from_dict(data: dict[str, Any] | None) -> Optional[DiagnosisSummary]:
    if not data:
        return None
    known = {f.name for f in DiagnosisSummary.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in data.items() if k in known}
    return DiagnosisSummary(**kwargs)

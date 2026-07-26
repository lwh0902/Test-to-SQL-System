"""Strict admission evaluation (Recovery R0+).

File existence alone must never yield admission_pass=True.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.pilot.admission import collect_phase_evidence


@dataclass
class AdmissionDecision:
    admission_pass: bool
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_REJECT_EVIDENCE_CLASS = frozenset(
    {"oracle", "offline_fixture", "component_only", "obsolete_for_release"}
)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def evaluate_admission(repo_root: Path | str) -> AdmissionDecision:
    root = Path(repo_root)
    reasons: list[str] = []
    details: dict[str, Any] = {}

    # 1) file-exists collector is never sufficient
    file_ev = collect_phase_evidence(root)
    details["file_exists_all_phases"] = bool(file_ev.get("all_phases"))
    if file_ev.get("all_phases"):
        reasons.append("file_exists_insufficient")

    # 2) require blackbox baseline report with proper evidence_class
    reports = root / "eval" / "reports"
    r0 = _load_json(reports / "recovery_r0_blackbox.json")
    details["r0_report_present"] = r0 is not None
    if not r0:
        reasons.append("missing_recovery_r0_blackbox_report")
    else:
        ec = str(r0.get("evidence_class") or "")
        details["r0_evidence_class"] = ec
        if ec in _REJECT_EVIDENCE_CLASS or ec != "blackbox_baseline":
            reasons.append(f"reject_evidence_class:{ec or 'missing'}")
        # must include nails + rate fields
        if "full_correct_rate" not in r0 and "desired_pass_rate" not in r0:
            reasons.append("r0_missing_rate_fields")
        if not r0.get("trace_ids") and not r0.get("turns"):
            reasons.append("r0_missing_trace_or_turns")
        # commit sha preferred
        if not r0.get("commit_sha"):
            reasons.append("r0_missing_commit_sha")

    # 3) reject known component_only phase reports as product evidence
    for name in (
        "phase3_analysis.json",
        "phase4_followup.json",
        "phase5_diagnosis.json",
        "phase6_pilot.json",
    ):
        doc = _load_json(reports / name)
        if not doc:
            continue
        ec = str(doc.get("evidence_class") or "component_only")
        if ec in _REJECT_EVIDENCE_CLASS or doc.get("shippable") is True:
            # component reports cannot alone admit
            details.setdefault("rejected_component_reports", []).append(name)
    if details.get("rejected_component_reports"):
        reasons.append("component_only_reports_present_not_product_evidence")

    # 4) multi-day camouflage
    p6 = _load_json(reports / "phase6_pilot.json")
    if p6:
        multi = p6.get("multi_day") or {}
        days = multi.get("day_results") or []
        if len(days) >= 3:
            # if no distinct day stamps, reject
            stamps = [d.get("day") for d in days]
            if stamps == [1, 2, 3] and not multi.get("natural_days"):
                reasons.append("multi_day_not_natural_days")
                details["multi_day_note"] = "loop×3 without natural_days flag"

    # Admission pass only if no blocking reasons and r0 baseline exists as blackbox
    # R0 itself does not grant pilot admission — only establishes baseline.
    # Full admission_pass requires later recovery phases; for now always False
    # unless explicitly all product gates (not implemented yet).
    blocking = [
        r
        for r in reasons
        if r
        not in {
            # informational
        }
    ]
    # Product pilot admission never true at R0
    admission_pass = False
    if not r0:
        pass
    else:
        # still false: R0 baseline ≠ pilot admission
        reasons.append("r0_baseline_not_pilot_admission")
        blocking.append("r0_baseline_not_pilot_admission")

    # Ensure file_exists never flips true
    if file_ev.get("all_phases") and admission_pass:
        admission_pass = False
        reasons.append("file_exists_insufficient")

    return AdmissionDecision(
        admission_pass=admission_pass,
        reasons=sorted(set(blocking or reasons)),
        details=details,
    )

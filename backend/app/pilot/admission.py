"""Pilot admission: Phase 0–5 evidence + operational gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _exists(*paths: Path) -> bool:
    return all(p.is_file() for p in paths)


def collect_phase_evidence(repo_root: Path | str) -> dict[str, Any]:
    root = Path(repo_root)
    reports = root / "eval" / "reports"
    tests = root / "backend" / "tests"
    agents = root / "backend" / "app" / "agents"

    phase0 = _exists(
        tests / "test_phase0_eval_baseline.py",
        reports / "phase0_offline_fixture.md",
    ) or _exists(tests / "test_phase0_eval_baseline.py", reports / "phase0_offline_oracle.md")

    phase1 = _exists(tests / "test_phase1_link_reliability.py", agents / "query_outcome.py", agents / "link_policy.py")
    phase2 = _exists(
        tests / "test_phase2_semantic_catalog.py",
        agents / "semantic_catalog.py",
        reports / "phase2_catalog.md",
    )
    phase3 = _exists(
        tests / "test_phase3_analysis_pipeline.py",
        agents / "analysis_spec.py",
        reports / "phase3_analysis.md",
    )
    phase4 = _exists(
        tests / "test_phase4_followup_state.py",
        agents / "active_analysis_state.py",
        reports / "phase4_followup.md",
    )
    phase5 = _exists(
        tests / "test_phase5_evidence_diagnosis.py",
        agents / "diagnosis_admission.py",
        reports / "phase5_diagnosis.md",
    )

    return {
        "phase0": phase0,
        "phase1": phase1,
        "phase2": phase2,
        "phase3": phase3,
        "phase4": phase4,
        "phase5": phase5,
        "all_phases": all([phase0, phase1, phase2, phase3, phase4, phase5]),
        "report_dir": str(reports),
    }

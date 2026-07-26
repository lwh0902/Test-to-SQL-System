from __future__ import annotations

from pathlib import Path


DEV_SPEC = Path(__file__).resolve().parents[3] / "dev_spec.md"


def test_devspec_requires_unseen_mysql_gate_before_pilot():
    text = DEV_SPEC.read_text(encoding="utf-8")
    assert "Recovery Phase 3.5" in text
    assert "run_unseen_mysql_semantic_gate.py" in text
    assert "wrong-table successful answers: 0" in text
    assert "R3.5" in text[text.index("Recovery Phase 6") : text.index("## 6.")]


def test_devspec_contains_profiler_privacy_and_anti_overfit_contracts():
    text = DEV_SPEC.read_text(encoding="utf-8")
    required = (
        "at most 100 dispersed masked sample rows per table",
        "raw sample rows persisted: 0",
        "DATAPILOT_UNSEEN_SEED",
        "fixture-specific production branches: 0",
        "per-schema-family full semantic correctness >= 95%",
        "Ground Truth value match >= 98%",
        "changed-follow-up wrong reuse: 0",
    )
    missing = [item for item in required if item not in text]
    assert not missing, f"DevSpec missing semantic gate clauses: {missing}"


def test_devspec_suspends_downstream_pilot_evidence_until_r35_green():
    text = DEV_SPEC.read_text(encoding="utf-8")
    assert "R4–R5.5 pilot-readiness evidence is suspended" in text
    assert "soft_ok is not semantic-correctness evidence" in text
    assert "intent accuracy is not semantic-correctness evidence" in text


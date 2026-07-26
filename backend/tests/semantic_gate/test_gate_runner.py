from .contracts import SemanticScore
from .gate_report import build_gate_report


def _score(case_id: str, *, ok: bool = True, hard: list[str] | None = None):
    return SemanticScore(
        case_id=case_id,
        family="family_a",
        full_correct=ok,
        dimensions={"subject_table": ok, "values": ok},
        hard_failures=list(hard or []),
    )


def test_wrong_table_cannot_be_averaged_away():
    scores = [_score(f"pass_{i}") for i in range(99)]
    scores.append(_score("wrong", ok=False, hard=["wrong_subject_table"]))
    report = build_gate_report(scores)
    assert report["status"] == "NOT_PILOT_READY"
    assert report["hard_failures"]["wrong_subject_table"] == 1


def test_each_family_must_reach_threshold():
    scores = [_score(f"a_{i}") for i in range(20)]
    scores.append(
        SemanticScore(
            case_id="b_bad",
            family="family_b",
            full_correct=False,
            dimensions={"subject_table": False},
        )
    )
    report = build_gate_report(scores)
    assert report["families"]["family_a"]["full_correct_rate"] == 1.0
    assert report["families"]["family_b"]["full_correct_rate"] == 0.0
    assert report["status"] == "NOT_PILOT_READY"


def test_skip_xfail_dirty_tree_and_infra_are_not_success():
    score = [_score("ok")]
    assert build_gate_report(score, skipped=1)["status"] == "NOT_PILOT_READY"
    assert build_gate_report(score, xfailed=1)["status"] == "NOT_PILOT_READY"
    assert build_gate_report(score, working_tree_clean=False)["status"] == "NOT_PILOT_READY"
    assert build_gate_report(score, infrastructure_error="mysql down")["status"] == "INFRA_BLOCKED"


def test_all_green_report_can_admit_internal_pilot():
    report = build_gate_report([_score(f"ok_{i}") for i in range(20)])
    assert report["status"] == "READY_FOR_INTERNAL_PILOT"


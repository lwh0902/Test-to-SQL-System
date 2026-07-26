from .assertions import score_semantics
from .contracts import (
    FilterExpectation,
    MeasureExpectation,
    SemanticExpectation,
    SemanticObservation,
)


def test_wrong_table_success_is_hard_failure():
    expected = SemanticExpectation(
        case_id="wrong_table",
        family="contract",
        question="count alpha_orders",
        subject_table="alpha_orders",
        sql_tables=frozenset({"alpha_orders"}),
    )
    observed = SemanticObservation(
        behavior="answer",
        terminal_status="SUCCESS_WITH_DATA",
        sql="SELECT COUNT(*) FROM alpha_items",
        spec={"subject": "alpha_items", "required_tables": ["alpha_items"]},
    )
    score = score_semantics(expected, observed)
    assert not score.full_correct
    assert "wrong_subject_table" in score.hard_failures
    assert "wrong_sql_tables" in score.hard_failures


def test_missing_filter_and_second_measure_are_hard_failures():
    expected = SemanticExpectation(
        case_id="compound",
        family="contract",
        question="total and failed count",
        subject_table="jobs",
        measures=(
            MeasureExpectation("count", "id", "jobs"),
            MeasureExpectation("count", "error_id", "jobs"),
        ),
        filters=(FilterExpectation("status", "failed", table="jobs"),),
    )
    observed = SemanticObservation(
        behavior="answer",
        spec={
            "subject": "jobs",
            "measures": [{"aggregation": "count", "source_field": "id", "table": "jobs"}],
            "filters": [],
        },
    )
    score = score_semantics(expected, observed)
    assert not score.full_correct
    assert "missing_or_wrong_measure" in score.hard_failures
    assert "missing_or_wrong_filter" in score.hard_failures


def test_changed_followup_cannot_reuse_previous_result():
    expected = SemanticExpectation(
        case_id="reuse",
        family="contract",
        question="其中失败多少",
        behavior="answer",
        expect_reuse=False,
    )
    observed = SemanticObservation(behavior="answer", reused=True)
    score = score_semantics(expected, observed)
    assert not score.full_correct
    assert "wrong_reuse_decision" in score.hard_failures


def test_clarification_requires_expected_slot():
    expected = SemanticExpectation(
        case_id="ambiguous",
        family="contract",
        question="收入多少",
        behavior="clarify",
        clarify_slots=("measure",),
    )
    wrong = score_semantics(expected, SemanticObservation(behavior="clarify", clarify_slots=["time_range"]))
    right = score_semantics(expected, SemanticObservation(behavior="clarify", clarify_slots=["measure"]))
    assert not wrong.full_correct
    assert right.full_correct


def test_sensitive_raw_value_in_persisted_payload_is_failure():
    expected = SemanticExpectation(
        case_id="privacy",
        family="contract",
        question="profile users",
        behavior="answer",
        forbidden_persisted_values=("13800138000",),
    )
    observed = SemanticObservation(behavior="answer", persisted_payload='{"sample":"13800138000"}')
    score = score_semantics(expected, observed)
    assert not score.full_correct
    assert "sensitive_raw_value_persisted" in score.hard_failures

from __future__ import annotations

from app.agents.active_analysis_state import ActiveAnalysisState
from app.agents.analysis_pipeline import plan_question
from app.agents.profiler import profile_from_ddl
from app.agents.spec_patch import looks_like_followup, patch_or_build

from .case_matrix import DEFAULT_SEEDS
from .schema_factory import build_schema_suites


def _state_for_suite(suite):
    catalog = profile_from_ddl(suite.ddl, database_id=suite.family, seed_sql=suite.seed_sql)
    first = suite.followup_chains[0][0]
    plan = plan_question(first.question, catalog)
    assert plan.action == "query" and plan.spec, f"base query must be plannable: {plan}"
    state = ActiveAnalysisState.from_spec(
        plan.spec,
        session_id="semantic-gate",
        space_id=suite.family,
        result_preview=[{"value": 120}],
        sql=f"SELECT COUNT(*) FROM `{suite.physical('fact')}`",
        answer_text="120",
    )
    return catalog, state


def test_real_anaphoric_followup_phrases_are_recognized():
    assert looks_like_followup("那按商品分类看一下订单量前几名？")
    assert looks_like_followup("其中失败的有多少？失败率大概多少？")
    assert looks_like_followup("那失败率呢？")


def test_changed_followup_produces_semantic_patch_not_reuse():
    suite = next(s for s in build_schema_suites(DEFAULT_SEEDS[0]) if s.family == "support")
    catalog, state = _state_for_suite(suite)
    result = patch_or_build("其中失败的有多少？", catalog, state)
    assert result.action == "patch_spec"
    assert result.spec is not None
    assert any(f.value == "failed" for f in result.spec.filters)
    assert result.patch_ops


def test_group_topn_followup_changes_dimension_order_and_limit():
    suite = next(s for s in build_schema_suites(DEFAULT_SEEDS[0]) if s.family == "commerce")
    catalog, state = _state_for_suite(suite)
    question = f"那按{suite.physical('group')}看前3名？"
    result = patch_or_build(question, catalog, state)
    assert result.action == "patch_spec"
    assert suite.physical("group") in (result.spec.dimensions if result.spec else [])
    assert result.spec and result.spec.limit == 3
    assert result.spec.ordering


def test_multi_measure_followup_is_complete_or_explicitly_clarified():
    suite = next(s for s in build_schema_suites(DEFAULT_SEEDS[0]) if s.family == "support")
    catalog, state = _state_for_suite(suite)
    result = patch_or_build("总数和失败数都给我", catalog, state)
    if result.action == "patch_spec":
        assert result.spec and len(result.spec.measures) >= 2
    else:
        assert result.action == "clarify"
        assert any(slot in result.clarify_slots for slot in ("multi_measure", "measure"))

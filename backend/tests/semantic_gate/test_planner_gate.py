from __future__ import annotations

from dataclasses import replace

import pytest

from app.agents.analysis_pipeline import compile_and_guard, plan_question
from app.agents.profiler import profile_from_ddl

from .assertions import score_semantics
from .case_matrix import gate_seeds
from .contracts import SemanticObservation
from .schema_factory import SchemaSuite, build_schema_suites


GATE_SEEDS = gate_seeds()


def _observe(suite: SchemaSuite, case):
    # Planner layer scores behavior and AnalysisSpec/SQL shape. Numeric Ground
    # Truth is enforced by executor/API layers.
    case = replace(case, expected_values={})
    catalog = profile_from_ddl(suite.ddl, database_id=f"{suite.family}_{suite.seed}", seed_sql=suite.seed_sql)
    plan = plan_question(case.question, catalog)
    behavior = "clarify" if plan.action == "clarify" else "answer" if plan.action == "query" else "refuse"
    spec = plan.spec.to_dict() if plan.spec else None
    sql = ""
    if plan.action == "query" and plan.spec:
        compiled = compile_and_guard(plan.spec, catalog)
        sql = compiled.sql if compiled.ok else ""
    observation = SemanticObservation(
        behavior=behavior,
        terminal_status="INVALID_REQUEST" if behavior == "clarify" else "SUCCESS_WITH_DATA",
        sql=sql,
        spec=spec,
        clarify_slots=list(plan.clarify_slots or []),
    )
    return score_semantics(case, observation)


@pytest.mark.parametrize(
    "seed,family",
    [(seed, family) for seed in GATE_SEEDS for family in ("commerce", "billing", "support", "iot", "warehouse", "ambiguous")],
)
def test_unseen_schema_planner_family_gate(seed: int, family: str):
    suite = next(s for s in build_schema_suites(seed) if s.family == family)
    scores = [_observe(suite, case) for case in suite.single_turn_cases]
    wrong_table = sum(
        "wrong_subject_table" in score.hard_failures or "wrong_sql_tables" in score.hard_failures
        for score in scores
    )
    full_rate = sum(score.full_correct for score in scores) / len(scores)
    failures = [score.explain() for score in scores if not score.full_correct][:8]
    assert wrong_table == 0, f"wrong-table successful answers={wrong_table}; {failures}"
    assert full_rate >= 0.95, f"family={family} seed={seed} full_rate={full_rate:.3f}; {failures}"


@pytest.mark.parametrize("seed", GATE_SEEDS)
def test_explicit_table_identifier_grounding_is_100_percent(seed: int):
    failures = []
    total = 0
    for suite in build_schema_suites(seed):
        physical = suite.physical("fact")
        for case in suite.single_turn_cases:
            if physical not in case.question:
                continue
            total += 1
            score = _observe(suite, case)
            if not score.dimensions["subject_table"]:
                failures.append(score.explain())
    assert total >= 30
    assert not failures, failures[:10]

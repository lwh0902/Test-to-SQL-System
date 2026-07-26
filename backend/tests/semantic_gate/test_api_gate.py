from __future__ import annotations

import pytest

from app.agents.analysis_state_repository import AnalysisStateRepository
from app.agents.catalog_repository import CatalogRepository
from app.agents.diagnosis_summary_repository import DiagnosisSummaryRepository
from app.agents.live_profiler import profile_live_mysql
from app.application.analysis_service import AnalysisApplicationService
from app.application.connection_registry import clear_space_connection, register_space_connection
from app.application.contracts import KERNEL_V2, TurnRequest

from .assertions import score_semantics
from .contracts import SemanticObservation
from .mysql_runtime import provision_suite
from .schema_factory import build_schema_suites


@pytest.fixture
def live_semantic_service(tmp_path):
    suite = next(s for s in build_schema_suites() if s.family == "commerce")
    with provision_suite(suite) as (cfg, database):
        space = f"semantic_gate_{suite.family}_{suite.seed}"
        catalog = profile_live_mysql(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            database=database,
            database_id=space,
        )
        repo = CatalogRepository(tmp_path / "catalog")
        repo.save(space, catalog)
        register_space_connection(
            space,
            {
                "host": cfg.host,
                "port": cfg.port,
                "user": cfg.user,
                "password": cfg.password,
                "database": database,
            },
        )
        service = AnalysisApplicationService(
            kernel_route=KERNEL_V2,
            catalog_repo=repo,
            state_repo=AnalysisStateRepository(tmp_path / "state"),
            diagnosis_repo=DiagnosisSummaryRepository(tmp_path / "diagnosis"),
            skip_data_plane_precheck=True,
        )
        try:
            yield suite, space, service
        finally:
            clear_space_connection(space)


def _request(question: str, space: str, session: str) -> TurnRequest:
    return TurnRequest(
        question=question,
        user_id=9,
        user_role="tester",
        space_id=space,
        session_id=session,
    )


def _values(rows: list[dict]) -> dict:
    if not rows:
        return {}
    row = rows[0]
    if "value" in row:
        return {"value": row["value"]}
    if len(row) == 1:
        return {"value": next(iter(row.values()))}
    return dict(row)


def _canonical_spec(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        return {}
    ignored = {"spec_id", "original_question"}
    return {key: item for key, item in value.items() if key not in ignored}


@pytest.mark.asyncio
async def test_real_mysql_service_scores_sql_spec_and_value_not_http_only(live_semantic_service):
    suite, space, service = live_semantic_service
    selected = [suite.single_turn_cases[0], suite.single_turn_cases[10], suite.single_turn_cases[14]]
    failures = []
    for index, case in enumerate(selected):
        result = await service.handle_turn(_request(case.question, space, f"api_gate_{index}"))
        public = result.to_public_dict()
        spec = public.get("analysis_spec")
        if hasattr(spec, "to_dict"):
            spec = spec.to_dict()
        observation = SemanticObservation(
            behavior="clarify" if public.get("type") == "clarification" else "answer",
            terminal_status=str(public.get("terminal_status") or ""),
            sql=str(public.get("sql") or ""),
            spec=spec if isinstance(spec, dict) else {},
            result_values=_values(public.get("rows") or []),
            clarify_slots=list(public.get("clarify_slots") or []),
        )
        score = score_semantics(case, observation)
        if not score.full_correct:
            failures.append(score.explain())
    assert not failures, failures


@pytest.mark.asyncio
async def test_json_and_sse_complete_are_semantically_equivalent(live_semantic_service):
    suite, space, service = live_semantic_service
    question = suite.single_turn_cases[0].question
    direct = await service.handle_turn(_request(question, space, "json_path"))
    complete = None
    async for event, payload in service.handle_turn_stream(_request(question, space, "sse_path")):
        if event == "complete":
            complete = payload
    assert complete is not None
    direct_public = direct.to_public_dict()
    assert complete.get("terminal_status") == direct_public.get("terminal_status")
    assert complete.get("kernel_route") == direct_public.get("kernel_route") == KERNEL_V2
    assert complete.get("sql") == direct_public.get("sql")
    assert _canonical_spec(complete.get("analysis_spec")) == _canonical_spec(
        direct_public.get("analysis_spec")
    )


@pytest.mark.asyncio
async def test_real_mysql_ground_truth_count_across_all_schema_families(tmp_path):
    failures = []
    for suite in build_schema_suites():
        with provision_suite(suite) as (cfg, database):
            space = f"gt_{suite.family}_{suite.seed}"
            catalog = profile_live_mysql(
                host=cfg.host,
                port=cfg.port,
                user=cfg.user,
                password=cfg.password,
                database=database,
                database_id=space,
            )
            root = tmp_path / suite.family
            repo = CatalogRepository(root / "catalog")
            repo.save(space, catalog)
            register_space_connection(
                space,
                {
                    "host": cfg.host,
                    "port": cfg.port,
                    "user": cfg.user,
                    "password": cfg.password,
                    "database": database,
                },
            )
            try:
                service = AnalysisApplicationService(
                    kernel_route=KERNEL_V2,
                    catalog_repo=repo,
                    state_repo=AnalysisStateRepository(root / "state"),
                    diagnosis_repo=DiagnosisSummaryRepository(root / "diagnosis"),
                    skip_data_plane_precheck=True,
                )
                case = suite.single_turn_cases[0]
                result = await service.handle_turn(_request(case.question, space, f"gt_{suite.family}"))
                public = result.to_public_dict()
                score = score_semantics(
                    case,
                    SemanticObservation(
                        behavior="clarify" if public.get("type") == "clarification" else "answer",
                        terminal_status=str(public.get("terminal_status") or ""),
                        sql=str(public.get("sql") or ""),
                        spec=_canonical_spec(public.get("analysis_spec")),
                        result_values=_values(public.get("rows") or []),
                        clarify_slots=list(public.get("clarify_slots") or []),
                    ),
                )
                if not score.full_correct:
                    failures.append(score.explain())
            finally:
                clear_space_connection(space)
    assert not failures, failures

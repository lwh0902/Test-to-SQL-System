import asyncio
from datetime import date

from app.agents.semantic_catalog import ReadinessReport, ReadinessStatus, SemanticCatalog
from app.agents.supervisor_decision import SupervisorDecision, TaskSpec
from app.application.contracts import KERNEL_V2, TurnRequest, TurnResult


class CatalogRepo:
    def load(self, _space_id):
        return SemanticCatalog(
            database_id="travel_b2b",
            readiness=ReadinessReport(status=ReadinessStatus.READY),
        )


def decision(question):
    return SupervisorDecision(
        intent="data_query",
        task_spec=TaskSpec(target_agent="query", task_type="metric_query", params={}),
        resolved_question=question,
        confidence=1.0,
        fallback_used=False,
        route="data_query",
    )


def test_missing_time_pauses_before_dispatch(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository
    from app.application.analysis_service import AnalysisApplicationService

    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=PendingQueryRepository(tmp_path),
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)
    called = []

    async def dispatch(*args, **kwargs):
        called.append(True)
        return TurnResult(terminal_status="SUCCESS_WITH_DATA", sql="SELECT 1")

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    result = asyncio.run(service.handle_turn(TurnRequest(
        question="按渠道看订单数", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    assert result.stop_reason == "pending_time_confirmation"
    assert result.sql == ""
    assert "今天" in result.message
    assert called == []


def test_confirmation_resumes_original_query_with_explicit_dates(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="暑假7到8月订单数趋势", start="2026-07-01", end="2026-08-31",
        reason="missing_year", prompt="是否按今年？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)
    seen = []

    async def dispatch(req, _cat, _decision):
        seen.append(req.question)
        return TurnResult(terminal_status="SUCCESS_WITH_DATA", stop_reason="stop_ok", sql="SELECT 1")

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    result = asyncio.run(service.handle_turn(TurnRequest(
        question="确定", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    assert result.terminal_status == "SUCCESS_WITH_DATA"
    assert seen == ["暑假7到8月订单数趋势，时间范围为2026-07-01至2026-08-31"]
    assert repo.load("s1", 7, "travel_b2b") is None


def test_failed_resumed_query_keeps_pending_for_retry(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="暑假7到8月订单数趋势", start="2026-07-01", end="2026-08-31",
        reason="missing_year", prompt="是否按今年？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)

    async def dispatch(_req, _cat, _decision):
        return TurnResult(
            terminal_status="INVALID_REQUEST",
            stop_reason="semantic_parse",
            response_type="clarification",
        )

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    result = asyncio.run(service.handle_turn(TurnRequest(
        question="确定", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    assert result.stop_reason == "semantic_parse"
    assert repo.load("s1", 7, "travel_b2b") is not None


def test_rejection_keeps_pending_and_never_dispatches(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="订单数", start=date(2026, 8, 13).isoformat(), end=date(2026, 8, 13).isoformat(),
        reason="missing_time", prompt="是否查今天？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)
    result = asyncio.run(service.handle_turn(TurnRequest(
        question="不是", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    assert result.stop_reason == "pending_time_confirmation"
    assert "提供时间" in result.message
    assert repo.load("s1", 7, "travel_b2b") is not None


def test_replacement_time_resumes_with_user_dates(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="暑假7到8月订单数趋势", start="2026-07-01", end="2026-08-31",
        reason="missing_year", prompt="是否按今年？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)
    seen = []

    async def dispatch(req, _cat, _decision):
        seen.append(req.question)
        return TurnResult(terminal_status="SUCCESS_WITH_DATA", stop_reason="stop_ok", sql="SELECT 1")

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    asyncio.run(service.handle_turn(TurnRequest(
        question="改成2025年7到8月", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    assert seen == ["暑假7到8月订单数趋势，时间范围为2025-07-01至2025-08-31"]


def test_failed_replacement_keeps_replacement_dates_for_retry(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="暑假7到8月订单数趋势", start="2026-07-01", end="2026-08-31",
        reason="missing_year", prompt="是否按今年？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)

    async def dispatch(_req, _cat, _decision):
        return TurnResult(
            terminal_status="INVALID_REQUEST",
            stop_reason="semantic_parse",
            response_type="clarification",
        )

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    asyncio.run(service.handle_turn(TurnRequest(
        question="改成2025年7到8月", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    pending = repo.load("s1", 7, "travel_b2b")
    assert pending is not None
    assert (pending.start, pending.end) == ("2025-07-01", "2025-08-31")


def test_new_query_clears_pending_and_routes_normally(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="订单数", start="2026-08-13", end="2026-08-13",
        reason="missing_time", prompt="是否查今天？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2, skip_data_plane_precheck=True, catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    schema_decision = SupervisorDecision(
        intent="schema_understanding",
        task_spec=TaskSpec(target_agent="query", task_type="schema_inventory", params={}),
        resolved_question="这个库有什么表", confidence=1.0, fallback_used=False,
        route="schema_help",
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda _req: schema_decision)
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)

    async def dispatch(_req, _cat, _decision):
        return TurnResult(terminal_status="SUCCESS_WITH_DATA", stop_reason="schema_inventory")

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    result = asyncio.run(service.handle_turn(TurnRequest(
        question="这个库有什么表", user_id=7, space_id="travel_b2b", session_id="s1"
    )))

    assert result.stop_reason == "schema_inventory"
    assert repo.load("s1", 7, "travel_b2b") is None

import asyncio

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


async def collect_complete(service, request):
    events = [item async for item in service.handle_turn_stream(request)]
    complete = [payload for name, payload in events if name == "complete"]
    assert len(complete) == 1
    return complete[0]


def test_stream_missing_year_pauses_without_dispatch(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository
    from app.application.analysis_service import AnalysisApplicationService

    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2,
        skip_data_plane_precheck=True,
        catalog_repo=CatalogRepo(),
        pending_query_repo=PendingQueryRepository(tmp_path),
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)
    called = []

    async def dispatch(*_args, **_kwargs):
        called.append(True)
        return TurnResult(terminal_status="SUCCESS_WITH_DATA", sql="SELECT 1")

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    result = asyncio.run(collect_complete(service, TurnRequest(
        question="暑假7到8月订单数趋势",
        user_id=7,
        space_id="travel_b2b",
        session_id="stream-s1",
    )))

    assert result["stop_reason"] == "pending_time_confirmation"
    assert result["sql"] == ""
    assert "今年" in result["message"]
    assert called == []


def test_stream_confirmation_resumes_and_clears_pending_after_success(tmp_path, monkeypatch):
    from app.agents.pending_query_repository import PendingQueryRepository, PendingTimeConfirmation
    from app.application.analysis_service import AnalysisApplicationService

    repo = PendingQueryRepository(tmp_path)
    repo.save("stream-s1", 7, "travel_b2b", PendingTimeConfirmation(
        original_question="暑假7到8月订单数趋势",
        start="2026-07-01",
        end="2026-08-31",
        reason="missing_year",
        prompt="是否按今年？",
    ))
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2,
        skip_data_plane_precheck=True,
        catalog_repo=CatalogRepo(),
        pending_query_repo=repo,
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)
    seen = []

    async def dispatch(req, _cat, _decision):
        seen.append(req.question)
        return TurnResult(
            terminal_status="SUCCESS_WITH_DATA",
            stop_reason="stop_ok",
            sql="SELECT 1",
            rows=[{"value": 1}],
        )

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    result = asyncio.run(collect_complete(service, TurnRequest(
        question="确定",
        user_id=7,
        space_id="travel_b2b",
        session_id="stream-s1",
    )))

    assert result["terminal_status"] == "SUCCESS_WITH_DATA"
    assert seen == ["暑假7到8月订单数趋势，时间范围为2026-07-01至2026-08-31"]
    assert repo.load("stream-s1", 7, "travel_b2b") is None

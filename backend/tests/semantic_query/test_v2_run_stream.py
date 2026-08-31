import asyncio

from app.agents.semantic_catalog import ReadinessReport, ReadinessStatus, SemanticCatalog
from app.agents.supervisor_decision import SupervisorDecision, TaskSpec
from app.application.contracts import KERNEL_V2, TurnRequest, TurnResult
from app.services.run_store import InMemoryRunStore


class CatalogRepo:
    def load(self, _space_id):
        return SemanticCatalog(
            database_id="travel_b2b",
            readiness=ReadinessReport(status=ReadinessStatus.READY),
        )


def _decision(question):
    return SupervisorDecision(
        intent="data_query",
        task_spec=TaskSpec(target_agent="query", task_type="metric_query", params={}),
        resolved_question=question,
        confidence=1.0,
        fallback_used=False,
        route="data_query",
    )


def test_v2_stream_emits_committed_run_events_before_complete(monkeypatch):
    from app.application.analysis_service import AnalysisApplicationService

    store = InMemoryRunStore()
    service = AnalysisApplicationService(
        kernel_route=KERNEL_V2,
        skip_data_plane_precheck=True,
        catalog_repo=CatalogRepo(),
        run_store=store,
    )
    monkeypatch.setattr(service, "_run_supervisor", lambda req: _decision(req.question))
    monkeypatch.setattr(service, "_persist_turn", lambda *_: None)

    async def dispatch(*_args, **_kwargs):
        return TurnResult(
            response_type="answer",
            answer="订单数为 1。",
            message="订单数为 1。",
            terminal_status="SUCCESS_WITH_DATA",
            rows=[{"value": 1}],
        )

    monkeypatch.setattr(service, "_dispatch_v2", dispatch)
    request = TurnRequest(question="订单数", user_id=7, space_id="travel_b2b", session_id="s1")
    events = asyncio.run(_collect(service, request))

    timeline = [data for name, data in events if name == "run_event"]
    assert [event["seq"] for event in timeline] == list(range(1, len(timeline) + 1))
    assert [(event["agent"], event["status"]) for event in timeline] == [
        ("run", "started"),
        ("supervisor", "started"),
        ("supervisor", "completed"),
        ("run", "completed"),
    ]
    terminal = [data for name, data in events if name == "complete"]
    assert len(terminal) == 1
    assert terminal[0]["run_id"] == timeline[0]["run_id"]


async def _collect(service, request):
    return [item async for item in service.handle_turn_stream(request)]

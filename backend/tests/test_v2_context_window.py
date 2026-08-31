from app.application.contracts import KERNEL_V2, TurnRequest


def test_v2_supervisor_receives_exactly_twenty_recent_messages(monkeypatch):
    from app.application.analysis_service import AnalysisApplicationService

    calls = []
    monkeypatch.setattr(
        "app.services.persistence.load_recent_messages",
        lambda *args, **kwargs: calls.append(kwargs["limit"]) or [],
    )
    monkeypatch.setattr("app.application.analysis_service.supervisor_decide", lambda *args, **kwargs: None)
    service = AnalysisApplicationService(kernel_route=KERNEL_V2, skip_data_plane_precheck=True)

    service._run_supervisor(TurnRequest(question="订单数", user_id=7, space_id="travel", session_id="s1"))

    assert calls == [20]

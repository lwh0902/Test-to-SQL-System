from pathlib import Path


def test_supervisor_timeout_does_not_wait_for_slow_model_worker():
    import time

    from app.agents.model_adapter import ModelResponse
    from app.agents.supervisor_decision import decide

    def slow_complete(_request):
        time.sleep(0.05)
        return ModelResponse(ok=True, json_payload={})

    started = time.monotonic()
    decision = decide("帮我看看GMV", llm_complete=slow_complete, timeout_s=0.001)

    assert decision.layer == "L2"
    assert decision.error == "supervisor_decision_timeout_0.001s"
    assert time.monotonic() - started < 0.04


def test_loads_versioned_supervisor_policy_from_markdown():
    from app.services.prompt_policy_service import load_prompt_policy

    policy = load_prompt_policy("supervisor_policy")

    assert policy.name == "supervisor_policy"
    assert policy.version
    assert "data_query" in policy.content
    assert len(policy.sha256) == 64
    assert Path(policy.source_path).name == "supervisor_policy.md"


def test_supervisor_default_timeout_is_provider_configurable(monkeypatch):
    import importlib
    import app.agents.supervisor_decision as module

    monkeypatch.setenv("SUPERVISOR_DECISION_TIMEOUT_S", "60")
    reloaded = importlib.reload(module)

    assert reloaded._DECISION_TIMEOUT_S == 60.0


def test_supervisor_decision_records_the_policy_version():
    from app.agents.model_adapter import ModelResponse
    from app.agents.supervisor_decision import decide

    decision = decide(
        "2026年7到8月GMV趋势",
        llm_complete=lambda _: ModelResponse(ok=True, json_payload={
            "intent": "data_query",
            "task_spec": {"target_agent": "query", "task_type": "metric_query", "params": {}},
            "resolved_question": "2026年7到8月GMV趋势",
            "confidence": 0.9,
        }),
    )

    assert decision.layer == "L1"
    assert decision.policy_version == "2026-08-12.1"

from app.services.run_store import InMemoryRunStore


def test_run_events_are_monotonic_and_replayable_within_owner_scope():
    store = InMemoryRunStore()
    run = store.create_run(session_id="s1", user_id=7, space_id="travel", question="订单数")

    started = store.append_event(
        run["run_id"], kind="run", status="started", agent="supervisor", step="start"
    )
    completed = store.append_event(
        run["run_id"], kind="query", status="completed", agent="query", step="execute"
    )

    assert (started["seq"], completed["seq"]) == (1, 2)
    assert store.list_events(run["run_id"], user_id=7, after_seq=1) == [completed]
    assert store.list_events(run["run_id"], user_id=8, after_seq=0) == []


def test_run_event_payload_is_sanitized_before_persistence():
    store = InMemoryRunStore()
    run = store.create_run(session_id="s1", user_id=7, space_id="travel", question="订单数")

    event = store.append_event(
        run["run_id"],
        kind="query",
        status="started",
        public_payload={"summary": "正在执行只读查询", "password": "never-store"},
    )

    assert event["public_payload"] == {"summary": "正在执行只读查询"}

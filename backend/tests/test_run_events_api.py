from app.services.run_store import InMemoryRunStore


def test_run_event_replay_is_owner_scoped(monkeypatch):
    from app.api import chat

    store = InMemoryRunStore()
    run = store.create_run(session_id="s1", user_id=7, space_id="travel", question="订单数")
    store.append_event(run["run_id"], kind="run", status="started")
    later = store.append_event(run["run_id"], kind="query", status="completed")
    monkeypatch.setattr(chat, "get_run_store", lambda: store)
    monkeypatch.setattr(chat, "require_session_access", lambda *_args, **_kwargs: None)

    body = chat.get_run_events(run["run_id"], after_seq=1, user={"user_id": 7})

    assert body == {"run_id": run["run_id"], "events": [later]}

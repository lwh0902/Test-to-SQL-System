from datetime import datetime, timedelta, timezone


def _pending(created_at: str | None = None):
    from app.agents.pending_query_repository import PendingTimeConfirmation

    return PendingTimeConfirmation(
        original_question="暑假7到8月订单数趋势",
        start="2026-07-01",
        end="2026-08-31",
        reason="missing_year",
        prompt="是否按今年处理？",
        semantic_model_version="seed-v1",
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )


def test_pending_query_is_saved_loaded_and_deleted(tmp_path):
    from app.agents.pending_query_repository import PendingQueryRepository

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", _pending())

    assert repo.load("s1", 7, "travel_b2b").original_question == "暑假7到8月订单数趋势"
    assert repo.delete("s1", 7, "travel_b2b") is True
    assert repo.load("s1", 7, "travel_b2b") is None


def test_pending_query_is_isolated_by_session_user_and_space(tmp_path):
    from app.agents.pending_query_repository import PendingQueryRepository

    repo = PendingQueryRepository(tmp_path)
    repo.save("s1", 7, "travel_b2b", _pending())

    assert repo.load("s2", 7, "travel_b2b") is None
    assert repo.load("s1", 8, "travel_b2b") is None
    assert repo.load("s1", 7, "ecommerce") is None


def test_expired_pending_query_is_not_loaded(tmp_path):
    from app.agents.pending_query_repository import PendingQueryRepository

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    repo = PendingQueryRepository(tmp_path, ttl_seconds=60)
    repo.save("s1", 7, "travel_b2b", _pending(old))

    assert repo.load("s1", 7, "travel_b2b") is None

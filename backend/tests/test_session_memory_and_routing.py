from app.services import agent


def test_fallback_routes_contextual_short_question_to_follow_up():
    memory = {"last_metric": "gmv", "last_time_range": {"start": "2026-07-01", "end": "2026-07-07"}}
    assert agent._fallback_route("按渠道拆一下", memory) == "follow_up"


def test_fallback_routes_explicit_query_to_data_query():
    assert agent._fallback_route("最近7天销售额趋势", None) == "data_query"


def test_fallback_routes_ambiguous_question_to_clarification():
    assert agent._fallback_route("这个呢", None) == "clarification"


def test_session_memory_requires_matching_session_owner_and_space(monkeypatch):
    from app.services import session_memory_service

    captured = {}

    class Result:
        def fetchone(self):
            return ("仅限当前会话", 6)

    class Conn:
        def execute(self, statement, params):
            captured.update(params)
            return Result()

    class ConnectionContext:
        def __enter__(self):
            return Conn()

        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return ConnectionContext()

    monkeypatch.setattr(session_memory_service, "engine", Engine())
    assert session_memory_service.load_session_memory("s1", 7, "space-a") == "仅限当前会话"
    assert captured == {"session_id": "s1", "user_id": 7, "space_id": "space-a"}


def test_session_memory_refreshes_each_six_user_turns():
    from app.services.session_memory_service import should_refresh_session_memory

    assert should_refresh_session_memory(5, None) is False
    assert should_refresh_session_memory(6, None) is True
    assert should_refresh_session_memory(7, 6) is False
    assert should_refresh_session_memory(12, 6) is True

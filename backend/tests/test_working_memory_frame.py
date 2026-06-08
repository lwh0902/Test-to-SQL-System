"""测试 working_memory 任务帧扩展"""

import pytest


def test_calc_result_status_success():
    from app.services.persistence import _calc_result_status

    class FakeState:
        response_type = "answer"
        rows = [{"id": 1}, {"id": 2}]
        error_code = ""

    assert _calc_result_status(FakeState()) == "success"


def test_calc_result_status_no_data():
    from app.services.persistence import _calc_result_status

    class FakeState:
        response_type = "answer"
        rows = []
        error_code = ""

    assert _calc_result_status(FakeState()) == "no_data"


def test_calc_result_status_error():
    from app.services.persistence import _calc_result_status

    class FakeState:
        response_type = "error"
        rows = []
        error_code = "PERMISSION_DENIED"

    assert _calc_result_status(FakeState()) == "error"


def test_calc_result_status_clarification():
    from app.services.persistence import _calc_result_status

    class FakeState:
        response_type = "clarification"
        rows = []
        error_code = ""

    assert _calc_result_status(FakeState()) == "clarification"


def test_calc_result_status_error_takes_priority_over_rows():
    from app.services.persistence import _calc_result_status

    class FakeState:
        response_type = "error"
        rows = [{"id": 1}]
        error_code = "SQL_GUARD_BLOCKED"

    assert _calc_result_status(FakeState()) == "error"


def test_extract_target_from_intent_metric():
    from app.services.persistence import _extract_target
    from app.models.schemas import QueryIntent

    class FakeState:
        route = "data_query"
        sql = "SELECT * FROM scan_records LIMIT 10"
        intent = QueryIntent(metric="scan_success_rate", query_type="trend")

    assert _extract_target(FakeState()) == "scan_success_rate"


def test_extract_target_no_intent():
    from app.services.persistence import _extract_target

    class FakeState:
        route = "data_query"
        sql = "SELECT * FROM api_logs LIMIT 10"
        intent = None
        table_target = None

    assert _extract_target(FakeState()) is None


def test_extract_target_from_table_target():
    from app.services.persistence import _extract_target

    class FakeState:
        route = "table_query"
        sql = "SELECT id, api_name FROM api_logs LIMIT 10"
        intent = None
        table_target = "api_logs"

    assert _extract_target(FakeState()) == "api_logs"


def test_persist_updates_frame_on_answer(monkeypatch):
    """answer 类型应该写入完整 frame"""
    from app.services.persistence import persist_from_agent_state

    saved = {}

    def mock_update_wm(session_id, memory):
        saved.update(memory)

    monkeypatch.setattr("app.services.persistence.update_working_memory", mock_update_wm)
    monkeypatch.setattr("app.services.persistence.save_message", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.save_trace", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.auto_rename_session", lambda *a, **k: None)

    from app.models.schemas import QueryIntent, TimeRange

    class FakeState:
        session_id = "test-session"
        user_id = 1
        space_id = "tech_quality"
        trace_id = "trace_123"
        question = "最近7天扫描成功率趋势"
        intent = QueryIntent(metric="scan_success_rate", query_type="trend",
                             time_range=TimeRange(start="2026-05-29", end="2026-06-05"))
        response_type = "answer"
        route = "data_query"
        sql = "SELECT date, success_rate FROM scan_records WHERE date BETWEEN :start_time AND :end_time LIMIT 100"
        params = {"start_time": "2026-05-29", "end_time": "2026-06-05"}
        rows = []
        columns = ["date", "success_rate"]
        error_code = ""
        candidates = []
        chart = None
        trace = []
        working_memory = None
        message = "查询完成"

    persist_from_agent_state(FakeState())

    assert saved["last_route"] == "data_query"
    assert saved["last_target"] == "scan_success_rate"
    assert saved["last_target_type"] == "metric"
    assert saved["last_sql"] == FakeState.sql
    assert saved["last_result_status"] == "no_data"
    assert saved["last_rows_count"] == 0
    assert saved["last_metric"] == "scan_success_rate"
    assert saved["last_query_type"] == "trend"


def test_persist_preserves_frame_on_chat(monkeypatch):
    """chat 类型不应该覆盖 frame"""
    from app.services.persistence import persist_from_agent_state

    saved = {"last_route": "data_query", "last_target": "api_logs", "last_result_status": "no_data"}

    def mock_update_wm(session_id, memory):
        saved.update(memory)

    monkeypatch.setattr("app.services.persistence.update_working_memory", mock_update_wm)
    monkeypatch.setattr("app.services.persistence.save_message", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.save_trace", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.auto_rename_session", lambda *a, **k: None)

    class FakeState:
        session_id = "test-session"
        user_id = 1
        space_id = "tech_quality"
        trace_id = "trace_456"
        question = "谢谢"
        intent = None
        response_type = "chat"
        route = "chat"
        sql = ""
        params = {}
        rows = []
        columns = []
        error_code = ""
        candidates = []
        chart = None
        trace = []
        working_memory = {"last_route": "data_query", "last_target": "api_logs", "last_result_status": "no_data"}
        message = "不客气！"

    persist_from_agent_state(FakeState())

    # frame 应该保留，不被 chat 覆盖
    assert saved["last_target"] == "api_logs"
    assert saved["last_result_status"] == "no_data"
    # 不应该有 last_route 被覆盖为 "chat"
    assert saved["last_route"] == "data_query"


def test_persist_preserves_frame_on_database_profile(monkeypatch):
    """database_profile 是说明类回答，不应覆盖上一轮业务 frame"""
    from app.services.persistence import persist_from_agent_state

    saved = {"last_route": "table_query", "last_target": "api_logs", "last_result_status": "no_data"}

    def mock_update_wm(session_id, memory):
        saved.update(memory)

    monkeypatch.setattr("app.services.persistence.update_working_memory", mock_update_wm)
    monkeypatch.setattr("app.services.persistence.save_message", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.save_trace", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.auto_rename_session", lambda *a, **k: None)

    class FakeState:
        session_id = "test-session"
        user_id = 1
        space_id = "tech_quality"
        trace_id = "trace_profile"
        question = "现在接入的数据库是什么"
        intent = None
        table_target = None
        response_type = "answer"
        route = "database_profile"
        sql = ""
        params = {}
        rows = []
        columns = []
        error_code = ""
        candidates = []
        chart = None
        trace = []
        working_memory = {"last_route": "table_query", "last_target": "api_logs", "last_result_status": "no_data"}
        message = "当前接入的是 MySQL 数据库。"

    persist_from_agent_state(FakeState())

    assert saved["last_route"] == "table_query"
    assert saved["last_target"] == "api_logs"
    assert saved["last_result_status"] == "no_data"


def test_persist_merges_with_existing_frame(monkeypatch):
    """新的 frame 应该和已有 working_memory 合并"""
    from app.services.persistence import persist_from_agent_state

    saved = {}

    def mock_update_wm(session_id, memory):
        saved.update(memory)

    monkeypatch.setattr("app.services.persistence.update_working_memory", mock_update_wm)
    monkeypatch.setattr("app.services.persistence.save_message", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.save_trace", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.auto_rename_session", lambda *a, **k: None)

    from app.models.schemas import QueryIntent

    class FakeState:
        session_id = "test-session"
        user_id = 1
        space_id = "tech_quality"
        trace_id = "trace_789"
        question = "按渠道拆解"
        intent = QueryIntent(metric="gmv", query_type="breakdown")
        response_type = "answer"
        route = "data_query"
        sql = "SELECT channel, SUM(amount) FROM orders GROUP BY channel"
        params = {}
        rows = [{"channel": "wechat", "amount": 1000}]
        columns = ["channel", "amount"]
        error_code = ""
        candidates = []
        chart = None
        trace = []
        working_memory = {"user_preference": "dark_mode", "last_metric": "scan_success_rate"}
        message = "按渠道拆解完成"

    persist_from_agent_state(FakeState())

    # 已有的非 frame 字段应该保留
    assert saved["user_preference"] == "dark_mode"
    # frame 字段应该被覆盖为新值
    assert saved["last_metric"] == "gmv"
    assert saved["last_route"] == "data_query"
    assert saved["last_result_status"] == "success"


def test_persist_table_query_frame(monkeypatch):
    """table_query answer 应该写入表目标，供下一轮 diagnosis 使用"""
    from app.services.persistence import persist_from_agent_state

    saved = {}

    def mock_update_wm(session_id, memory):
        saved.update(memory)

    monkeypatch.setattr("app.services.persistence.update_working_memory", mock_update_wm)
    monkeypatch.setattr("app.services.persistence.save_message", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.save_trace", lambda *a, **k: None)
    monkeypatch.setattr("app.services.persistence.auto_rename_session", lambda *a, **k: None)

    class FakeState:
        session_id = "test-session"
        user_id = 1
        space_id = "tech_quality"
        trace_id = "trace_table"
        question = "看 api_logs"
        intent = None
        table_target = "api_logs"
        response_type = "answer"
        route = "table_query"
        sql = "SELECT id, api_name FROM api_logs LIMIT 10"
        params = {}
        rows = []
        columns = ["id", "api_name"]
        error_code = ""
        candidates = []
        chart = None
        trace = []
        working_memory = None
        message = "查询到 0 条数据"

    persist_from_agent_state(FakeState())

    assert saved["last_route"] == "table_query"
    assert saved["last_target"] == "api_logs"
    assert saved["last_target_type"] == "table"
    assert saved["last_result_status"] == "no_data"

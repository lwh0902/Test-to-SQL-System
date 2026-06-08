"""测试 table_query_handler 节点"""

import pytest
from unittest.mock import patch, MagicMock

from app.services.agent import AgentState, query_executor, table_query_handler


def _make_state() -> AgentState:
    return AgentState(
        question="看 api_logs",
        space_id="tech_quality",
        session_id="test-session-1",
    )


def test_table_query_sets_freeform():
    """handler 设置 is_freeform=True 并加载 schema"""
    state = _make_state()

    with patch("app.services.persistence.load_recent_messages", return_value=[]), \
         patch("app.services.persistence.load_working_memory", return_value=None), \
         patch("app.services.agent._load_space_schema", return_value={"api_logs": {"columns": []}}):
        result = table_query_handler(state)

    assert result["is_freeform"] is True
    assert result["db_schema"] is not None
    assert any(e["event"] == "table_query" for e in state.events)


def test_table_query_uses_system_schema_and_extracts_target():
    """预设空间不依赖 analysis_spaces.db_schema，也能识别表目标"""
    state = _make_state()

    with patch("app.services.persistence.load_recent_messages", return_value=[]), \
         patch("app.services.persistence.load_working_memory", return_value=None):
        result = table_query_handler(state)

    assert result["db_schema"] is not None
    assert "api_logs" in result["db_schema"]
    assert result["table_target"] == "api_logs"
    assert state.table_target == "api_logs"


def test_table_query_loads_context():
    """handler 加载 chat_history 和 working_memory"""
    state = _make_state()
    fake_history = [{"role": "user", "content": "上次的问题"}]
    fake_wm = {"last_route": "data_query", "last_target": "scan_records"}

    with patch("app.services.persistence.load_recent_messages", return_value=fake_history), \
         patch("app.services.persistence.load_working_memory", return_value=fake_wm), \
         patch("app.services.agent._load_space_schema", return_value=None):
        result = table_query_handler(state)

    assert result["chat_history"] == fake_history
    assert result["working_memory"] == fake_wm


def test_table_query_preserves_working_memory():
    """handler 不会覆盖已有的 working_memory"""
    existing_wm = {"last_route": "data_query", "last_result_status": "success"}
    state = _make_state()

    with patch("app.services.persistence.load_recent_messages", return_value=[]), \
         patch("app.services.persistence.load_working_memory", return_value=existing_wm), \
         patch("app.services.agent._load_space_schema", return_value=None):
        result = table_query_handler(state)

    assert result["working_memory"]["last_route"] == "data_query"
    assert result["working_memory"]["last_result_status"] == "success"


def test_table_query_no_session():
    """session_id 为 None 时不崩溃"""
    state = AgentState(question="看 api_logs", space_id="tech_quality")

    with patch("app.services.persistence.load_recent_messages", return_value=[]), \
         patch("app.services.persistence.load_working_memory", return_value=None), \
         patch("app.services.agent._load_space_schema", return_value=None):
        result = table_query_handler(state)

    assert result["is_freeform"] is True


def test_query_executor_supports_table_query_without_intent(monkeypatch):
    """table_query 没有 QueryIntent，也应该能执行并生成表查询回复"""
    state = AgentState(
        question="看 api_logs",
        space_id="tech_quality",
        route="table_query",
        table_target="api_logs",
        sql="SELECT id, api_name FROM api_logs LIMIT 10",
        params={},
    )

    monkeypatch.setattr(
        "app.services.agent.execute_query",
        lambda sql, params, space_id="": (["id", "api_name"], [{"id": 1, "api_name": "login"}]),
    )

    result = query_executor(state)

    assert result["rows"] == [{"id": 1, "api_name": "login"}]
    assert result["chart"] is None
    assert "api_logs" in result["message"]


def test_query_executor_table_query_no_data_uses_empty_check(monkeypatch):
    """table_query 0 行时使用稳定自检提示，不依赖 intent"""
    state = AgentState(
        question="看 api_logs",
        space_id="tech_quality",
        route="table_query",
        table_target="api_logs",
        sql="SELECT id, api_name FROM api_logs WHERE created_at >= '2099-01-01' LIMIT 10",
        params={},
    )

    monkeypatch.setattr(
        "app.services.agent.execute_query",
        lambda sql, params, space_id="": (["id", "api_name"], []),
    )
    monkeypatch.setattr(
        "app.services.agent._check_empty_result",
        lambda sql, space_id, table_target=None: f"表 {table_target} 有历史数据，但当前筛选条件没有匹配结果。",
    )

    result = query_executor(state)

    assert result["rows"] == []
    assert result["message"] == "表 api_logs 有历史数据，但当前筛选条件没有匹配结果。"


def test_empty_result_check_detects_time_after_latest(monkeypatch):
    """0 结果自检应识别查询时间晚于表内最新数据"""
    from datetime import datetime
    from app.services.agent import _check_empty_result

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=(1,))),  # table exists
        MagicMock(fetchone=MagicMock(return_value=(120,))),  # total rows
        MagicMock(fetchone=MagicMock(return_value=(datetime(2026, 5, 20, 12, 0, 0),))),  # latest created_at
    ]
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.core.engine_registry.engine_registry") as mock_reg:
        mock_reg.get_engine.return_value = mock_eng
        message = _check_empty_result(
            "SELECT id FROM api_logs WHERE created_at >= '2026-06-01' LIMIT 10",
            "tech_quality",
            "api_logs",
        )

    assert message is not None
    assert "共有 120 条数据" in message
    assert "最新数据时间：2026-05-20" in message
    assert "查询时间晚于最新数据" in message

"""测试 diagnosis_handler 节点"""

import pytest
from unittest.mock import patch, MagicMock

from app.services.agent import AgentState, diagnosis_handler


def _make_state(wm: dict | None = None) -> AgentState:
    state = AgentState(
        question="为什么没数据",
        space_id="tech_quality",
        working_memory=wm,
    )
    return state


def test_diagnosis_no_data_table_has_zero_rows():
    """表存在但总行数为 0 → 提示没有数据"""
    wm = {
        "last_result_status": "no_data",
        "last_target": "api_logs",
        "last_sql": "SELECT * FROM api_logs WHERE date >= '2025-01-01' LIMIT 100",
    }
    state = _make_state(wm)

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        MagicMock(scalar=MagicMock(return_value=1)),  # table_exists=True
        MagicMock(scalar=MagicMock(return_value=0)),  # total_rows=0
    ]
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.core.engine_registry.engine_registry") as mock_reg, \
         patch("anthropic.Anthropic"):
        mock_reg.get_engine.return_value = mock_eng
        result = diagnosis_handler(state)

    assert result["response_type"] == "answer"
    assert "0 条数据" in state.message or "没有任何数据" in state.message
    assert state.trace[-1]["node"] == "diagnosis_handler"


def test_diagnosis_table_not_found():
    """表不存在 → 提示表不存在"""
    wm = {
        "last_result_status": "error",
        "last_target": "nonexistent_table",
        "last_sql": "SELECT * FROM nonexistent_table LIMIT 100",
    }
    state = _make_state(wm)

    mock_conn = MagicMock()
    mock_conn.execute.return_value = MagicMock(scalar=MagicMock(return_value=0))
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.core.engine_registry.engine_registry") as mock_reg, \
         patch("anthropic.Anthropic"):
        mock_reg.get_engine.return_value = mock_eng
        result = diagnosis_handler(state)

    assert result["response_type"] == "answer"
    assert "不存在" in state.message


def test_diagnosis_time_filter_issue():
    """表有数据但当前 SQL 为空 → 用固定模板确认表内确实有数据"""
    wm = {
        "last_result_status": "no_data",
        "last_target": "api_logs",
        "last_sql": "SELECT * FROM api_logs WHERE date >= '2099-01-01' LIMIT 100",
    }
    state = _make_state(wm)

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        MagicMock(scalar=MagicMock(return_value=1)),   # table_exists
        MagicMock(scalar=MagicMock(return_value=500)),  # total_rows=500
        MagicMock(scalar=MagicMock(return_value=None)),  # MAX(created_at)
        MagicMock(scalar=MagicMock(return_value=None)),  # MAX(updated_at)
        MagicMock(scalar=MagicMock(return_value=None)),  # MAX(date)
        MagicMock(scalar=MagicMock(return_value=None)),  # MAX(timestamp)
        MagicMock(fetchone=MagicMock(return_value=("row",))),  # fixed existence probe
    ]
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.core.engine_registry.engine_registry") as mock_reg, \
         patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_reg.get_engine.return_value = mock_eng

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="时间范围太窄，表中有 500 条数据。")]
        mock_anthropic_cls.return_value.messages.create.return_value = mock_response

        result = diagnosis_handler(state)

    assert result["response_type"] == "answer"
    assert state.trace[-1]["output"]["checks"]["has_any_row"] is True


def test_diagnosis_db_connection_error():
    """数据库连接失败 → fallback 消息"""
    wm = {
        "last_result_status": "error",
        "last_target": "api_logs",
        "last_sql": "SELECT 1",
    }
    state = _make_state(wm)

    mock_eng = MagicMock()
    mock_eng.connect.side_effect = Exception("Connection refused")

    with patch("app.core.engine_registry.engine_registry") as mock_reg, \
         patch("anthropic.Anthropic"):
        mock_reg.get_engine.return_value = mock_eng
        result = diagnosis_handler(state)

    assert result["response_type"] == "answer"
    assert "error" in state.trace[-1]["output"]["checks"]


def test_diagnosis_no_working_memory():
    """没有 working_memory → 使用默认值"""
    state = _make_state(None)

    mock_eng = MagicMock()
    mock_eng.connect.side_effect = Exception("no db")

    with patch("app.core.engine_registry.engine_registry") as mock_reg, \
         patch("anthropic.Anthropic"):
        mock_reg.get_engine.return_value = mock_eng
        result = diagnosis_handler(state)

    assert result["response_type"] == "answer"
    assert len(state.message) > 0

"""测试 safe_sample_table 安全采样"""

import pytest
from unittest.mock import patch, MagicMock

from app.services.data_map_service import safe_sample_table, SENSITIVE_FIELDS


def test_sensitive_fields_not_in_result():
    """敏感字段不出现在结果中"""
    mock_conn = MagicMock()
    # 列名查询
    cols_result = MagicMock()
    cols_result.fetchall.return_value = [("id",), ("name",), ("phone",), ("email",)]
    # 数据查询
    data_result = MagicMock()
    data_result.fetchall.return_value = [(1, "test")]

    mock_conn.execute.side_effect = [cols_result, data_result]
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.services.data_map_service.engine_registry") as mock_reg:
        mock_reg.get_engine.return_value = mock_eng
        rows = safe_sample_table("tech_quality", "users")

    # phone 和 email 不应在结果中
    assert len(rows) > 0
    assert "phone" not in rows[0]
    assert "email" not in rows[0]
    assert "id" in rows[0]
    assert "name" in rows[0]


def test_all_columns_sensitive_returns_empty():
    """全部是敏感字段时返回空"""
    mock_conn = MagicMock()
    cols_result = MagicMock()
    cols_result.fetchall.return_value = [("phone",), ("email",), ("password",)]

    mock_conn.execute.return_value = cols_result
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.services.data_map_service.engine_registry") as mock_reg:
        mock_reg.get_engine.return_value = mock_eng
        rows = safe_sample_table("tech_quality", "users")

    assert rows == []


def test_sample_limit_respected():
    """采样数量不超过 limit"""
    mock_conn = MagicMock()
    cols_result = MagicMock()
    cols_result.fetchall.return_value = [("id",)]
    data_result = MagicMock()
    data_result.fetchall.return_value = [(1,), (2,), (3,)]

    mock_conn.execute.side_effect = [cols_result, data_result]
    mock_eng = MagicMock()
    mock_eng.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_eng.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.services.data_map_service.engine_registry") as mock_reg:
        mock_reg.get_engine.return_value = mock_eng
        rows = safe_sample_table("tech_quality", "users", limit=3)

    assert len(rows) == 3


def test_sensitive_fields_constant():
    """确认敏感字段列表包含核心项"""
    assert "phone" in SENSITIVE_FIELDS
    assert "password" in SENSITIVE_FIELDS
    assert "email" in SENSITIVE_FIELDS
    assert "id_card" in SENSITIVE_FIELDS
    assert "credit_card" in SENSITIVE_FIELDS

"""测试 get_db_identity 和 mask_host"""

from unittest.mock import patch, MagicMock
from app.services.data_map_service import get_db_identity, _mask_host


def test_mask_host_short():
    assert _mask_host("abc") == "ab***"


def test_mask_host_long():
    assert _mask_host("192.168.1.100") == "192***00"


def test_mask_host_none():
    assert _mask_host(None) == "***"


def test_mask_host_localhost():
    assert _mask_host("localhost") == "loc***st"


def test_get_db_identity_preset_space():
    """系统预设空间返回 is_preset=True"""
    mock_conn = MagicMock()
    mock_row = ("质量保障", "技术质量", None, None, None, None, None, None, None)
    mock_conn.execute.return_value.fetchone.return_value = mock_row

    with patch("app.services.data_map_service.engine") as mock_engine:
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = get_db_identity("tech_quality")

    assert result is not None
    assert result["is_preset"] is True
    assert result["space_name"] == "质量保障"
    assert result["table_count"] > 0  # tech_quality has SYSTEM_SCHEMAS


def test_get_db_identity_user_space():
    """用户自建空间返回连接信息（脱敏）"""
    mock_conn = MagicMock()
    mock_row = ("我的空间", "测试", "conn-1", None, "mysql", "192.168.1.100", 3306, "mydb", None)
    mock_conn.execute.return_value.fetchone.return_value = mock_row

    with patch("app.services.data_map_service.engine") as mock_engine:
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = get_db_identity("user-space-1")

    assert result is not None
    assert result["is_preset"] is False
    assert result["connection"]["db_type"] == "mysql"
    assert result["connection"]["host_masked"] == "192***00"
    assert result["connection"]["db_name"] == "mydb"


def test_get_db_identity_not_found():
    """空间不存在返回 None"""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None

    with patch("app.services.data_map_service.engine") as mock_engine:
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        result = get_db_identity("nonexistent")

    assert result is None

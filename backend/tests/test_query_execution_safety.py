import pytest

from app.services.query_service import QuerySafeError, _explain_check


class BrokenConnection:
    def __enter__(self):
        raise RuntimeError("EXPLAIN unavailable")

    def __exit__(self, *args):
        return False


class BrokenEngine:
    def connect(self):
        return BrokenConnection()


def test_explain_failure_is_denied_not_silently_allowed():
    result = _explain_check(BrokenEngine(), "SELECT id FROM orders LIMIT 1", {})
    assert result is not None
    assert "查询预检失败" in result

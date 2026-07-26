import os
import base64

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest-0123456789abcdef")
os.environ.setdefault("DB_ENCRYPTION_KEY", base64.b64encode(b"t" * 32).decode())


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """确保测试环境变量可用"""
    env_defaults = {
        "JWT_SECRET": "test-secret-for-pytest",
        "DB_HOST": "localhost",
        "DB_PORT": "3306",
        "DB_USER": "root",
        "DB_PASSWORD": "",
        "DB_NAME": "datacheck_test",
        "LLM_API_KEY": "test-key",
        "LLM_BASE_URL": "https://api.deepseek.com/anthropic",
        "LLM_MODEL": "deepseek-v4-flash",
        "DB_ENCRYPTION_KEY": "",
    }
    for key, default in env_defaults.items():
        if key not in os.environ:
            monkeypatch.setenv(key, default)


@pytest.fixture(autouse=True)
def _supervisor_l1_fast_fail(monkeypatch):
    """生产默认 L1=ON；单测用假 adapter 瞬时失败 → 自动 L2（不改 skip_l1 语义）。

    需要测真 L1 的用例自行 monkeypatch get_model_adapter 或传入 llm_complete。
    禁止用 DATAPILOT_SUPERVISOR_L1=0 冒充生产默认。
    """
    from app.agents.model_adapter import ModelResponse

    class _FastFailAdapter:
        def complete(self, req):
            return ModelResponse(ok=False, error="test_skip_l1_network")

        async def acomplete(self, req):
            return ModelResponse(ok=False, error="test_skip_l1_network")

    monkeypatch.setattr(
        "app.agents.supervisor_decision.get_model_adapter",
        lambda: _FastFailAdapter(),
    )

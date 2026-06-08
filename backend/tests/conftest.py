import os

import pytest


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

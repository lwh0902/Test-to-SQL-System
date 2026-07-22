import base64

import pytest


def test_security_config_rejects_default_jwt(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "JWT_SECRET", "change-me-in-production")
    monkeypatch.setattr(config, "DB_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        config.validate_security_config()

from app.services.connection_target_policy import connection_target_allowed


def test_default_policy_allows_only_local_development_targets(monkeypatch):
    monkeypatch.delenv("DATAPILOT_DB_ALLOWED_HOSTS", raising=False)

    assert connection_target_allowed("127.0.0.1") is True
    assert connection_target_allowed("localhost") is True
    assert connection_target_allowed("169.254.169.254") is False
    assert connection_target_allowed("db.untrusted.example") is False


def test_policy_allows_only_explicit_host_or_cidr(monkeypatch):
    monkeypatch.setenv("DATAPILOT_DB_ALLOWED_HOSTS", "db.company.test,10.20.0.0/16")

    assert connection_target_allowed("db.company.test") is True
    assert connection_target_allowed("10.20.8.9") is True
    assert connection_target_allowed("10.21.8.9") is False
    assert connection_target_allowed("db.other.test") is False

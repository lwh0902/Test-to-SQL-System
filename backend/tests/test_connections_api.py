from fastapi.testclient import TestClient

from app.api import connections
from app.core.auth import create_access_token
from app.main import app


def _auth_headers() -> dict[str, str]:
    token = create_access_token(user_id=42, role="user")
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "connection-api-test"}


def test_direct_connection_test_returns_service_result_and_audits_without_credentials(monkeypatch):
    received = {}
    audit_calls = []

    def fake_test_direct_connection(**kwargs):
        received.update(kwargs)
        return {"ok": True, "server_version": "8.0"}

    monkeypatch.setattr(connections, "test_direct_connection", fake_test_direct_connection, raising=False)
    monkeypatch.setattr(connections, "audit_security_event", lambda *args, **kwargs: audit_calls.append((args, kwargs)))

    response = TestClient(app).post("/api/connections/test-direct", json={
        "host": "db.example.test", "port": 3307, "db_user": "analyst",
        "db_password": "secret-password", "db_name": "analytics",
    }, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"ok": True, "server_version": "8.0"}
    assert received == {
        "host": "db.example.test", "port": 3307, "db_user": "analyst",
        "db_password": "secret-password", "db_name": "analytics",
    }
    assert audit_calls == [
        (("connection_direct_test", "connection-api-test", 42), {"ok": True})
    ]


def test_direct_schema_discovery_returns_service_result_and_audits_without_credentials(monkeypatch):
    received = {}
    audit_calls = []

    def fake_discover_schema_direct(**kwargs):
        received.update(kwargs)
        return {"ok": True, "schema": {"orders": ["id", "amount"]}}

    monkeypatch.setattr(connections, "discover_schema_direct", fake_discover_schema_direct, raising=False)
    monkeypatch.setattr(connections, "audit_security_event", lambda *args, **kwargs: audit_calls.append((args, kwargs)))

    response = TestClient(app).post("/api/connections/discover-schema", json={
        "host": "db.example.test", "db_user": "analyst",
        "db_password": "secret-password", "db_name": "analytics",
    }, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"ok": True, "schema": {"orders": ["id", "amount"]}}
    assert received == {
        "host": "db.example.test", "port": 3306, "db_user": "analyst",
        "db_password": "secret-password", "db_name": "analytics",
    }
    assert audit_calls == [
        (("connection_schema_discovery", "connection-api-test", 42), {"ok": True})
    ]

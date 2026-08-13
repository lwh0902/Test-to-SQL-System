from fastapi.testclient import TestClient

from app.api import connections
from app.core.auth import create_access_token
from app.main import app


def _auth_headers() -> dict[str, str]:
    token = create_access_token(user_id=42, role="user")
    return {"Authorization": f"Bearer {token}", "X-Request-ID": "connection-api-test"}


def test_direct_connection_test_endpoint_is_not_exposed():
    response = TestClient(app).post("/api/connections/test-direct", json={
        "host": "db.example.test", "port": 3307, "db_user": "analyst",
        "db_password": "secret-password", "db_name": "analytics",
    }, headers=_auth_headers())

    assert response.status_code in {404, 405}


def test_direct_schema_discovery_endpoint_is_not_exposed():
    response = TestClient(app).post("/api/connections/discover-schema", json={
        "host": "db.example.test", "db_user": "analyst",
        "db_password": "secret-password", "db_name": "analytics",
    }, headers=_auth_headers())

    assert response.status_code in {404, 405}


def test_saved_connection_test_hides_low_level_error(monkeypatch):
    monkeypatch.setattr(
        connections,
        "test_connection",
        lambda *_: {"ok": False, "error": "access denied for password secret-password"},
    )

    response = TestClient(app).post("/api/connections/owned-connection/test", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"ok": False, "code": "CONNECTION_FAILED", "message": "连接测试失败"}


def test_saved_connection_test_returns_owned_service_result(monkeypatch):
    monkeypatch.setattr(connections, "test_connection", lambda *_: {"ok": True})

    response = TestClient(app).post("/api/connections/owned-connection/test", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json() == {"ok": True}

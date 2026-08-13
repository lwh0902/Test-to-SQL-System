import pytest
from fastapi import HTTPException

from app.services import authorization_service as authz
from app.api import catalog
from app.api import sessions
from app.api import spaces


def test_personal_space_is_hidden_from_other_user(monkeypatch):
    monkeypatch.setattr(authz, "get_space_for_access", lambda space_id, user_id: None)
    with pytest.raises(HTTPException) as error:
        authz.require_space_access("private-space", 2)
    assert error.value.status_code == 404


def test_session_requires_matching_user_and_space(monkeypatch):
    monkeypatch.setattr(authz, "session_belongs_to_user_and_space", lambda *args: False)
    with pytest.raises(HTTPException) as error:
        authz.require_session_access("session-a", 2, "space-b")
    assert error.value.status_code == 404


def test_public_space_profile_requires_administrator():
    with pytest.raises(HTTPException) as error:
        authz.require_admin({"user_id": 2, "role": "user"})
    assert error.value.status_code == 403


def test_create_session_checks_space_access_before_persisting(monkeypatch):
    seen = []

    def deny(space_id, user_id):
        seen.append((space_id, user_id))
        raise HTTPException(status_code=404, detail="空间不存在")

    monkeypatch.setattr(sessions, "require_space_access", deny, raising=False)
    monkeypatch.setattr(
        sessions,
        "create_session",
        lambda *args, **kwargs: pytest.fail("must not create an unauthorized session"),
    )

    with pytest.raises(HTTPException) as error:
        sessions.create_session_endpoint(
            sessions.CreateSessionRequest(space_id="another-users-space"),
            {"user_id": 2, "role": "user"},
        )

    assert error.value.status_code == 404
    assert seen == [("another-users-space", 2)]


def test_admin_public_profile_registers_managed_connection(monkeypatch):
    calls = []

    class ProfiledCatalog:
        def to_public_dict(self):
            return {"readiness": "READY", "schema_fingerprint": "fp", "identity": {}}

        tables = []
        relations = []
        schema_fingerprint = "fp"

    monkeypatch.setattr(catalog, "require_space_access", lambda *_: {"id": "public", "user_id": None})
    monkeypatch.setattr(catalog, "require_admin", lambda *_: None)
    monkeypatch.setattr(catalog, "profile_live_mysql", lambda **_: ProfiledCatalog())
    monkeypatch.setattr(catalog, "catalog_contains_secrets", lambda _: [])
    monkeypatch.setattr(catalog, "analysis_allowed", lambda _: True)
    monkeypatch.setattr(catalog, "get_catalog_repository", lambda: type("Repo", (), {"save": lambda *_: True})())
    monkeypatch.setattr(catalog, "upsert_managed_space_connection", lambda **kwargs: calls.append(kwargs))

    result = catalog.profile_and_store(
        catalog.ProfileIn(space_id="public", host="db.example", user="readonly", password="secret", database="travel"),
        {"user_id": 1, "role": "admin"},
    )

    assert result["ok"] is True
    assert calls == [{
        "space_id": "public", "host": "db.example", "port": 3306,
        "db_user": "readonly", "db_password": "secret", "db_name": "travel",
    }]


def test_private_profile_uses_owned_saved_connection_not_request_credentials(monkeypatch):
    seen = {}

    class ProfiledCatalog:
        schema_fingerprint = "fp"
        tables = []
        relations = []

        def to_public_dict(self):
            return {"readiness": "READY", "schema_fingerprint": "fp", "identity": {}}

    monkeypatch.setattr(catalog, "require_space_access", lambda *_: {"id": "private", "user_id": 7})
    monkeypatch.setattr(
        catalog,
        "resolve_authorized_connection",
        lambda **_: {"host": "owned.db", "port": 3306, "user": "reader", "password": "owned-secret", "database": "owned"},
    )
    monkeypatch.setattr(catalog, "profile_live_mysql", lambda **kwargs: seen.update(kwargs) or ProfiledCatalog())
    monkeypatch.setattr(catalog, "catalog_contains_secrets", lambda _: [])
    monkeypatch.setattr(catalog, "analysis_allowed", lambda _: True)
    monkeypatch.setattr(catalog, "get_catalog_repository", lambda: type("Repo", (), {"save": lambda *_: True})())

    result = catalog.profile_and_store(
        catalog.ProfileIn(space_id="private", host="attacker.example", user="attacker", password="bad", database="other"),
        {"user_id": 7, "role": "user"},
    )

    assert result["ok"] is True
    assert {key: seen[key] for key in ("host", "port", "user", "password", "database")} == {
        "host": "owned.db", "port": 3306, "user": "reader", "password": "owned-secret", "database": "owned",
    }


def test_public_space_table_profile_requires_administrator(monkeypatch):
    monkeypatch.setattr(spaces, "require_space_access", lambda *_: {"id": "public", "user_id": None})
    monkeypatch.setattr(spaces, "require_admin", lambda *_: (_ for _ in ()).throw(
        HTTPException(status_code=403, detail="需要管理员权限")
    ), raising=False)
    monkeypatch.setattr(spaces, "profile_tables", lambda *_: pytest.fail("must not update public schema"))

    with pytest.raises(HTTPException) as error:
        spaces.profile_tables_endpoint.__wrapped__(None, "public", {"user_id": 2, "role": "user"})

    assert error.value.status_code == 403

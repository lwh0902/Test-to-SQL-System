import pytest
from fastapi import HTTPException

from app.services import authorization_service as authz


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

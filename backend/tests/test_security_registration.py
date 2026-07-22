import pytest
from pydantic import ValidationError

from app.api.auth import RegisterRequest


def test_public_registration_rejects_client_role():
    with pytest.raises(ValidationError):
        RegisterRequest(
            phone="13800138000",
            password="Password123",
            display_name="new-user",
            role="admin",
        )

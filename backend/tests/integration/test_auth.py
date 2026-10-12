"""Authentication endpoints.

Covers registration, login, and the uniform-failure property: a wrong password and an
unknown email must look identical to the caller, so the login endpoint cannot be used
to enumerate which addresses have accounts.
"""

from __future__ import annotations

import pytest

from tests.conftest import register_user

pytestmark = pytest.mark.integration


class TestRegister:
    async def test_register_creates_a_usable_token(self, client: object, api: str) -> None:
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/register",
            json={"email": "new.user@example.com", "password": "a-strong-password-1"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["access_token"]
        assert body["token_type"] == "bearer"

    async def test_register_also_creates_an_empty_profile(self, client: object, api: str) -> None:
        """Every authenticated request relies on a profile existing."""
        _, token = await register_user(client)
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/profile", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["skills"] == []

    async def test_duplicate_email_is_rejected(self, client: object, api: str) -> None:
        address = "duplicate@example.com"
        first = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/register",
            json={"email": address, "password": "a-strong-password-1"},
        )
        assert first.status_code == 201

        second = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/register",
            json={"email": address, "password": "a-different-password-2"},
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

    async def test_a_short_password_is_rejected(self, client: object, api: str) -> None:
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/register",
            json={"email": "short@example.com", "password": "short"},
        )
        assert response.status_code == 422

    async def test_an_invalid_email_is_rejected(self, client: object, api: str) -> None:
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/register",
            json={"email": "not-an-email", "password": "a-strong-password-1"},
        )
        assert response.status_code == 422

    async def test_an_unknown_field_is_rejected(self, client: object, api: str) -> None:
        """Requests use extra="forbid"; a typo in a payload is an error, not silence."""
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/register",
            json={
                "email": "strict@example.com",
                "password": "a-strong-password-1",
                "is_admin": True,
            },
        )
        assert response.status_code == 422


class TestLogin:
    async def test_correct_credentials_succeed(self, client: object, api: str) -> None:
        email, _ = await register_user(client, password="correct-password-1")
        response = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/login", json={"email": email, "password": "correct-password-1"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]

    async def test_wrong_password_and_unknown_email_look_identical(
        self, client: object, api: str
    ) -> None:
        """The endpoint must not be usable to discover which addresses are registered."""
        email, _ = await register_user(client, password="correct-password-1")

        wrong_password = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/login", json={"email": email, "password": "wrong-password-1"}
        )
        unknown_email = await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/login",
            json={"email": "nobody-here@example.com", "password": "wrong-password-1"},
        )

        assert wrong_password.status_code == 401
        assert unknown_email.status_code == 401
        assert wrong_password.json()["error"]["code"] == unknown_email.json()["error"]["code"]
        assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]

    async def test_login_updates_last_login_at(self, client: object, api: str) -> None:
        email, token = await register_user(client, password="correct-password-1")
        await client.post(  # type: ignore[attr-defined]
            f"{api}/auth/login", json={"email": email, "password": "correct-password-1"}
        )
        me = await client.get(  # type: ignore[attr-defined]
            f"{api}/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.json()["last_login_at"] is not None


class TestMe:
    async def test_requires_authentication(self, client: object, api: str) -> None:
        response = await client.get(f"{api}/auth/me")  # type: ignore[attr-defined]
        assert response.status_code == 401

    async def test_rejects_a_garbage_token(self, client: object, api: str) -> None:
        response = await client.get(  # type: ignore[attr-defined]
            f"{api}/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    async def test_returns_the_authenticated_user(
        self, client: object, api: str, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{api}/auth/me", headers=auth)  # type: ignore[attr-defined]
        assert response.status_code == 200
        assert "@" in response.json()["email"]
        # The password hash must never be exposed, under any field name.
        assert "password" not in response.text.lower()

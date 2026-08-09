"""Tests for the authentication flow: register, login, refresh, logout."""

from datetime import datetime, timedelta

from httpx import AsyncClient
from jose import jwt

from app.core.config import get_settings

settings = get_settings()

EMAIL = "reader@example.com"
PASSWORD = "super-secret-password"


async def _register_and_login(client: AsyncClient) -> dict[str, str]:
    await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    response = await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    body: dict[str, str] = response.json()
    return body


async def test_register_returns_user_without_password(client: AsyncClient) -> None:
    response = await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == EMAIL
    assert "password" not in body
    assert "hashed_password" not in body


async def test_duplicate_register_is_rejected(client: AsyncClient) -> None:
    first = await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    second = await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})

    assert first.status_code == 201
    assert second.status_code == 422
    assert "already" in second.json()["detail"].lower()


async def test_login_with_wrong_password_is_rejected(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})

    response = await client.post("/auth/login", json={"email": EMAIL, "password": "wrong"})

    assert response.status_code == 401


async def test_login_with_nonexistent_email_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401


async def test_successful_login_allows_profile_access(client: AsyncClient) -> None:
    tokens = await _register_and_login(client)

    response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


async def test_expired_token_is_rejected(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})

    expired_token = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "iat": datetime.utcnow() - timedelta(minutes=10),
            "exp": datetime.utcnow() - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {expired_token}"})

    assert response.status_code == 401


async def test_missing_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/users/me")

    assert response.status_code == 401


async def test_refresh_rotation_invalidates_old_token(client: AsyncClient) -> None:
    tokens = await _register_and_login(client)

    renewal = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert renewal.status_code == 200
    new_tokens = renewal.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    old_retry = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert old_retry.status_code == 401

    new_works = await client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert new_works.status_code == 200


async def test_refresh_with_unknown_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/auth/refresh", json={"refresh_token": "made-up-token"})

    assert response.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    tokens = await _register_and_login(client)

    logout_response = await client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout_response.status_code == 204

    retry = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert retry.status_code == 401


async def test_logout_with_unknown_token_is_idempotent(client: AsyncClient) -> None:
    response = await client.post("/auth/logout", json={"refresh_token": "does-not-exist"})

    assert response.status_code == 204

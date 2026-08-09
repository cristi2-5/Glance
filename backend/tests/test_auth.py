"""Teste pentru fluxul de autentificare: register, login, refresh, logout."""

from datetime import datetime, timedelta

from httpx import AsyncClient
from jose import jwt

from app.core.config import get_settings

settings = get_settings()

EMAIL = "cititor@example.com"
PAROLA = "parola-super-secreta"


async def _inregistreaza_si_autentifica(client: AsyncClient) -> dict[str, str]:
    await client.post("/auth/register", json={"email": EMAIL, "password": PAROLA})
    response = await client.post("/auth/login", json={"email": EMAIL, "password": PAROLA})
    body: dict[str, str] = response.json()
    return body


async def test_register_returneaza_utilizator_fara_parola(client: AsyncClient) -> None:
    response = await client.post("/auth/register", json={"email": EMAIL, "password": PAROLA})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == EMAIL
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_duplicat_este_respins(client: AsyncClient) -> None:
    prima = await client.post("/auth/register", json={"email": EMAIL, "password": PAROLA})
    a_doua = await client.post("/auth/register", json={"email": EMAIL, "password": PAROLA})

    assert prima.status_code == 201
    assert a_doua.status_code == 422
    assert "deja" in a_doua.json()["detail"].lower()


async def test_login_cu_parola_gresita_este_respins(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": EMAIL, "password": PAROLA})

    response = await client.post("/auth/login", json={"email": EMAIL, "password": "gresita"})

    assert response.status_code == 401


async def test_login_cu_email_inexistent_este_respins(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "nimeni@example.com", "password": PAROLA}
    )

    assert response.status_code == 401


async def test_login_reusit_permite_accesul_la_profil(client: AsyncClient) -> None:
    tokeni = await _inregistreaza_si_autentifica(client)

    response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {tokeni['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


async def test_token_expirat_este_respins(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": EMAIL, "password": PAROLA})

    token_expirat = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "iat": datetime.utcnow() - timedelta(minutes=10),
            "exp": datetime.utcnow() - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {token_expirat}"})

    assert response.status_code == 401


async def test_lipsa_token_este_respinsa(client: AsyncClient) -> None:
    response = await client.get("/users/me")

    assert response.status_code == 401


async def test_refresh_rotation_invalideaza_tokenul_vechi(client: AsyncClient) -> None:
    tokeni = await _inregistreaza_si_autentifica(client)

    reinnoire = await client.post("/auth/refresh", json={"refresh_token": tokeni["refresh_token"]})
    assert reinnoire.status_code == 200
    tokeni_noi = reinnoire.json()
    assert tokeni_noi["refresh_token"] != tokeni["refresh_token"]

    reincercare_veche = await client.post(
        "/auth/refresh", json={"refresh_token": tokeni["refresh_token"]}
    )
    assert reincercare_veche.status_code == 401

    functioneaza_noul = await client.post(
        "/auth/refresh", json={"refresh_token": tokeni_noi["refresh_token"]}
    )
    assert functioneaza_noul.status_code == 200


async def test_refresh_cu_token_necunoscut_este_respins(client: AsyncClient) -> None:
    response = await client.post("/auth/refresh", json={"refresh_token": "token-inventat"})

    assert response.status_code == 401


async def test_logout_revoca_refresh_tokenul(client: AsyncClient) -> None:
    tokeni = await _inregistreaza_si_autentifica(client)

    logout_response = await client.post(
        "/auth/logout", json={"refresh_token": tokeni["refresh_token"]}
    )
    assert logout_response.status_code == 204

    reincercare = await client.post(
        "/auth/refresh", json={"refresh_token": tokeni["refresh_token"]}
    )
    assert reincercare.status_code == 401


async def test_logout_cu_token_necunoscut_e_idempotent(client: AsyncClient) -> None:
    response = await client.post("/auth/logout", json={"refresh_token": "nu-exista"})

    assert response.status_code == 204

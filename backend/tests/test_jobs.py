"""Tests for the job skeleton: cover upload, polling, owner isolation."""

from httpx import AsyncClient

EMAIL_A = "reader.a@example.com"
EMAIL_B = "reader.b@example.com"
PASSWORD = "super-secret-password"

A_SMALL_IMAGE = b"\xff\xd8\xff\xe0" + b"fake-jpeg-content" * 10


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["access_token"]
    return access_token


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_analyze_cover_creates_job_that_becomes_done(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)

    creation = await client.post(
        "/books/analyze-cover",
        headers=_auth_header(token),
        files={"file": ("cover.jpg", A_SMALL_IMAGE, "image/jpeg")},
    )
    assert creation.status_code == 202
    job_id = creation.json()["job_id"]

    read = await client.get(f"/jobs/{job_id}", headers=_auth_header(token))
    assert read.status_code == 200
    body = read.json()
    assert body["status"] == "done"
    assert body["result"]["image_size_bytes"] == len(A_SMALL_IMAGE)
    assert body["error"] is None


async def test_analyze_cover_rejects_unsupported_file_type(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)

    response = await client.post(
        "/books/analyze-cover",
        headers=_auth_header(token),
        files={"file": ("cover.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 415


async def test_analyze_cover_rejects_file_too_large(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)
    huge_image = b"\xff" * (8 * 1024 * 1024 + 1)

    response = await client.post(
        "/books/analyze-cover",
        headers=_auth_header(token),
        files={"file": ("cover.jpg", huge_image, "image/jpeg")},
    )

    assert response.status_code == 413


async def test_analyze_cover_without_authentication_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/books/analyze-cover",
        files={"file": ("cover.jpg", A_SMALL_IMAGE, "image/jpeg")},
    )

    assert response.status_code == 401


async def test_get_nonexistent_job_returns_404(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)

    response = await client.get("/jobs/999", headers=_auth_header(token))

    assert response.status_code == 404


async def test_get_job_without_authentication_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/jobs/1")

    assert response.status_code == 401


async def test_get_job_of_another_user_is_forbidden(client: AsyncClient) -> None:
    token_a = await _register_and_login(client, EMAIL_A)
    token_b = await _register_and_login(client, EMAIL_B)

    creation = await client.post(
        "/books/analyze-cover",
        headers=_auth_header(token_a),
        files={"file": ("cover.jpg", A_SMALL_IMAGE, "image/jpeg")},
    )
    job_id = creation.json()["job_id"]

    foreign_read = await client.get(f"/jobs/{job_id}", headers=_auth_header(token_b))

    assert foreign_read.status_code == 403

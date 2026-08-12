"""Tests for `PATCH /jobs/{job_id}/correction`."""

from io import BytesIO

from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job import Job, JobStatus

EMAIL_A = "reader.a@example.com"
EMAIL_B = "reader.b@example.com"
PASSWORD = "super-secret-password"


def _make_small_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (200, 300), color=(10, 200, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


A_SMALL_IMAGE = _make_small_jpeg()


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    access_token: str = response.json()["access_token"]
    return access_token


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_done_job(client: AsyncClient, token: str) -> int:
    creation = await client.post(
        "/books/analyze-cover",
        headers=_auth_header(token),
        files={"file": ("cover.jpg", A_SMALL_IMAGE, "image/jpeg")},
    )
    job_id: int = creation.json()["job_id"]
    return job_id


async def test_correction_overrides_title_and_author(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)
    job_id = await _create_done_job(client, token)

    response = await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token),
        json={"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["title"] == "The Left Hand of Darkness"
    assert body["result"]["author"] == "Ursula K. Le Guin"
    assert body["result"]["confidence"] == 1.0
    assert body["result"]["method"] == "manual"
    assert body["result"]["needs_review"] is False
    assert body["result"]["corrected"] is True


async def test_correction_allows_null_author(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)
    job_id = await _create_done_job(client, token)

    response = await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token),
        json={"title": "Anonymous Chronicle"},
    )

    assert response.status_code == 200
    assert response.json()["result"]["author"] is None


async def test_correction_of_another_users_job_is_forbidden(client: AsyncClient) -> None:
    token_a = await _register_and_login(client, EMAIL_A)
    token_b = await _register_and_login(client, EMAIL_B)
    job_id = await _create_done_job(client, token_a)

    response = await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token_b),
        json={"title": "Hijacked Title"},
    )

    assert response.status_code == 403


async def test_correction_of_pending_job_is_rejected(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    token = await _register_and_login(client, EMAIL_A)
    me = await client.get("/users/me", headers=_auth_header(token))
    user_id = me.json()["id"]

    async with db_session_factory() as db:
        job = Job(user_id=user_id, status=JobStatus.PENDING.value)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    response = await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token),
        json={"title": "Doesn't Matter"},
    )

    assert response.status_code == 422


async def test_correction_rejects_empty_title(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)
    job_id = await _create_done_job(client, token)

    response = await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token),
        json={"title": ""},
    )

    assert response.status_code == 422


async def test_correction_without_authentication_is_rejected(client: AsyncClient) -> None:
    response = await client.patch(
        "/jobs/1/correction",
        json={"title": "Doesn't Matter"},
    )

    assert response.status_code == 401


# --- Module 4: correcting a title invalidates the fetched metadata ----------


async def test_correction_immediately_discards_the_previous_books_metadata(
    client: AsyncClient,
) -> None:
    # A correction usually means the wrong book was recognized, so the
    # cover, blurb and rating fetched for it describe something the user
    # isn't holding. They must be gone from the very first response —
    # rendering another book's cover under a corrected title is worse than
    # rendering nothing.
    token = await _register_and_login(client, EMAIL_A)
    job_id = await _create_done_job(client, token)

    before = await client.get(f"/jobs/{job_id}", headers=_auth_header(token))
    assert before.json()["result"]["cover_url"] is not None, "fixture should start with metadata"

    response = await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token),
        json={"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "running", "the client keeps polling while metadata is re-fetched"
    assert body["result"]["cover_url"] is None
    assert body["result"]["description"] is None
    assert body["result"]["categories"] == []
    assert body["result"]["average_rating"] is None
    assert body["result"]["metadata_found"] is False


async def test_correction_refetches_and_completes_the_job(client: AsyncClient) -> None:
    token = await _register_and_login(client, EMAIL_A)
    job_id = await _create_done_job(client, token)

    await client.patch(
        f"/jobs/{job_id}/correction",
        headers=_auth_header(token),
        json={"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"},
    )

    # The background task runs within the ASGI call, so by the time the
    # next request is served the re-fetch has finished.
    after = await client.get(f"/jobs/{job_id}", headers=_auth_header(token))
    result = after.json()["result"]

    assert after.json()["status"] == "done"
    assert result["metadata_found"] is True, "metadata was re-fetched for the corrected title"
    assert result["cover_url"] is not None
    # The corrected title survives the re-fetch: the catalog's spelling
    # must never overwrite what the user typed by hand.
    assert result["title"] == "The Left Hand of Darkness"
    assert result["author"] == "Ursula K. Le Guin"
    assert result["corrected"] is True
    assert result["method"] == "manual"

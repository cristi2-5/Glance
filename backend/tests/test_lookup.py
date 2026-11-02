"""Tests for `POST /books/lookup` (find a book by typing its title and author)."""

import json

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job import Job
from app.workers.queue_worker import QueueWorker
from tests.fakes import InMemoryJobQueue

EMAIL_A = "reader.a@example.com"
EMAIL_B = "reader.b@example.com"
PASSWORD = "super-secret-password"


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_lookup_creates_a_running_photoless_job_and_one_refresh_message(
    client: AsyncClient,
    job_queue: InMemoryJobQueue,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _register_and_login(client, EMAIL_A)

    response = await client.post(
        "/books/lookup", headers=headers, json={"title": "Dune", "author": "Frank Herbert"}
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
    assert job is not None
    assert job.status == "running"
    assert job.photo_blob is None
    assert job.result is not None
    assert job.result["title"] == "Dune"
    assert job.result["author"] == "Frank Herbert"
    assert job.result["searched"] is True
    assert job.result["method"] == "manual"
    assert job.result["confidence"] == 1.0
    assert job.result["needs_review"] is False
    assert job.result["corrected"] is False
    assert job.result["book_id"] is None

    assert len(job_queue.bodies) == 1
    message = json.loads(job_queue.bodies[0])
    assert message["kind"] == "refresh"
    assert message["job_id"] == job_id


async def test_lookup_strips_whitespace_and_blank_author_becomes_none(
    client: AsyncClient,
) -> None:
    headers = await _register_and_login(client, EMAIL_A)

    response = await client.post(
        "/books/lookup", headers=headers, json={"title": "  Dune  ", "author": "   "}
    )

    job = (await client.get(f"/jobs/{response.json()['job_id']}", headers=headers)).json()
    assert job["result"]["title"] == "Dune"
    assert job["result"]["author"] is None


async def test_lookup_without_authentication_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/books/lookup", json={"title": "Dune"})

    assert response.status_code == 401


async def test_lookup_rejects_an_empty_or_blank_title(
    client: AsyncClient, job_queue: InMemoryJobQueue
) -> None:
    headers = await _register_and_login(client, EMAIL_A)

    for title in ("", "   ", "\t\n"):
        response = await client.post("/books/lookup", headers=headers, json={"title": title})
        assert response.status_code == 422, repr(title)
    assert job_queue.bodies == []


async def test_lookup_is_a_503_and_the_job_fails_when_the_queue_is_down(
    client: AsyncClient,
    job_queue: InMemoryJobQueue,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _register_and_login(client, EMAIL_A)
    job_queue.fail_send = True

    response = await client.post("/books/lookup", headers=headers, json={"title": "Dune"})

    assert response.status_code == 503
    # The only job in the table, found without assuming ids restart at 1.
    async with db_session_factory() as db:
        job = (await db.execute(select(Job))).scalar_one_or_none()
    assert job is not None
    assert job.status == "failed"
    assert job.error


async def test_lookup_runs_through_the_queue_worker_and_lands_in_the_library(
    client: AsyncClient, queue_worker: QueueWorker
) -> None:
    headers = await _register_and_login(client, EMAIL_A)
    response = await client.post(
        "/books/lookup", headers=headers, json={"title": "Dune", "author": "Frank Herbert"}
    )
    job_id = response.json()["job_id"]

    assert await queue_worker.process_one() is True

    body = (await client.get(f"/jobs/{job_id}", headers=headers)).json()
    result = body["result"]
    assert body["status"] == "done"
    assert result["book_id"] is not None
    assert result["metadata_found"] is True
    assert result["cover_url"] == "https://example.test/dune.jpg"
    assert result["searched"] is True
    assert result["title"] == "Dune"

    library = (await client.get("/users/me/library", headers=headers)).json()
    assert [entry["book"]["id"] for entry in library] == [result["book_id"]]


async def test_another_user_cannot_read_a_lookup_job(client: AsyncClient) -> None:
    headers_a = await _register_and_login(client, EMAIL_A)
    headers_b = await _register_and_login(client, EMAIL_B)
    job_id = (await client.post("/books/lookup", headers=headers_a, json={"title": "Dune"})).json()[
        "job_id"
    ]

    response = await client.get(f"/jobs/{job_id}", headers=headers_b)

    assert response.status_code == 403

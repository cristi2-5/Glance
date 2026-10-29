"""Tests for the scan-photo flow: upload, queueing, serving, and the photo lifecycle.

The routes run against in-memory fakes of the blob store and queue; the
pipeline and the database are real.
"""

import asyncio
import json
from io import BytesIO

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_job_queue
from app.core.config import get_settings
from app.main import app
from app.models.book import SourceKind, SourceName
from app.models.job import Job, JobStatus
from app.models.library import LibraryEntry
from app.models.user import User
from app.schemas.library import LibraryEntryUpdate
from app.schemas.vision import CoverIdentification
from app.services import library_service
from app.services.data_fetcher import BookDataFetcher
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult
from app.services.storage import JobMessage
from app.workers.cover_pipeline import fail_job, process_cover, refresh_book_data
from app.workers.queue_worker import QueueWorker
from tests.fakes import FakeContentSource, InMemoryBlobStore, InMemoryJobQueue

PASSWORD = "super-secret-password"


def _jpeg(width: int = 200, height: int = 300) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(120, 80, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _scan(
    client: AsyncClient, headers: dict[str, str], worker: QueueWorker, image: bytes | None = None
) -> dict[str, object]:
    """Uploads a cover, lets the queue worker finish it, returns the job body."""
    creation = await client.post(
        "/books/analyze-cover",
        headers=headers,
        files={"file": ("cover.jpg", image or _jpeg(), "image/jpeg")},
    )
    assert creation.status_code == 202, creation.text
    assert await worker.process_one() is True
    job = await client.get(f"/jobs/{creation.json()['job_id']}", headers=headers)
    body: dict[str, object] = job.json()
    assert body["status"] == "done", body
    return body


def _book_id(job_body: dict[str, object]) -> int:
    result = job_body["result"]
    assert isinstance(result, dict)
    book_id = result["book_id"]
    assert isinstance(book_id, int)
    return book_id


# --- upload ----------------------------------------------------------------


async def test_upload_stores_a_normalised_jpeg_and_enqueues_one_message(
    client: AsyncClient,
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")

    response = await client.post(
        "/books/analyze-cover",
        headers=headers,
        files={"file": ("cover.jpg", _jpeg(1600, 2400), "image/jpeg")},
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert [json.loads(body) for body in job_queue.bodies] == [{"kind": "scan", "job_id": job_id}]
    assert len(blob_store.blobs) == 1
    ((name, stored),) = blob_store.blobs.items()
    assert name.endswith(".jpg")
    with Image.open(BytesIO(stored)) as image:
        assert image.format == "JPEG"
        assert max(image.size) == get_settings().image_max_edge_px, "downscaled once, at upload"
    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING.value
        assert job.photo_blob == name


async def test_the_job_is_committed_before_it_is_enqueued(
    client: AsyncClient,
    blob_store: InMemoryBlobStore,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A fast queue worker must never dequeue a message for a job that is not there yet."""
    seen: list[tuple[int, str | None]] = []

    class _CheckingQueue(InMemoryJobQueue):
        async def send(self, message: JobMessage) -> None:
            async with db_session_factory() as db:
                job = await db.get(Job, message.job_id)
                assert job is not None, "the message was sent before the job was committed"
                seen.append((job.id, job.photo_blob))
            assert job.photo_blob in blob_store.blobs, "the photo is uploaded before enqueueing"
            await super().send(message)

    app.dependency_overrides[get_job_queue] = lambda: _CheckingQueue()
    headers = await _login(client, "a@example.com")

    response = await client.post(
        "/books/analyze-cover",
        headers=headers,
        files={"file": ("cover.jpg", _jpeg(), "image/jpeg")},
    )

    assert response.status_code == 202
    assert len(seen) == 1


async def test_blob_storage_down_fails_the_job_and_answers_503(
    client: AsyncClient,
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")
    blob_store.fail = True

    response = await client.post(
        "/books/analyze-cover",
        headers=headers,
        files={"file": ("cover.jpg", _jpeg(), "image/jpeg")},
    )

    assert response.status_code == 503
    assert job_queue.bodies == []
    async with db_session_factory() as db:
        job = await db.scalar(select(Job))
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.error == "The scan could not be queued. Please try again."
        assert job.photo_blob is None


async def test_queue_down_fails_the_job_removes_the_photo_and_answers_503(
    client: AsyncClient,
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")
    job_queue.fail_send = True

    response = await client.post(
        "/books/analyze-cover",
        headers=headers,
        files={"file": ("cover.jpg", _jpeg(), "image/jpeg")},
    )

    assert response.status_code == 503
    assert blob_store.blobs == {}, "nothing is left half-done"
    async with db_session_factory() as db:
        job = await db.scalar(select(Job))
        assert job is not None and job.status == JobStatus.FAILED.value


async def test_an_undecodable_image_is_rejected_before_any_job_exists(
    client: AsyncClient,
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")

    response = await client.post(
        "/books/analyze-cover",
        headers=headers,
        files={"file": ("cover.jpg", b"definitely not a jpeg", "image/jpeg")},
    )

    assert response.status_code == 422
    assert blob_store.blobs == {} and job_queue.bodies == []
    async with db_session_factory() as db:
        assert await db.scalar(select(Job)) is None


async def test_correction_enqueues_a_refresh_message(
    client: AsyncClient, queue_worker: QueueWorker, job_queue: InMemoryJobQueue
) -> None:
    headers = await _login(client, "a@example.com")
    body = await _scan(client, headers, queue_worker)

    response = await client.patch(
        f"/jobs/{body['id']}/correction",
        headers=headers,
        json={"title": "Dune Messiah", "author": "Frank Herbert"},
    )

    assert response.status_code == 200 and response.json()["status"] == "running"
    # `previous_book_id` is the misidentified book, which the endpoint is about
    # to clear from the job result and the queue worker still needs.
    assert [json.loads(b) for b in job_queue.bodies] == [
        {"kind": "refresh", "job_id": body["id"], "previous_book_id": body["result"]["book_id"]}
    ]


async def test_correction_with_the_queue_down_reverts_to_done_and_answers_503(
    client: AsyncClient, queue_worker: QueueWorker, job_queue: InMemoryJobQueue
) -> None:
    headers = await _login(client, "a@example.com")
    body = await _scan(client, headers, queue_worker)
    job_queue.fail_send = True

    failed = await client.patch(
        f"/jobs/{body['id']}/correction", headers=headers, json={"title": "Dune Messiah"}
    )
    job_queue.fail_send = False
    retried = await client.patch(
        f"/jobs/{body['id']}/correction", headers=headers, json={"title": "Dune Messiah"}
    )

    assert failed.status_code == 503
    assert retried.status_code == 200, "a job left `running` forever could not be retried"


# --- serving the photo -----------------------------------------------------


async def test_the_owner_gets_their_photo_and_it_is_private(
    client: AsyncClient, queue_worker: QueueWorker, blob_store: InMemoryBlobStore
) -> None:
    headers = await _login(client, "a@example.com")
    body = await _scan(client, headers, queue_worker)
    book_id = _book_id(body)

    response = await client.get(f"/books/{book_id}/library/photo", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"].startswith("private")
    assert response.content == next(iter(blob_store.blobs.values()))


async def test_another_reader_cannot_fetch_the_photo(
    client: AsyncClient, queue_worker: QueueWorker
) -> None:
    alice = await _login(client, "alice@example.com")
    bob = await _login(client, "bob@example.com")
    book_id = _book_id(await _scan(client, alice, queue_worker))

    # Bob has no entry for the book: indistinguishable from nobody having one.
    foreign = await client.get(f"/books/{book_id}/library/photo", headers=bob)
    anonymous = await client.get(f"/books/{book_id}/library/photo")

    assert foreign.status_code == 404
    assert anonymous.status_code == 401


async def test_each_reader_gets_their_own_photo_of_the_same_book(
    client: AsyncClient, queue_worker: QueueWorker
) -> None:
    alice = await _login(client, "alice@example.com")
    bob = await _login(client, "bob@example.com")
    alice_photo = _jpeg(200, 300)
    bob_photo = _jpeg(240, 360)
    book_id = _book_id(await _scan(client, alice, queue_worker, alice_photo))
    await _scan(client, bob, queue_worker, bob_photo)

    seen_by_alice = await client.get(f"/books/{book_id}/library/photo", headers=alice)
    seen_by_bob = await client.get(f"/books/{book_id}/library/photo", headers=bob)

    assert seen_by_alice.content != seen_by_bob.content


async def test_no_entry_and_no_photo_are_both_404(
    client: AsyncClient,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")
    book_id = _book_id(await _scan(client, headers, queue_worker))
    async with db_session_factory() as db:
        entry = await db.scalar(select(LibraryEntry))
        assert entry is not None
        entry.photo_blob = None  # an entry from before photos were kept
        await db.commit()

    without_photo = await client.get(f"/books/{book_id}/library/photo", headers=headers)
    unknown_book = await client.get("/books/9999/library/photo", headers=headers)

    assert without_photo.status_code == 404
    assert unknown_book.status_code == 404


async def test_has_photo_is_exposed_but_the_blob_name_is_not(
    client: AsyncClient, queue_worker: QueueWorker, blob_store: InMemoryBlobStore
) -> None:
    headers = await _login(client, "a@example.com")
    book_id = _book_id(await _scan(client, headers, queue_worker))

    entry = (await client.get(f"/books/{book_id}/library", headers=headers)).json()
    listing = (await client.get("/users/me/library", headers=headers)).json()

    assert entry["has_photo"] is True
    assert listing[0]["has_photo"] is True
    blob_name = next(iter(blob_store.blobs))
    assert blob_name not in json.dumps(entry) + json.dumps(listing)


# --- lifecycle -------------------------------------------------------------


async def test_removing_the_library_entry_deletes_its_photo(
    client: AsyncClient,
    queue_worker: QueueWorker,
    blob_store: InMemoryBlobStore,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")
    body = await _scan(client, headers, queue_worker)
    book_id = _book_id(body)
    assert len(blob_store.blobs) == 1

    response = await client.delete(f"/books/{book_id}/library", headers=headers)

    assert response.status_code == 204
    assert blob_store.blobs == {}
    async with db_session_factory() as db:
        job = await db.get(Job, body["id"])
        assert job is not None and job.photo_blob is None, "no dangling pointer on the job"


async def test_removing_the_entry_still_works_when_blob_cleanup_fails(
    client: AsyncClient, queue_worker: QueueWorker, blob_store: InMemoryBlobStore
) -> None:
    headers = await _login(client, "a@example.com")
    book_id = _book_id(await _scan(client, headers, queue_worker))
    blob_store.fail_delete = True

    response = await client.delete(f"/books/{book_id}/library", headers=headers)

    # The entry is gone; the leftover file is merely an unreferenced orphan.
    assert response.status_code == 204
    assert (await client.get(f"/books/{book_id}/library", headers=headers)).status_code == 404


async def test_a_rescan_replaces_the_photo_and_deletes_the_old_one(
    client: AsyncClient,
    queue_worker: QueueWorker,
    blob_store: InMemoryBlobStore,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = await _login(client, "a@example.com")
    first = await _scan(client, headers, queue_worker, _jpeg(200, 300))
    (first_blob,) = blob_store.blobs

    second = await _scan(client, headers, queue_worker, _jpeg(220, 330))

    assert first_blob not in blob_store.blobs, "the superseded photo is deleted"
    assert len(blob_store.blobs) == 1
    async with db_session_factory() as db:
        entry = await db.scalar(select(LibraryEntry))
        assert entry is not None and entry.photo_blob in blob_store.blobs
        old_job = await db.get(Job, first["id"])
        assert old_job is not None and old_job.photo_blob is None
        new_job = await db.get(Job, second["id"])
        assert new_job is not None and new_job.photo_blob == entry.photo_blob


# --- correction moves the photo with the job -------------------------------


def _fetcher_for(title: str, author: str) -> BookDataFetcher:
    return BookDataFetcher(
        sources=[
            FakeContentSource(
                SourceName.GOOGLE_BOOKS,
                SourceResult(
                    source=SourceName.GOOGLE_BOOKS,
                    metadata=BookMetadata(title=title, author=author),
                    passages=[SourcePassage(kind=SourceKind.DESCRIPTION, content="Un roman.")],
                ),
            )
        ],
        settings=get_settings(),
    )


class _Vision:
    def __init__(self, title: str, author: str) -> None:
        self._identification = CoverIdentification(
            title=title, author=author, confidence=0.95, method="ocr", needs_review=False
        )

    async def identify(self, image_bytes: bytes) -> CoverIdentification:
        return self._identification


async def _scanned_dune_with_photo(
    factory: async_sessionmaker[AsyncSession], blobs: InMemoryBlobStore
) -> tuple[int, int, str]:
    """A done Dune scan whose photo is on the job and on the reader's entry."""
    async with factory() as db:
        user = User(email="pipeline@example.com", hashed_password="x")
        db.add(user)
        await db.flush()
        job = Job(user_id=user.id, status=JobStatus.PENDING.value, photo_blob="p1.jpg")
        db.add(job)
        await db.commit()
        user_id, job_id = user.id, job.id
    blobs.blobs["p1.jpg"] = _jpeg()
    await process_cover(
        job_id,
        b"unused",
        factory,
        _Vision("Dune", "Frank Herbert"),  # type: ignore[arg-type]
        _fetcher_for("Dune", "Frank Herbert"),
        blobs,
    )
    return user_id, job_id, "p1.jpg"


async def _book_id_of(factory: async_sessionmaker[AsyncSession], job_id: int) -> int | None:
    """The book the job's scan resolved to (what the endpoint sends as `previous_book_id`)."""
    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        book_id = job.result["book_id"]
        return book_id if isinstance(book_id, int) else None


async def test_a_correction_moves_the_photo_to_the_corrected_book(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    user_id, job_id, photo = await _scanned_dune_with_photo(db_session_factory, blob_store)

    previous = await _book_id_of(db_session_factory, job_id)
    await refresh_book_data(
        job_id,
        "Baltagul",
        "Mihail Sadoveanu",
        db_session_factory,
        _fetcher_for("Baltagul", "Mihail Sadoveanu"),
        blob_store,
        previous_book_id=previous,
    )

    # The misidentified Dune entry was a scan artifact: gone. Its photo is
    # *not* deleted, because the corrected book's entry now owns it.
    assert photo in blob_store.blobs
    async with db_session_factory() as db:
        entries = list(await db.scalars(select(LibraryEntry)))
        assert len(entries) == 1
        assert entries[0].photo_blob == photo
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        assert entries[0].book_id == job.result["book_id"]


async def test_a_correction_leaves_a_rated_book_without_the_photo_of_another(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    user_id, job_id, photo = await _scanned_dune_with_photo(db_session_factory, blob_store)
    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        dune_id = job.result["book_id"]
        await library_service.update_entry(db, user_id, dune_id, LibraryEntryUpdate(rating=5))

    previous = await _book_id_of(db_session_factory, job_id)
    await refresh_book_data(
        job_id,
        "Baltagul",
        "Mihail Sadoveanu",
        db_session_factory,
        _fetcher_for("Baltagul", "Mihail Sadoveanu"),
        blob_store,
        previous_book_id=previous,
    )

    assert photo in blob_store.blobs
    async with db_session_factory() as db:
        entries = {e.book.title: e for e in await db.scalars(select(LibraryEntry))}
        assert set(entries) == {"Dune", "Baltagul"}, "the rated Dune stays (it is theirs)"
        assert entries["Dune"].photo_blob is None, "the photo shows the corrected book"
        assert entries["Baltagul"].photo_blob == photo


async def test_discarding_an_entry_with_an_older_photo_deletes_that_photo(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    user_id, job_id, photo = await _scanned_dune_with_photo(db_session_factory, blob_store)
    # The artifact entry also held some other, older photo of its own.
    blob_store.blobs["older.jpg"] = _jpeg()
    async with db_session_factory() as db:
        entry = await db.scalar(select(LibraryEntry))
        assert entry is not None
        entry.photo_blob = "older.jpg"
        await db.commit()

    previous = await _book_id_of(db_session_factory, job_id)
    await refresh_book_data(
        job_id,
        "Baltagul",
        "Mihail Sadoveanu",
        db_session_factory,
        _fetcher_for("Baltagul", "Mihail Sadoveanu"),
        blob_store,
        previous_book_id=previous,
    )

    assert "older.jpg" not in blob_store.blobs, "its owner entry was discarded"
    assert photo in blob_store.blobs


async def test_failing_a_job_never_deletes_a_photo_a_library_entry_owns(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    """A pointer never dangles: the job's pointer goes, the entry's blob stays."""
    _, job_id, photo = await _scanned_dune_with_photo(db_session_factory, blob_store)

    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.photo_blob == photo
        await fail_job(db, job, "boom", blob_store)

    assert photo in blob_store.blobs
    async with db_session_factory() as db:
        entry = await db.scalar(select(LibraryEntry))
        assert entry is not None and entry.photo_blob == photo
        job = await db.get(Job, job_id)
        assert job is not None and job.photo_blob is None


async def test_correcting_an_old_scan_keeps_an_entry_that_was_scanned_again(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    user_id, first_job, photo = await _scanned_dune_with_photo(db_session_factory, blob_store)
    # Days later the reader deliberately scans the same Dune again.
    async with db_session_factory() as db:
        job = Job(user_id=user_id, status=JobStatus.PENDING.value, photo_blob="p2.jpg")
        db.add(job)
        await db.commit()
        second_job = job.id
    blob_store.blobs["p2.jpg"] = _jpeg()
    await process_cover(
        second_job,
        b"unused",
        db_session_factory,
        _Vision("Dune", "Frank Herbert"),  # type: ignore[arg-type]
        _fetcher_for("Dune", "Frank Herbert"),
        blob_store,
    )

    # Now the FIRST scan's job is corrected: it was really Baltagul.
    previous = await _book_id_of(db_session_factory, first_job)
    await refresh_book_data(
        first_job,
        "Baltagul",
        "Mihail Sadoveanu",
        db_session_factory,
        _fetcher_for("Baltagul", "Mihail Sadoveanu"),
        blob_store,
        previous_book_id=previous,
    )

    async with db_session_factory() as db:
        entries = {e.book.title: e for e in await db.scalars(select(LibraryEntry))}
    assert set(entries) == {"Dune", "Baltagul"}, "the later, legitimate Dune scan survives"
    assert entries["Dune"].scan_count == 1, "only the wrong scan was uncounted"
    assert entries["Dune"].photo_blob == "p2.jpg"
    assert "p2.jpg" in blob_store.blobs
    # The first scan's photo was already replaced (and deleted) by the second
    # scan's, per the one-photo-per-entry rule, so there is nothing to move.
    assert photo not in blob_store.blobs
    assert entries["Baltagul"].photo_blob is None


async def test_attach_photo_waits_for_a_concurrent_writer_of_the_same_entry(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    """`attach_photo` locks the entry, so two queue workers cannot lose an update."""
    user_id, job_id, _ = await _scanned_dune_with_photo(db_session_factory, blob_store)
    book_id = await _book_id_of(db_session_factory, job_id)
    assert book_id is not None

    async with db_session_factory() as holder:
        # Another queue worker is mid-update on the same entry.
        locked = await library_service._get_entry(holder, user_id, book_id, for_update=True)
        assert locked is not None

        async def _attach() -> str | None:
            async with db_session_factory() as other:
                return await library_service.attach_photo(other, user_id, book_id, "p9.jpg")

        attach = asyncio.create_task(_attach())
        done, _ = await asyncio.wait({attach}, timeout=0.7)
        assert not done, "blocked on the row lock instead of racing past it"

        await holder.commit()

    assert await asyncio.wait_for(attach, timeout=5) == "p1.jpg", "saw the committed pointer"

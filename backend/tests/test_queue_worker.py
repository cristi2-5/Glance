"""Tests for `QueueWorker`: delivery semantics, idempotency, poison, photo cleanup.

The queue worker is exercised through `process_one()` against in-memory fakes
of the queue and blob store (the real adapters are covered against Azurite in
`test_storage.py`). The pipeline underneath is the real one, over a scripted
catalog, on the real test database.
"""

import asyncio
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import CoverNotRecognized, StorageUnavailable
from app.models.job import Job, JobStatus
from app.models.library import LibraryEntry
from app.models.user import User
from app.schemas.vision import CoverIdentification
from app.services.storage import JobKind, JobMessage
from app.workers import queue_worker as queue_worker_module
from app.workers.queue_worker import PHOTO_MISSING_ERROR, POISON_ERROR, QueueWorker
from tests.conftest import _build_fake_data_fetcher, _build_fake_vision_service
from tests.fakes import InMemoryBlobStore, InMemoryJobQueue


def _jpeg() -> bytes:
    """A real, decodable JPEG: the pipeline preprocesses the stored photo for real."""
    buffer = BytesIO()
    Image.new("RGB", (120, 180), color=(30, 90, 150)).save(buffer, format="JPEG")
    return buffer.getvalue()


PHOTO = _jpeg()


async def _make_job(
    session_factory: async_sessionmaker[AsyncSession],
    blobs: InMemoryBlobStore,
    *,
    status: JobStatus = JobStatus.PENDING,
    photo: str | None = "photo-1.jpg",
    store_photo: bool = True,
) -> int:
    """Creates a job (and its stored photo) the way `POST /analyze-cover` would."""
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == "worker@example.com"))
        if user is None:
            user = User(email="worker@example.com", hashed_password="x")
            db.add(user)
            await db.flush()
        job = Job(user_id=user.id, status=status.value, photo_blob=photo)
        db.add(job)
        await db.commit()
        job_id = job.id
    if photo is not None and store_photo:
        blobs.blobs[photo] = PHOTO
    return job_id


async def _job(session_factory: async_sessionmaker[AsyncSession], job_id: int) -> Job:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


async def _enqueue(queue: InMemoryJobQueue, job_id: int, kind: JobKind = JobKind.SCAN) -> None:
    await queue.send(JobMessage(kind=kind, job_id=job_id))


class _RaisingVision:
    """A vision service whose reading always fails with a domain error."""

    async def identify(self, image_bytes: bytes) -> CoverIdentification:
        raise CoverNotRecognized("Could not identify the book from the cover image.")


async def test_an_empty_queue_is_not_work(queue_worker: QueueWorker) -> None:
    assert await queue_worker.process_one() is False


async def test_a_scan_message_runs_the_pipeline_and_is_deleted(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    assert await queue_worker.process_one() is True

    job = await _job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE.value
    assert job.result is not None and job.result["title"] == "Dune"
    assert job_queue.bodies == [], "a finished job's message is deleted"
    # The photo is kept: it now belongs to the reader's library entry.
    assert blob_store.blobs == {"photo-1.jpg": PHOTO}
    async with db_session_factory() as db:
        entry = await db.scalar(select(LibraryEntry))
        assert entry is not None and entry.photo_blob == "photo-1.jpg"


async def test_the_pipeline_receives_the_stored_photo(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    queue_worker: QueueWorker,
) -> None:
    seen: list[bytes] = []

    async def _spy(job_id: int, image: bytes, *args: object) -> None:
        seen.append(image)

    monkeypatch.setattr(queue_worker_module, "process_cover", _spy)
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await queue_worker.process_one()

    assert seen == [PHOTO]


async def test_a_duplicate_message_for_a_finished_job_is_dropped_not_rerun(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    await queue_worker.process_one()

    await _enqueue(job_queue, job_id)  # at-least-once: the same job again
    assert await queue_worker.process_one() is True

    assert job_queue.bodies == []
    async with db_session_factory() as db:
        entry = await db.scalar(select(LibraryEntry))
        # A re-run would have recorded a second scan.
        assert entry is not None and entry.scan_count == 1


async def test_a_running_job_is_picked_up_again_after_a_crash(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    # The previous queue worker set `running`, then died before finishing.
    job_id = await _make_job(db_session_factory, blob_store, status=JobStatus.RUNNING)
    await _enqueue(job_queue, job_id)

    await queue_worker.process_one()

    assert (await _job(db_session_factory, job_id)).status == JobStatus.DONE.value


async def test_a_message_for_a_missing_job_is_logged_and_dropped(
    queue_worker: QueueWorker, job_queue: InMemoryJobQueue
) -> None:
    await _enqueue(job_queue, 999)

    assert await queue_worker.process_one() is True

    assert job_queue.bodies == []


async def test_a_malformed_message_is_dropped(
    queue_worker: QueueWorker, job_queue: InMemoryJobQueue
) -> None:
    job_queue.send_raw("this is not json")
    job_queue.send_raw('{"kind": "scan", "job_id": "seven", "extra": 1}')

    assert await queue_worker.process_one() is True
    assert await queue_worker.process_one() is True

    assert job_queue.bodies == []


async def test_a_message_delivered_too_often_is_poison(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    # `job_max_attempts` deliveries that never settled the message...
    for _ in range(get_settings().job_max_attempts):
        assert await job_queue.receive(30) is not None
        job_queue.expire_visibility()

    # ...so the next one is dropped instead of tried again.
    assert await queue_worker.process_one() is True

    job = await _job(db_session_factory, job_id)
    assert job.status == JobStatus.FAILED.value
    assert job.error == POISON_ERROR
    assert job_queue.bodies == []
    assert blob_store.blobs == {}, "a failed scan's photo is deleted"


async def test_a_crash_leaves_the_message_for_a_later_delivery(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _explode(*args: object) -> None:
        raise RuntimeError("database on fire")

    monkeypatch.setattr(queue_worker_module, "process_cover", _explode)
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    assert await queue_worker.process_one() is True

    # Not final, so not deleted: it comes back once the visibility timeout passes.
    assert len(job_queue.bodies) == 1
    assert (await _job(db_session_factory, job_id)).status == JobStatus.PENDING.value
    job_queue.expire_visibility()
    redelivered = await job_queue.receive(30)
    assert redelivered is not None and redelivered.dequeue_count == 2


async def test_a_failed_scan_deletes_its_photo(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_RaisingVision(),  # type: ignore[arg-type]
        data_fetcher=_build_fake_data_fetcher(),
        settings=get_settings(),
    )
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    job = await _job(db_session_factory, job_id)
    assert job.status == JobStatus.FAILED.value
    assert job.error == "Could not identify the book from the cover image."
    assert job.photo_blob is None
    assert blob_store.blobs == {}
    assert job_queue.bodies == [], "failed is final: the message is not retried"


async def test_a_missing_photo_blob_fails_the_job(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store, store_photo=False)
    await _enqueue(job_queue, job_id)

    await queue_worker.process_one()

    job = await _job(db_session_factory, job_id)
    assert job.status == JobStatus.FAILED.value
    assert job.error == PHOTO_MISSING_ERROR
    assert job_queue.bodies == []


async def test_storage_being_down_leaves_the_message_on_the_queue(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    blob_store.fail = True

    with pytest.raises(StorageUnavailable):
        await queue_worker.process_one()

    # Not failed: the photo exists, storage is merely down. Retried later.
    assert (await _job(db_session_factory, job_id)).status == JobStatus.PENDING.value
    assert len(job_queue.bodies) == 1


async def test_a_refresh_message_refetches_the_corrected_title(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    await queue_worker.process_one()

    # What `PATCH /jobs/{id}/correction` leaves behind before enqueueing.
    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None and job.result is not None
        job.result = {**job.result, "title": "Dune Messiah", "author": "Frank Herbert"}
        job.status = JobStatus.RUNNING.value
        await db.commit()
    await _enqueue(job_queue, job_id, JobKind.REFRESH)

    assert await queue_worker.process_one() is True

    job = await _job(db_session_factory, job_id)
    assert job.status == JobStatus.DONE.value
    assert job.result is not None and job.result["title"] == "Dune Messiah"
    assert job.result["metadata_found"] is True
    assert job_queue.bodies == []


async def test_a_refresh_for_a_job_that_is_not_running_is_a_duplicate(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    await queue_worker.process_one()
    before = (await _job(db_session_factory, job_id)).result

    await _enqueue(job_queue, job_id, JobKind.REFRESH)
    await queue_worker.process_one()

    assert (await _job(db_session_factory, job_id)).result == before
    assert job_queue.bodies == []


async def test_a_poisoned_refresh_keeps_the_photo_the_library_owns(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    await queue_worker.process_one()
    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING.value
        await db.commit()
    await _enqueue(job_queue, job_id, JobKind.REFRESH)
    for _ in range(get_settings().job_max_attempts):
        assert await job_queue.receive(30) is not None
        job_queue.expire_visibility()

    await queue_worker.process_one()

    assert (await _job(db_session_factory, job_id)).status == JobStatus.FAILED.value
    assert blob_store.blobs == {"photo-1.jpg": PHOTO}, "the entry still shows it"


async def test_run_polls_until_stopped(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    settings = get_settings().model_copy(
        update={"queue_idle_backoff_min_seconds": 0.01, "queue_idle_backoff_max_seconds": 0.02}
    )
    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_build_fake_vision_service(),
        data_fetcher=_build_fake_data_fetcher(),
        settings=settings,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)  # idle for a few polls

    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    for _ in range(200):
        if (await _job(db_session_factory, job_id)).status == JobStatus.DONE.value:
            break
        await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(task, timeout=5)

    assert (await _job(db_session_factory, job_id)).status == JobStatus.DONE.value


async def test_a_redelivered_scan_message_does_not_undo_a_correction(
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    # A correction leaves the job `running` with the reader's title in `result`.
    # An old SCAN message arriving now must not re-run the scan over it.
    job_id = await _make_job(db_session_factory, blob_store, status=JobStatus.RUNNING)
    async with db_session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        job.result = {"title": "Corrected by the reader", "author": None, "book_id": None}
        await db.commit()
    await _enqueue(job_queue, job_id, JobKind.SCAN)

    assert await queue_worker.process_one() is True

    after = await _job(db_session_factory, job_id)
    assert after.status == JobStatus.RUNNING.value, "waiting for its refresh, untouched"
    assert after.result is not None and after.result["title"] == "Corrected by the reader"
    assert job_queue.bodies == [], "the stale scan message is deleted"


class _SlowVision:
    """Takes longer than the visibility timeout, like a slow catalog or model."""

    async def identify(self, image_bytes: bytes) -> CoverIdentification:
        await asyncio.sleep(1.3)
        return CoverIdentification(
            title="Dune", author="Frank Herbert", confidence=0.9, method="ocr", needs_review=False
        )


async def test_a_long_job_keeps_its_message_and_deletes_with_the_newest_receipt(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    settings = get_settings().model_copy(update={"job_visibility_timeout_seconds": 1})
    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_SlowVision(),  # type: ignore[arg-type]
        data_fetcher=_build_fake_data_fetcher(),
        settings=settings,
    )
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    assert job_queue.extended, "the heartbeat renewed the message while the job ran"
    assert all(timeout == 1 for _, timeout in job_queue.extended)
    # The fake (like Azure) ignores a delete made with a stale receipt, so an
    # empty queue proves the delete used the newest one.
    assert job_queue.bodies == []
    assert (await _job(db_session_factory, job_id)).status == JobStatus.DONE.value


async def test_a_failing_heartbeat_does_not_fail_the_job(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    settings = get_settings().model_copy(update={"job_visibility_timeout_seconds": 1})
    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_SlowVision(),  # type: ignore[arg-type]
        data_fetcher=_build_fake_data_fetcher(),
        settings=settings,
    )
    job_queue.fail_extend = True
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    assert (await _job(db_session_factory, job_id)).status == JobStatus.DONE.value
    assert job_queue.bodies == []

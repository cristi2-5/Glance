"""Tests for the Azure Blob and Queue adapters, against the real emulator (Azurite).

These deliberately do not use a fake. The design depends on specific queue
semantics — a received message turns invisible, and if nobody deletes it, it
reappears with a higher `dequeue_count` — and a fake could only restate the
assumption. Every test works in a container/queue of its own (unique name,
removed afterwards), so the tests cannot see each other's messages.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import get_settings
from app.core.exceptions import BlobNotFound, StorageUnavailable
from app.services.storage import (
    AzureBlobStore,
    AzureJobQueue,
    JobKind,
    JobMessage,
    QueuedMessage,
)

CONNECTION = get_settings().azure_storage_connection_string


@pytest.fixture
async def blobs() -> AsyncIterator[AzureBlobStore]:
    """A blob store on a container that exists only for this test."""
    store = AzureBlobStore(CONNECTION, f"test-{uuid.uuid4().hex}")
    try:
        yield store
    finally:
        await store._service.delete_container(store._container)
        await store.close()


@pytest.fixture
async def queue() -> AsyncIterator[AzureJobQueue]:
    """A job queue that exists only for this test."""
    jobs = AzureJobQueue(CONNECTION, f"test-{uuid.uuid4().hex}")
    try:
        yield jobs
    finally:
        await jobs._service.delete_queue(jobs._queue)
        await jobs.close()


async def test_blob_round_trip_and_overwrite(blobs: AzureBlobStore) -> None:
    await blobs.put("a.jpg", b"first")
    assert await blobs.get("a.jpg") == b"first"

    await blobs.put("a.jpg", b"second")
    assert await blobs.get("a.jpg") == b"second"


async def test_blob_container_is_private(blobs: AzureBlobStore) -> None:
    await blobs.put("a.jpg", b"x")

    container = blobs._service.get_container_client(blobs._container)
    properties = await container.get_container_properties()

    # `None` is the service's way of saying "no anonymous access".
    assert properties.public_access is None


async def test_blob_get_missing_raises_blob_not_found(blobs: AzureBlobStore) -> None:
    with pytest.raises(BlobNotFound):
        await blobs.get("never-stored.jpg")


async def test_blob_delete_removes_it_and_tolerates_a_missing_one(blobs: AzureBlobStore) -> None:
    await blobs.put("a.jpg", b"x")

    await blobs.delete("a.jpg")
    await blobs.delete("a.jpg")  # already gone: not an error

    with pytest.raises(BlobNotFound):
        await blobs.get("a.jpg")


async def test_unreachable_storage_is_storage_unavailable() -> None:
    # Nothing listens on port 1. `retry_total=0` keeps the SDK from backing
    # off for the better part of a minute before giving up.
    dead = CONNECTION.replace(":10000/", ":1/").replace(":10001/", ":1/")
    store = AzureBlobStore(dead, "scans", retry_total=0, connection_timeout=2)
    jobs = AzureJobQueue(dead, "cover-jobs", retry_total=0, connection_timeout=2)
    try:
        with pytest.raises(StorageUnavailable):
            await store.put("a.jpg", b"x")
        with pytest.raises(StorageUnavailable):
            await jobs.send(JobMessage(kind=JobKind.SCAN, job_id=1))
    finally:
        await store.close()
        await jobs.close()


async def test_queue_send_receive_delete(queue: AzureJobQueue) -> None:
    assert await queue.receive(30) is None, "a new queue is empty"

    await queue.send(JobMessage(kind=JobKind.REFRESH, job_id=7))
    received = await queue.receive(30)

    assert received is not None
    assert JobMessage.model_validate_json(received.body) == JobMessage(
        kind=JobKind.REFRESH, job_id=7
    )
    assert received.dequeue_count == 1
    # Invisible while held by a consumer...
    assert await queue.receive(30) is None

    await queue.delete(received)
    # ...and gone for good once deleted, even after the timeout would have expired.
    assert await queue.receive(1) is None


async def test_undeleted_message_reappears_after_visibility_timeout(
    queue: AzureJobQueue,
) -> None:
    """The at-least-once guarantee the whole retry design rests on.

    A queue worker that dies mid-job never deletes its message; the message
    must come back (so the job is retried) and say it has been delivered
    before (so a poison message can be recognised).
    """
    await queue.send(JobMessage(kind=JobKind.SCAN, job_id=3))

    first = await queue.receive(1)
    assert first is not None and first.dequeue_count == 1
    assert await queue.receive(1) is None, "invisible within its visibility timeout"

    await asyncio.sleep(1.5)

    second = await queue.receive(30)
    assert second is not None
    assert second.message_id == first.message_id
    assert second.dequeue_count == 2
    assert second.pop_receipt != first.pop_receipt

    # The first consumer waking up late holds a stale receipt: deleting with
    # it must not raise, and must not delete the second consumer's message.
    await queue.delete(first)
    assert await queue.receive(1) is None, "still held by the second consumer"
    await queue.delete(second)


async def test_extend_keeps_the_message_invisible_and_delete_needs_the_new_receipt(
    queue: AzureJobQueue,
) -> None:
    await queue.send(JobMessage(kind=JobKind.SCAN, job_id=5))
    held = await queue.receive(1)
    assert held is not None

    new_receipt = await queue.extend(held, 3)
    assert new_receipt != held.pop_receipt, "Azure issues a new receipt per update"

    await asyncio.sleep(1.5)  # past the original 1 s timeout
    assert await queue.receive(1) is None, "still invisible thanks to the extension"

    renewed = QueuedMessage(held.message_id, new_receipt, held.body, held.dequeue_count)
    await queue.delete(renewed)
    await asyncio.sleep(2.5)  # past the extended timeout too
    assert await queue.receive(1) is None, "deleted with the newest receipt, so it never returns"


async def test_message_without_previous_book_id_stays_two_keys(queue: AzureJobQueue) -> None:
    await queue.send(JobMessage(kind=JobKind.SCAN, job_id=9))
    received = await queue.receive(30)
    assert received is not None
    assert json.loads(received.body) == {"kind": "scan", "job_id": 9}


def test_job_message_rejects_unknown_kind_and_bad_ids_but_ignores_unknown_fields() -> None:
    with pytest.raises(ValueError):
        JobMessage.model_validate_json('{"kind": "explode", "job_id": 1}')
    # Unknown fields come from a newer api during a rolling deploy: ignored,
    # not grounds for dropping the message.
    parsed = JobMessage.model_validate_json('{"kind": "scan", "job_id": 1, "user_email": "a@b.c"}')
    assert (parsed.kind, parsed.job_id) == (JobKind.SCAN, 1)
    assert not hasattr(parsed, "user_email")
    with pytest.raises(ValueError):
        JobMessage.model_validate_json('{"kind": "scan", "job_id": 0}')

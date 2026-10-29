"""Blob and Queue Storage: where scan photos live and how jobs reach the queue worker.

Until cloud step 4 the uploaded photo sat in the api process's memory and the
whole pipeline ran in a FastAPI background task. A restart or scale-in
mid-scan lost the photo and left the job `running` forever. Now the api
writes the photo to a private **Blob** container and puts a tiny message on a
**Queue**; a separate queue worker process takes it from there.

Both services sit behind a `Protocol` (`BlobStore`, `JobQueue`), like every
external service here, so the queue worker and the routes are tested against
in-memory fakes. The Azure adapters themselves are tested against the real
emulator (Azurite): a fake would only assume exactly the behaviour this design
depends on (a received message turns invisible, then reappears with a higher
`dequeue_count` if nobody deletes it).

The same SDK talks to Azurite locally and to Azure Storage in the cloud; only
`Settings.azure_storage_connection_string` changes.

**Messages carry ids only** (`{"kind": "scan", "job_id": 7}`). The photo's
blob name is on the job row, a correction's title is already in `job.result`,
and nothing about the user travels through a queue whose messages are visible
to anyone with access to the storage account.
"""

import asyncio
import enum
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import structlog
from azure.core.exceptions import (
    AzureError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient, ContainerClient
from azure.storage.queue.aio import QueueClient, QueueServiceClient
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.exceptions import BlobNotFound, StorageUnavailable

logger = structlog.get_logger(__name__)


class JobKind(enum.StrEnum):
    """What the queue worker should do for a job."""

    #: Analyse a freshly uploaded cover (`process_cover`).
    SCAN = "scan"
    #: Re-fetch a corrected job's book data (`refresh_book_data`).
    REFRESH = "refresh"


class JobMessage(BaseModel):
    """The body of a queue message: which job, and what to do for it.

    Validated with pydantic on receive, because a queue is an input like any
    other: a message that does not parse is *poison* (retrying cannot fix it)
    and the queue worker drops it instead of crashing on it forever.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: JobKind
    job_id: int = Field(ge=1)
    # Refresh only: the book the scan was first (mis)identified as. The
    # correction endpoint clears `book_id` from `job.result` before queueing,
    # so the message is the one place it survives; without it the misread
    # book's untouched library entry was never discarded.
    previous_book_id: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class QueuedMessage:
    """One message handed out by `JobQueue.receive`.

    Attributes:
        message_id: The queue's id for the message.
        pop_receipt: Proof of this particular receive; required to delete.
            A later receive of the same message has a different one.
        body: The raw message text (parsed by the caller, see `JobMessage`).
        dequeue_count: How many times the queue has handed this message out,
            this time included. The poison-message signal.
    """

    message_id: str
    pop_receipt: str
    body: str
    dequeue_count: int


class BlobStore(Protocol):
    """Private object storage for scan photos."""

    async def put(self, name: str, data: bytes, content_type: str = "image/jpeg") -> None:
        """Stores `data` under `name`, replacing any existing blob.

        Raises:
            StorageUnavailable: If the storage service could not be used.
        """
        ...

    async def get(self, name: str) -> bytes:
        """Returns the content of the blob called `name`.

        Raises:
            BlobNotFound: If there is no such blob.
            StorageUnavailable: If the storage service could not be used.
        """
        ...

    async def delete(self, name: str) -> None:
        """Deletes the blob called `name`; deleting a missing blob is not an error.

        Raises:
            StorageUnavailable: If the storage service could not be used.
        """
        ...


class JobQueue(Protocol):
    """A durable queue with at-least-once delivery."""

    async def send(self, message: JobMessage) -> None:
        """Puts a message on the queue.

        Raises:
            StorageUnavailable: If the queue could not be reached.
        """
        ...

    async def receive(self, visibility_timeout: int) -> QueuedMessage | None:
        """Takes one message, hiding it from other consumers for a while.

        Args:
            visibility_timeout: Seconds the message stays invisible. If it
                is not deleted by then it is handed out again, with a higher
                `dequeue_count`.

        Returns:
            The message, or `None` when the queue is empty.

        Raises:
            StorageUnavailable: If the queue could not be reached.
        """
        ...

    async def extend(self, message: QueuedMessage, visibility_timeout: int) -> str:
        """Keeps a received message invisible for another `visibility_timeout` seconds.

        Azure issues a **new pop receipt** on every update and invalidates
        the old one, so the returned value, not the one in `message`, is what
        a later `delete` must be given.

        Args:
            message: The message being worked on (with its newest receipt).
            visibility_timeout: Seconds, counted from now.

        Returns:
            The new pop receipt.

        Raises:
            StorageUnavailable: If the queue could not be reached, or the
                receipt is stale (someone else holds the message now).
        """
        ...

    async def delete(self, message: QueuedMessage) -> None:
        """Permanently removes a received message (job finished or dropped).

        Raises:
            StorageUnavailable: If the queue could not be reached.
        """
        ...


class AzureBlobStore:
    """`BlobStore` over Azure Blob Storage (or Azurite) with a private container.

    The container is created on first use and never made public: photos are
    served to their owner by the api (`GET /books/{id}/library/photo`), never
    by URL or SAS link.
    """

    def __init__(self, connection_string: str, container: str, **client_options: Any) -> None:
        """Creates the adapter; no network call happens until first use.

        Args:
            connection_string: Azure Storage (or Azurite) connection string.
            container: Name of the private container for scan photos.
            **client_options: Passed to the SDK client (e.g. `retry_total`).
        """
        self._service = BlobServiceClient.from_connection_string(
            connection_string, **client_options
        )
        self._container = container
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def _container_client(self) -> ContainerClient:
        """Returns the container client, creating the container if missing."""
        container = self._service.get_container_client(self._container)
        if not self._ready:
            async with self._ready_lock:
                if not self._ready:
                    try:
                        await container.create_container()
                    except ResourceExistsError:
                        pass
                    except AzureError as exc:
                        raise StorageUnavailable("Blob storage is unavailable.") from exc
                    self._ready = True
        return container

    async def put(self, name: str, data: bytes, content_type: str = "image/jpeg") -> None:
        """Stores `data` under `name`. See `BlobStore.put`."""
        container = await self._container_client()
        try:
            await container.upload_blob(
                name,
                data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        except AzureError as exc:
            logger.warning("blob_put_failed", blob=name, error=str(exc))
            raise StorageUnavailable("Blob storage is unavailable.") from exc

    async def get(self, name: str) -> bytes:
        """Returns the blob's bytes. See `BlobStore.get`."""
        container = await self._container_client()
        try:
            stream = await container.download_blob(name)
            return bytes(await stream.readall())
        except ResourceNotFoundError as exc:
            raise BlobNotFound("The stored photo no longer exists.") from exc
        except AzureError as exc:
            logger.warning("blob_get_failed", blob=name, error=str(exc))
            raise StorageUnavailable("Blob storage is unavailable.") from exc

    async def delete(self, name: str) -> None:
        """Deletes the blob, tolerating a missing one. See `BlobStore.delete`."""
        container = await self._container_client()
        try:
            await container.delete_blob(name)
        except ResourceNotFoundError:
            # Already gone: deletion is idempotent, a retried cleanup must not fail.
            return
        except AzureError as exc:
            logger.warning("blob_delete_failed", blob=name, error=str(exc))
            raise StorageUnavailable("Blob storage is unavailable.") from exc

    async def close(self) -> None:
        """Closes the underlying HTTP session."""
        await self._service.close()


class AzureJobQueue:
    """`JobQueue` over Azure Queue Storage (or Azurite).

    Messages are plain JSON text (`JobMessage`), so what sits on the queue is
    readable in any storage explorer when debugging.
    """

    def __init__(self, connection_string: str, queue: str, **client_options: Any) -> None:
        """Creates the adapter; no network call happens until first use.

        Args:
            connection_string: Azure Storage (or Azurite) connection string.
            queue: Name of the queue carrying job messages.
            **client_options: Passed to the SDK client (e.g. `retry_total`).
        """
        self._service = QueueServiceClient.from_connection_string(
            connection_string, **client_options
        )
        self._queue = queue
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def _queue_client(self) -> QueueClient:
        """Returns the queue client, creating the queue if missing."""
        client = self._service.get_queue_client(self._queue)
        if not self._ready:
            async with self._ready_lock:
                if not self._ready:
                    try:
                        await client.create_queue()
                    except ResourceExistsError:
                        pass
                    except AzureError as exc:
                        raise StorageUnavailable("Queue storage is unavailable.") from exc
                    self._ready = True
        return client

    async def send(self, message: JobMessage) -> None:
        """Enqueues a message. See `JobQueue.send`."""
        client = await self._queue_client()
        try:
            # `exclude_none`: a scan message stays `{"kind", "job_id"}`; only a
            # refresh carries `previous_book_id`.
            await client.send_message(message.model_dump_json(exclude_none=True))
        except AzureError as exc:
            logger.warning("queue_send_failed", job_id=message.job_id, error=str(exc))
            raise StorageUnavailable("Queue storage is unavailable.") from exc

    async def receive(self, visibility_timeout: int) -> QueuedMessage | None:
        """Takes one message, or `None` when empty. See `JobQueue.receive`."""
        client = await self._queue_client()
        try:
            async for received in client.receive_messages(
                messages_per_page=1,
                visibility_timeout=visibility_timeout,
                max_messages=1,
            ):
                return QueuedMessage(
                    message_id=received.id,
                    pop_receipt=received.pop_receipt or "",
                    body=str(received.content),
                    dequeue_count=received.dequeue_count or 1,
                )
        except AzureError as exc:
            logger.warning("queue_receive_failed", error=str(exc))
            raise StorageUnavailable("Queue storage is unavailable.") from exc
        return None

    async def extend(self, message: QueuedMessage, visibility_timeout: int) -> str:
        """Extends a received message's invisibility. See `JobQueue.extend`."""
        client = await self._queue_client()
        try:
            updated = await client.update_message(
                message.message_id,
                pop_receipt=message.pop_receipt,
                visibility_timeout=visibility_timeout,
            )
        except AzureError as exc:
            logger.warning("queue_extend_failed", message_id=message.message_id, error=str(exc))
            raise StorageUnavailable("Queue storage is unavailable.") from exc
        return updated.pop_receipt or message.pop_receipt

    async def delete(self, message: QueuedMessage) -> None:
        """Deletes a received message. See `JobQueue.delete`."""
        client = await self._queue_client()
        try:
            await client.delete_message(message.message_id, message.pop_receipt)
        except ResourceNotFoundError:
            # The message was already deleted (e.g. by the consumer that
            # took it after our visibility timeout ran out).
            logger.warning("queue_message_already_gone", message_id=message.message_id)
        except HttpResponseError as exc:
            error_code = getattr(exc, "error_code", None)
            code = getattr(error_code, "value", error_code)
            if code == "PopReceiptMismatch":
                # The visibility timeout expired and another consumer holds
                # the message now: our receipt is stale, and deleting it
                # would be wrong anyway (that consumer may not be done).
                logger.warning("queue_stale_receipt", message_id=message.message_id)
                return
            logger.warning("queue_delete_failed", message_id=message.message_id, error=str(exc))
            raise StorageUnavailable("Queue storage is unavailable.") from exc
        except AzureError as exc:
            logger.warning("queue_delete_failed", message_id=message.message_id, error=str(exc))
            raise StorageUnavailable("Queue storage is unavailable.") from exc

    async def close(self) -> None:
        """Closes the underlying HTTP session."""
        await self._service.close()


@lru_cache
def get_blob_store() -> AzureBlobStore:
    """Returns the process-wide blob store (one HTTP session, reused).

    Used as a FastAPI dependency and by the queue worker; tests override it
    with an in-memory fake. Close it with `close_storage()` on shutdown.
    """
    settings = get_settings()
    return AzureBlobStore(settings.azure_storage_connection_string, settings.scan_container)


@lru_cache
def get_job_queue() -> AzureJobQueue:
    """Returns the process-wide job queue (one HTTP session, reused).

    Used as a FastAPI dependency and by the queue worker; tests override it
    with an in-memory fake. Close it with `close_storage()` on shutdown.
    """
    settings = get_settings()
    return AzureJobQueue(settings.azure_storage_connection_string, settings.job_queue)


async def close_storage() -> None:
    """Closes the cached storage clients, if they were ever created."""
    for factory in (get_blob_store, get_job_queue):
        if factory.cache_info().currsize:
            await factory().close()
            factory.cache_clear()

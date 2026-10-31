"""The queue worker: takes job messages off the queue and runs the pipeline.

Run as its own process (`python -m app.workers.queue_worker`, the `worker`
compose service): same image as the api, different command, no ingress. The
api only stores the photo and enqueues `{"kind", "job_id"}`; everything slow
(vision, three catalogs, embeddings) happens here, so an api restart or
scale-in can no longer lose a scan, and the api no longer has to be a single
process.

**Delivery is at-least-once, so handling is idempotent.** A message is deleted
only when its job reached a final state. If the queue worker dies mid-job the
message becomes visible again after `job_visibility_timeout_seconds` and
another delivery picks the job up:

| Job state on delivery  | What happens                                              |
|------------------------|-----------------------------------------------------------|
| missing                | logged, message deleted (nothing to do)                   |
| `done` / `failed`      | a duplicate: message deleted, nothing re-run              |
| `pending` / `running`  | (re-)run — the book fetch is cached, the library write is |
|                        | unique per user and book, so running twice is harmless    |

For a *scan* message, a job that already has a `result` also counts as
settled, whatever its status: a scan run writes `result` only at the moment it
reaches `done`, so a `result` on a `running` job means a correction put it
there. Re-running the scan then would overwrite the reader's corrected title
with the vision model's guess. (A *refresh* message is the opposite: `running`
is exactly what it expects.) `running` therefore means either "a scan is in
progress or was interrupted" or "a correction is waiting for its refresh".

**Long jobs keep their message.** The pipeline takes 30-120 s and the
visibility timeout is longer, but a slow catalog can still overrun it. While a
message is being handled, a heartbeat task extends its visibility every half
timeout, so a second queue worker does not pick up a job that is merely slow.
Azure issues a new pop receipt on every extension, so the delete at the end
uses the newest one (`_Lease`). A heartbeat that fails is logged and ignored:
the worst case is the ordinary at-least-once redelivery.

**Hung jobs are cancelled, not just outlived.** The handling of one message runs
inside `asyncio.timeout(job_max_runtime_seconds)`. A timeout is treated like any
unexpected failure (`queue_job_timed_out` is logged, the message stays on the
queue, the attempt limit eventually makes it poison), but the hung coroutine is
actually cancelled, so it cannot keep writing while a second delivery runs.
Blocking work in threads (`asyncio.to_thread`, e.g. OCR) cannot be cancelled;
only the platform restart stops those. Hence the timing rule: liveness
`failureThreshold x periodSeconds` must elapse **before** `job_max_runtime +
(visibility - heartbeat interval)` (the last extension's remaining life, 750 s
with the defaults), so a container stuck in a thread is restarted before its
message becomes visible to another queue worker.

**Shutdown.** SIGTERM stops the polling loop but lets the message in hand run
to its end, which only works if the platform waits long enough: compose's
`stop_grace_period` and the Container App's `terminationGracePeriodSeconds`
must both be at least the worst-case job. If the process is killed anyway,
nothing is lost: the message was never deleted, and it reappears once the last
extension runs out.

**Poison messages.** One that does not parse, or has been delivered more than
`job_max_attempts` times, can never succeed by trying again; left alone it
would crash or stall a queue worker forever. It is dropped, and its job (if
unfinished) is failed with a user-facing error. A pipeline *outcome* such as
an unreadable cover is not a retry case: that already ends the job as
`failed` on the first delivery.

**Liveness (cloud step 5).** Azure cannot tell a frozen queue worker from an
idle one, and it has no HTTP port. So `main` serves a tiny `/health` endpoint
(stdlib `asyncio.start_server`, no new dependency) backed by `WorkerHealth`:
alive when the polling loop ticked recently, or when the message in hand has
been worked on for less than `job_max_runtime_seconds`. A pipeline that hangs
therefore turns the endpoint unhealthy, the platform restarts the container,
and the message -- whose heartbeat stopped extending at the same limit -- is
redelivered. Like the api's `/health` it checks no dependency: a database
outage must not make the platform restart healthy containers in a loop.

The unit tested here is `QueueWorker.process_one`; `run` only adds the polling
loop, backoff and shutdown signals around it.
"""

import asyncio
import contextlib
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.exceptions import BlobNotFound, StorageUnavailable
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.db.session import AsyncSessionLocal, dispose_engine, engine
from app.models.job import Job, JobStatus
from app.services.data_fetcher import BookDataFetcher, build_data_fetcher
from app.services.storage import (
    QUEUE_WORKER_CLIENT_OPTIONS,
    BlobStore,
    JobKind,
    JobMessage,
    JobQueue,
    QueuedMessage,
    close_storage,
    configure_client_options,
    get_blob_store,
    get_job_queue,
)
from app.services.vision_service import VisionService, build_vision_service
from app.workers.cover_pipeline import fail_job, process_cover, refresh_book_data

logger = structlog.get_logger(__name__)

POISON_ERROR = "This scan could not be processed. Please try again."
PHOTO_MISSING_ERROR = "The scan photo is no longer available. Please scan the cover again."


class WorkerHealth:
    """What the liveness endpoint answers: has this queue worker stopped making progress?

    Pure bookkeeping around an injectable clock, so the rules are testable
    without sockets or sleeping. The loop calls `tick`; the message handler
    calls `job_started` / `job_finished`.

    Healthy means *either* the polling loop ticked within
    `queue_worker_loop_stale_seconds` *or* the message in hand started less
    than `job_max_runtime_seconds` ago. The loop cannot tick while a job runs,
    which is why the second clause exists; once the job outlives its limit
    nothing excuses a silent loop and the answer turns unhealthy.
    """

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        """Starts the clock; a queue worker that never ticks goes stale on its own.

        Args:
            settings: Supplies the stale and max-runtime limits.
            clock: A monotonic clock in seconds (injectable for tests).
        """
        self._settings = settings
        self._clock = clock
        self._last_tick = clock()
        self._job_started: float | None = None

    def tick(self) -> None:
        """Records that the polling loop is running."""
        self._last_tick = self._clock()

    def job_started(self) -> None:
        """Records that a message is being worked on."""
        self._job_started = self._clock()

    def job_finished(self) -> None:
        """Records that the message in hand is done (or abandoned)."""
        self._job_started = None

    def check(self) -> tuple[bool, str]:
        """Evaluates the liveness rules.

        Returns:
            `(healthy, reason)`; the reason is a short human-readable phrase
            for the probe's response body, not something users see.
        """
        now = self._clock()
        if self._job_started is not None and (
            now - self._job_started < self._settings.job_max_runtime_seconds
        ):
            return True, "processing a message"
        if now - self._last_tick <= self._settings.queue_worker_loop_stale_seconds:
            return True, "polling"
        if self._job_started is not None:
            return False, "a message has exceeded the maximum runtime"
        return False, "the polling loop has stopped"


def health_response(health: WorkerHealth, request_line: str) -> bytes:
    """Builds the raw HTTP/1.1 reply to one request on the health port.

    Args:
        health: The queue worker's liveness state.
        request_line: The first line of the request, e.g. `GET /health HTTP/1.1`.

    Returns:
        The complete response bytes: 200 `ok` / 503 with the reason for
        `GET /health`, 404 for any other path, 405 for any other method.
    """
    parts = request_line.split()
    if len(parts) < 2 or parts[0] != "GET":
        status_line, body = "405 Method Not Allowed", "method not allowed"
    elif parts[1].split("?")[0] != "/health":
        status_line, body = "404 Not Found", "not found"
    else:
        healthy, reason = health.check()
        status_line = "200 OK" if healthy else "503 Service Unavailable"
        body = "ok" if healthy else f"unhealthy: {reason}"
    payload = body.encode()
    head = (
        f"HTTP/1.1 {status_line}\r\nContent-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + payload


async def start_health_server(
    health: WorkerHealth, port: int, host: str = "0.0.0.0"
) -> asyncio.Server:
    """Starts the liveness HTTP server.

    A hand-rolled responder on `asyncio.start_server` is enough for a probe
    that asks one question, and adds no dependency to the image. It binds all
    interfaces because the platform's probe arrives from outside the
    container; compose publishes no port for it.

    Args:
        health: The liveness state to report.
        port: TCP port to listen on (`0` picks a free one).
        host: Interface to bind.

    Returns:
        The running server; the caller closes it on shutdown.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            writer.write(health_response(health, line.decode("latin-1").strip()))
            await writer.drain()
        except (TimeoutError, ConnectionError, ValueError, asyncio.LimitOverrunError):
            # A probe that went away, never spoke, or sent an absurdly long
            # request line (readline raises ValueError past the stream limit)
            # needs no answer.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    return await asyncio.start_server(handle, host, port)


@dataclass
class _Lease:
    """A received message plus its *current* pop receipt.

    `QueuedMessage` is frozen, but every visibility extension returns a new
    receipt and invalidates the old one, so the heartbeat swaps `message` for
    a copy and the final delete reads it from here.
    """

    message: QueuedMessage


class QueueWorker:
    """Consumes `JobMessage`s and drives `process_cover` / `refresh_book_data`."""

    def __init__(
        self,
        queue: JobQueue,
        blob_store: BlobStore,
        session_factory: async_sessionmaker[AsyncSession],
        vision_service: VisionService,
        data_fetcher: BookDataFetcher,
        settings: Settings,
        health: WorkerHealth | None = None,
    ) -> None:
        """Wires the queue worker; nothing is read from the queue until it runs.

        Args:
            queue: The queue to consume.
            blob_store: Where scan photos live.
            session_factory: Opens the database sessions the pipeline uses.
            vision_service: Identifies covers.
            data_fetcher: Gathers book data from the catalogs.
            settings: Visibility timeout, attempt limit and backoff bounds.
            health: Liveness bookkeeping fed by the loop; one is created if
                omitted (the production `main` shares it with the health server).
        """
        self._queue = queue
        self._blobs = blob_store
        self._session_factory = session_factory
        self._vision = vision_service
        self._fetcher = data_fetcher
        self._settings = settings
        self._stopping = asyncio.Event()
        self.health = health or WorkerHealth(settings)

    def stop(self) -> None:
        """Asks `run` to exit after the message it is currently handling."""
        self._stopping.set()

    async def process_one(self) -> bool:
        """Receives and handles at most one message.

        Returns:
            `True` if a message was received (whatever came of it), `False`
            if the queue was empty.

        Raises:
            StorageUnavailable: If the queue or blob storage could not be
                reached. The message, if any, is left on the queue and
                reappears after the visibility timeout.
        """
        message = await self._queue.receive(self._settings.job_visibility_timeout_seconds)
        if message is None:
            return False

        lease = _Lease(message)
        heartbeat = asyncio.create_task(self._keep_alive(lease))
        self.health.job_started()
        limit = asyncio.timeout(self._settings.job_max_runtime_seconds)
        try:
            async with limit:
                await self._handle(lease)
        except StorageUnavailable:
            raise
        except TimeoutError:
            if limit.expired():
                # The pipeline hung: it is cancelled here instead of running
                # on beside a second delivery of the same message. Like any
                # other failure the message stays on the queue.
                logger.error(
                    "queue_job_timed_out",
                    message_id=message.message_id,
                    dequeue_count=message.dequeue_count,
                    max_runtime_seconds=self._settings.job_max_runtime_seconds,
                )
            else:
                logger.exception(
                    "queue_message_failed",
                    message_id=message.message_id,
                    dequeue_count=message.dequeue_count,
                )
        except Exception:
            # Deliberately broad: whatever broke (database down, a bug), the
            # message must not be deleted — the job is not in a final state.
            # It reappears after the visibility timeout, and the attempt
            # limit turns a message that always breaks into a poison one.
            logger.exception(
                "queue_message_failed",
                message_id=message.message_id,
                dequeue_count=message.dequeue_count,
            )
        finally:
            self.health.job_finished()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def _keep_alive(self, lease: _Lease) -> None:
        """Extends the message's visibility every half timeout until cancelled.

        Failing to extend is not fatal: the message may simply reappear and
        be redelivered, which the idempotent handling already tolerates.

        Stops by itself once the message has been held for
        `job_max_runtime_seconds`: a job still running then is hung, and
        extending it forever would keep its message from ever being retried.
        The liveness endpoint turns unhealthy at the same moment.
        """
        timeout = self._settings.job_visibility_timeout_seconds
        interval = timeout / 2
        started = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            if time.monotonic() - started >= self._settings.job_max_runtime_seconds:
                logger.error(
                    "queue_job_exceeded_max_runtime",
                    message_id=lease.message.message_id,
                    max_runtime_seconds=self._settings.job_max_runtime_seconds,
                )
                return
            try:
                receipt = await self._queue.extend(lease.message, timeout)
            except StorageUnavailable:
                logger.warning("queue_extend_failed", message_id=lease.message.message_id)
                continue
            lease.message = replace(lease.message, pop_receipt=receipt)

    async def run(self) -> None:
        """Polls until `stop` is called, backing off while the queue is empty."""
        settings = self._settings
        delay = settings.queue_idle_backoff_min_seconds
        logger.info("queue_worker_started", queue=settings.job_queue)

        while not self._stopping.is_set():
            self.health.tick()
            try:
                handled = await self.process_one()
            except StorageUnavailable:
                logger.warning("queue_worker_storage_unavailable")
                handled = False

            if handled:
                delay = settings.queue_idle_backoff_min_seconds
                continue

            # Interruptible sleep: a stop request must not wait out the backoff.
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, settings.queue_idle_backoff_max_seconds)

        self.health.tick()
        logger.info("queue_worker_stopped")

    async def _handle(self, lease: _Lease) -> None:
        """Dispatches one received message, deleting it once it is settled."""
        message = lease.message
        try:
            job_message = JobMessage.model_validate_json(message.body)
        except ValidationError:
            logger.error("queue_message_malformed", message_id=message.message_id)
            await self._queue.delete(lease.message)
            return

        job_id = job_message.job_id
        if message.dequeue_count > self._settings.job_max_attempts:
            await self._drop_poison(lease, job_message)
            return

        # Read what is needed and close the session: the pipeline below runs
        # for up to two minutes and opens its own, and a session held open
        # across it would sit "idle in transaction" on the database.
        async with self._session_factory() as db:
            job = await db.get(Job, job_id)
        if job is None:
            logger.error("queue_job_missing", job_id=job_id, kind=job_message.kind)
            await self._queue.delete(lease.message)
            return

        if job_message.kind is JobKind.SCAN:
            # A finished scan is a duplicate delivery; `pending`/`running`
            # runs (again, after a crash). But a `running` job that already has
            # a result was put there by a correction: a scan run writes
            # `result` only when it reaches `done`.
            already_settled = (
                job.status in (JobStatus.DONE.value, JobStatus.FAILED.value)
                or job.result is not None
            )
        else:
            # A correction puts the job in `running`; anything else means
            # this refresh was already carried out (or superseded).
            already_settled = job.status != JobStatus.RUNNING.value
        if already_settled:
            logger.info("queue_duplicate_ignored", job_id=job_id, status=job.status)
            await self._queue.delete(lease.message)
            return

        logger.info(
            "queue_job_started",
            job_id=job_id,
            kind=job_message.kind,
            dequeue_count=message.dequeue_count,
        )
        if job_message.kind is JobKind.SCAN:
            await self._run_scan(job)
        else:
            await self._run_refresh(job, job_message.previous_book_id)

        await self._queue.delete(lease.message)
        logger.info("queue_job_finished", job_id=job_id, kind=job_message.kind)

    async def _fail(self, job_id: int, message: str, blobs: BlobStore | None) -> None:
        """Fails a job (and, given `blobs`, deletes its photo) in a session of its own."""
        async with self._session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None:
                await fail_job(db, job, message, blobs)

    async def _run_scan(self, job: Job) -> None:
        """Loads the photo and runs `process_cover`.

        Raises:
            StorageUnavailable: If the photo could not be read. On purpose:
                the photo exists, storage is just down, so the message must
                stay on the queue for a later attempt.
        """
        if job.photo_blob is None:
            await self._fail(job.id, PHOTO_MISSING_ERROR, self._blobs)
            return
        try:
            image = await self._blobs.get(job.photo_blob)
        except BlobNotFound:
            logger.error("queue_photo_missing", job_id=job.id, blob=job.photo_blob)
            await self._fail(job.id, PHOTO_MISSING_ERROR, self._blobs)
            return

        await process_cover(
            job.id,
            image,
            self._session_factory,
            self._vision,
            self._fetcher,
            self._blobs,
        )

    async def _run_refresh(self, job: Job, previous_book_id: int | None) -> None:
        """Runs `refresh_book_data` with the title/author the endpoint stored.

        Args:
            job: The corrected job.
            previous_book_id: The misidentified book, from the queue message.
        """
        result: dict[str, Any] = job.result or {}
        title = result.get("title")
        author = result.get("author")
        if not isinstance(title, str) or not title.strip():
            # The endpoint always writes a title; without one nothing can be
            # fetched and retrying cannot change that. The photo is not
            # touched: it already belongs to the reader's library entry.
            logger.error("queue_refresh_without_title", job_id=job.id)
            await self._fail(job.id, POISON_ERROR, None)
            return

        await refresh_book_data(
            job.id,
            title,
            author if isinstance(author, str) else None,
            self._session_factory,
            self._fetcher,
            self._blobs,
            previous_book_id=previous_book_id,
        )

    async def _drop_poison(self, lease: _Lease, job_message: JobMessage) -> None:
        """Fails the job of a message delivered too often, then drops it.

        A scan's photo goes with it. A refresh keeps its photo: that blob
        already belongs to the reader's library entry.
        """
        async with self._session_factory() as db:
            job = await db.get(Job, job_message.job_id)
            if job is not None and job.status not in (
                JobStatus.DONE.value,
                JobStatus.FAILED.value,
            ):
                blobs = self._blobs if job_message.kind is JobKind.SCAN else None
                await fail_job(db, job, POISON_ERROR, blobs)
        logger.error(
            "job_poisoned",
            job_id=job_message.job_id,
            kind=job_message.kind,
            dequeue_count=lease.message.dequeue_count,
        )
        await self._queue.delete(lease.message)


def _install_signal_handlers(worker: QueueWorker) -> None:
    """Turns SIGTERM/SIGINT into a graceful `stop`.

    Container Apps sends SIGTERM on scale-in and `docker compose stop`
    does too: the queue worker finishes the message in hand, then exits, so
    a deploy does not abandon a half-run scan to the visibility timeout. That
    only holds while the platform's grace period (compose `stop_grace_period`,
    Container Apps `terminationGracePeriodSeconds`) outlasts the worst-case
    job; past it the process is killed and the message is redelivered.

    Args:
        worker: The queue worker to stop.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:
            # Windows event loops do not support add_signal_handler; fall back
            # to the plain signal module, hopping back onto the loop.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(worker.stop))


async def main() -> None:
    """Builds the production queue worker and runs it until signalled."""
    settings = get_settings()
    configure_logging(debug=settings.debug)
    # Bounded SDK retries/timeouts, before any storage client exists: a storage
    # outage must fail a receive in minutes at most, not stall the loop.
    configure_client_options(QUEUE_WORKER_CLIENT_OPTIONS)
    # Same guard as the api: a production queue worker with development
    # settings refuses to start instead of quietly running wide open.
    settings.validate_for_production()
    # Also as in the api: refuse to run against a schema that is behind.
    await init_db()

    health = WorkerHealth(settings)
    worker = QueueWorker(
        queue=get_job_queue(),
        blob_store=get_blob_store(),
        session_factory=AsyncSessionLocal,
        vision_service=build_vision_service(),
        data_fetcher=build_data_fetcher(),
        settings=settings,
        health=health,
    )
    _install_signal_handlers(worker)
    health_server = await start_health_server(health, settings.queue_worker_health_port)
    try:
        await worker.run()
    finally:
        health_server.close()
        await health_server.wait_closed()
        await close_storage()
        await dispose_engine(engine)


if __name__ == "__main__":
    asyncio.run(main())

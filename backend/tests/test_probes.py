"""Cloud step 5: liveness and readiness probes for the api and the queue worker."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.services.storage import JobKind, JobMessage, QueuedMessage
from app.workers.queue_worker import (
    QueueWorker,
    WorkerHealth,
    _Lease,
    health_response,
    start_health_server,
)
from tests.fakes import InMemoryBlobStore, InMemoryJobQueue

# ---------------------------------------------------------------- api


async def test_health_is_liveness_and_needs_no_database(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200


async def test_ready_is_200_when_the_database_answers(client: AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_ready_is_503_when_the_database_is_unreachable(client: AsyncClient) -> None:
    # Nothing listens on port 1: the connection is refused at once.
    dead = create_async_engine(
        "postgresql+asyncpg://glance:glance@127.0.0.1:1/glance", poolclass=NullPool
    )
    factory = async_sessionmaker(bind=dead, expire_on_commit=False)

    async def _dead_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _dead_db
    try:
        ready = await client.get("/ready")
        health = await client.get("/health")
    finally:
        await dead.dispose()

    assert ready.status_code == 503
    assert ready.json()["status"] == "unavailable"
    assert health.status_code == 200, "liveness must survive a database outage"


# ---------------------------------------------------------------- queue worker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _health(clock: FakeClock) -> WorkerHealth:
    return WorkerHealth(
        Settings(_env_file=None, queue_worker_loop_stale_seconds=60, job_max_runtime_seconds=600),
        clock,
    )


def test_an_idle_loop_that_keeps_ticking_is_healthy() -> None:
    clock = FakeClock()
    health = _health(clock)

    clock.now += 59
    assert health.check()[0] is True
    health.tick()
    clock.now += 59
    assert health.check()[0] is True


def test_a_loop_that_stopped_ticking_is_unhealthy() -> None:
    clock = FakeClock()
    health = _health(clock)

    clock.now += 61

    healthy, reason = health.check()
    assert healthy is False
    assert "polling loop" in reason


def test_a_message_in_hand_excuses_a_silent_loop_until_the_max_runtime() -> None:
    clock = FakeClock()
    health = _health(clock)
    health.job_started()

    clock.now += 599  # far past the loop window, but within the job limit
    assert health.check()[0] is True

    clock.now += 2  # now over 600 s
    healthy, reason = health.check()
    assert healthy is False
    assert "maximum runtime" in reason

    health.job_finished()
    health.tick()
    assert health.check()[0] is True


def test_health_response_rules() -> None:
    clock = FakeClock()
    health = _health(clock)

    assert health_response(health, "GET /health HTTP/1.1").startswith(b"HTTP/1.1 200 OK")
    assert b"404" in health_response(health, "GET /nope HTTP/1.1").split(b"\r\n")[0]
    assert b"405" in health_response(health, "POST /health HTTP/1.1").split(b"\r\n")[0]
    clock.now += 120
    assert b"503" in health_response(health, "GET /health HTTP/1.1").split(b"\r\n")[0]


async def test_the_health_server_answers_over_a_socket() -> None:
    clock = FakeClock()
    health = _health(clock)
    server = await start_health_server(health, 0, host="127.0.0.1")
    port = server.sockets[0].getsockname()[1]
    try:

        async def ask() -> bytes:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()
            data = await reader.read()
            writer.close()
            return data

        assert (await ask()).startswith(b"HTTP/1.1 200 OK")
        clock.now += 120
        assert (await ask()).startswith(b"HTTP/1.1 503")
    finally:
        server.close()
        await server.wait_closed()


async def test_the_loop_and_the_handler_feed_the_health_state(
    queue_worker: QueueWorker, job_queue: InMemoryJobQueue
) -> None:
    seen: list[bool] = []

    class Spy(WorkerHealth):
        def job_started(self) -> None:
            seen.append(True)
            super().job_started()

        def job_finished(self) -> None:
            seen.append(False)
            super().job_finished()

    queue_worker.health = Spy(get_settings())
    await job_queue.send(JobMessage(kind=JobKind.SCAN, job_id=999))  # unknown job: dropped

    assert await queue_worker.process_one() is True
    assert seen == [True, False]


async def test_the_heartbeat_stops_extending_after_the_max_runtime(
    queue_worker: QueueWorker,
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    settings = get_settings().model_copy(
        update={"job_visibility_timeout_seconds": 0.2, "job_max_runtime_seconds": 0.35}
    )
    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=queue_worker._session_factory,
        vision_service=queue_worker._vision,
        data_fetcher=queue_worker._fetcher,
        settings=settings,
    )
    lease = _Lease(QueuedMessage(message_id="m1", pop_receipt="r1", body="{}", dequeue_count=1))
    await job_queue.send(JobMessage(kind=JobKind.SCAN, job_id=1))
    received = await job_queue.receive(300)
    assert received is not None
    lease.message = received

    # It returns by itself, instead of extending a hung job forever.
    await asyncio.wait_for(worker._keep_alive(lease), timeout=3)

    extensions = len(job_queue.extended)
    assert 1 <= extensions <= 4  # ~0.1 s apart, cut off at 0.35 s
    await asyncio.sleep(0.5)
    assert len(job_queue.extended) == extensions


def test_default_liveness_window_exceeds_the_idle_backoff() -> None:
    settings = Settings(_env_file=None)

    assert settings.queue_worker_loop_stale_seconds > settings.queue_idle_backoff_max_seconds
    assert settings.queue_worker_loop_stale_seconds == 300.0


async def test_a_hung_job_is_cancelled_and_its_message_stays_on_the_queue(
    queue_worker: QueueWorker,
    job_queue: InMemoryJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cancelled = False

    async def hangs_forever(lease: _Lease) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled = True
            raise

    queue_worker._settings = get_settings().model_copy(update={"job_max_runtime_seconds": 0.2})
    monkeypatch.setattr(queue_worker, "_handle", hangs_forever)
    await job_queue.send(JobMessage(kind=JobKind.SCAN, job_id=1))

    handled = await asyncio.wait_for(queue_worker.process_one(), timeout=5)

    assert handled is True
    assert cancelled, "the hung coroutine must be cancelled, not left running"
    assert "queue_job_timed_out" in capsys.readouterr().out
    assert len(job_queue.bodies) == 1, "the message must stay for a retry"


async def test_an_oversize_request_line_does_not_crash_the_health_server() -> None:
    health = _health(FakeClock())
    server = await start_health_server(health, 0, host="127.0.0.1")
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /" + b"a" * 200_000 + b" HTTP/1.1\r\n\r\n")
        await writer.drain()
        await reader.read()  # the server just closes the connection
        writer.close()

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert (await reader.read()).startswith(b"HTTP/1.1 200 OK")
        writer.close()
    finally:
        server.close()
        await server.wait_closed()

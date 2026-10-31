"""Cloud step 6: traces and metrics (OpenTelemetry), tested with in-memory exporters.

Nothing here touches the network. Telemetry is off in the rest of the suite
(`conftest.py` blanks the exporter settings); these tests switch it on with
*injected* providers -- an `InMemorySpanExporter` and an `InMemoryMetricReader`
-- and `shutdown_telemetry()` in the fixture's teardown returns the process to
the off state. OpenTelemetry's global providers can be set only once, which is
why `app.core.telemetry` keeps its providers in module state and the
instrumentors are handed them explicitly: that is what makes a fresh exporter per
test possible. See the module docstring there.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, TraceFlags
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import build_data_fetcher, build_vision_service
from app.core import metrics, telemetry
from app.core.config import Settings, get_settings
from app.core.exceptions import CoverNotRecognized
from app.core.logging import configure_logging
from app.db.session import get_db, get_session_factory
from app.main import create_app
from app.models.job import JobStatus
from app.services import job_metrics
from app.services.ocr_service import OcrService
from app.services.storage import JobKind, JobMessage, get_blob_store, get_job_queue
from app.services.title_lookup import BookCandidate
from app.services.vector_store import CorpusLeak, RetrievedChunk, _assert_no_leak
from app.services.vision_service import VisionService
from app.workers.queue_worker import QueueWorker
from tests.conftest import _build_fake_data_fetcher, _build_fake_vision_service
from tests.fakes import (
    FakeOcrEngine,
    FakeOllamaClient,
    FakeTitleLookup,
    InMemoryBlobStore,
    InMemoryJobQueue,
)
from tests.fixtures.rag_corpus import build_dune, build_foundation
from tests.test_groq_client import _build_client, _FakeCompletions
from tests.test_jobs import A_SMALL_IMAGE, _auth_header, _register_and_login
from tests.test_queue_worker import _enqueue, _job, _make_job
from tests.test_rag_service import _make_service, _persist, grounded_responder
from tests.test_vision import _cover_bytes, _UnavailableAIClient


@dataclass
class Telemetry:
    """The in-memory sinks of one test's telemetry."""

    exporter: InMemorySpanExporter
    reader: InMemoryMetricReader

    def spans(self, name: str | None = None) -> list[ReadableSpan]:
        """Finished spans, optionally only those called `name`."""
        spans = list(self.exporter.get_finished_spans())
        return [s for s in spans if name is None or s.name == name]

    def points(self, metric: str) -> dict[tuple[tuple[str, Any], ...], float]:
        """Data points of one metric: attributes -> value (a histogram's count)."""
        found: dict[tuple[tuple[str, Any], ...], float] = {}
        data = self.reader.get_metrics_data()
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for item in scope.metrics:
                    if item.name != metric:
                        continue
                    for point in item.data.data_points:
                        value = getattr(point, "value", None)
                        if value is None:
                            value = point.count  # type: ignore[attr-defined]
                        found[tuple(sorted((point.attributes or {}).items()))] = value
        return found

    def total(self, metric: str) -> float:
        """Sum over all attribute combinations of a metric."""
        return sum(self.points(metric).values())


@pytest.fixture
def tel() -> Iterator[Telemetry]:
    """Switches telemetry on with in-memory sinks; switches it off again after."""
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    assert telemetry.setup_telemetry(
        get_settings(),
        "glance-test",
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    try:
        yield Telemetry(exporter, reader)
    finally:
        telemetry.shutdown_telemetry()


@pytest.fixture
async def traced_client(
    tel: Telemetry,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> AsyncIterator[AsyncClient]:
    """A client on a *fresh* app built while telemetry is on (so it is instrumented).

    The shared module-level app was built with telemetry off and stays
    uninstrumented, which is exactly what the rest of the suite relies on.
    """
    app = create_app(get_settings())

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = lambda: db_session_factory
    app.dependency_overrides[build_vision_service] = _build_fake_vision_service
    app.dependency_overrides[build_data_fetcher] = _build_fake_data_fetcher
    app.dependency_overrides[get_blob_store] = lambda: blob_store
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


# --------------------------------------------------------------------- off by default


def test_telemetry_is_off_unless_configured() -> None:
    assert not telemetry.is_enabled()
    assert get_settings().telemetry_exporter is None
    assert telemetry.setup_telemetry(get_settings(), "glance-test") is False
    assert not telemetry.is_enabled()
    assert telemetry.inject_traceparent() is None
    with telemetry.span("anything") as current:
        assert not current.is_recording()
    # Safe no-ops: no call site asks whether telemetry is on.
    metrics.record_scan("done", "vision", "scan", 1.0)
    metrics.record_corpus_leak()
    telemetry.annotate(**{"glance.job_id": 1})


def test_a_message_is_unchanged_when_telemetry_is_off() -> None:
    message = JobMessage(kind=JobKind.SCAN, job_id=7, traceparent=telemetry.inject_traceparent())

    assert message.model_dump_json(exclude_none=True) == '{"kind":"scan","job_id":7}'


def test_the_exporter_is_chosen_by_configuration() -> None:
    assert (
        Settings(
            applicationinsights_connection_string="", otel_exporter_otlp_endpoint=""
        ).telemetry_exporter
        is None
    )
    assert Settings(otel_exporter_otlp_endpoint="http://aspire:18890").telemetry_exporter == "otlp"
    # Application Insights is the production destination and wins over a stray endpoint.
    both = Settings(
        applicationinsights_connection_string="InstrumentationKey=00000000-0000-0000-0000-000000000000",
        otel_exporter_otlp_endpoint="http://aspire:18890",
    )
    assert both.telemetry_exporter == "azure_monitor"


def test_the_otlp_exporters_post_to_the_standard_paths() -> None:
    spans, metrics_exporter = telemetry._build_exporters(
        Settings(otel_exporter_otlp_endpoint="http://aspire-dashboard:18890/")
    )

    assert spans._endpoint == "http://aspire-dashboard:18890/v1/traces"
    assert metrics_exporter._endpoint == "http://aspire-dashboard:18890/v1/metrics"


def test_setup_twice_changes_nothing(tel: Telemetry) -> None:
    assert telemetry.setup_telemetry(get_settings(), "someone-else") is True
    with telemetry.span("once"):
        pass

    assert [s.name for s in tel.spans()] == ["once"]


def test_shutdown_returns_to_the_off_state() -> None:
    reader = InMemoryMetricReader()
    telemetry.setup_telemetry(
        get_settings(),
        "glance-test",
        tracer_provider=TracerProvider(),
        meter_provider=MeterProvider(metric_readers=[reader]),
    )
    telemetry.shutdown_telemetry()
    telemetry.shutdown_telemetry()  # twice is fine

    assert not telemetry.is_enabled()
    metrics.record_corpus_leak()
    assert reader.get_metrics_data() is None or not reader.get_metrics_data().resource_metrics


# --------------------------------------------------------------------- sampling


def _parent(sampled: bool) -> Any:
    context = SpanContext(
        trace_id=1,
        span_id=1,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT),
    )
    return trace.set_span_in_context(NonRecordingSpan(context))


def test_orphan_root_spans_are_dropped_but_requests_and_messages_start_traces() -> None:
    sampler = telemetry._Sampler(1.0)

    def decide(kind: SpanKind, parent: Any = None) -> Decision:
        return sampler.should_sample(parent, 5, "x", kind).decision

    # The idle queue poll, a heartbeat, the pending-age query: roots, but not requests.
    assert decide(SpanKind.CLIENT) is Decision.DROP
    assert decide(SpanKind.INTERNAL) is Decision.DROP
    assert decide(SpanKind.SERVER) is Decision.RECORD_AND_SAMPLE
    assert decide(SpanKind.CONSUMER) is Decision.RECORD_AND_SAMPLE
    # Under a sampled parent anything is kept, under an unsampled one nothing is.
    assert decide(SpanKind.CLIENT, _parent(True)) is Decision.RECORD_AND_SAMPLE
    assert decide(SpanKind.SERVER, _parent(False)) is Decision.DROP


def test_the_ratio_applies_to_roots_only() -> None:
    sampler = telemetry._Sampler(0.0)

    assert sampler.should_sample(None, 5, "x", SpanKind.SERVER).decision is Decision.DROP
    kept = sampler.should_sample(_parent(True), 5, "x", SpanKind.CLIENT)
    assert kept.decision is Decision.RECORD_AND_SAMPLE


# --------------------------------------------------------------------- trace across the queue


async def test_one_trace_spans_the_api_and_the_queue_worker(
    tel: Telemetry,
    traced_client: AsyncClient,
    queue_worker: QueueWorker,
    job_queue: InMemoryJobQueue,
) -> None:
    token = await _register_and_login(traced_client, "traced@example.com")
    created = await traced_client.post(
        "/books/analyze-cover",
        headers=_auth_header(token),
        files={"file": ("cover.jpg", A_SMALL_IMAGE, "image/jpeg")},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    body = json.loads(job_queue.bodies[0])
    assert "traceparent" in body, "the api injects the trace context into the message"

    assert await queue_worker.process_one() is True

    (request,) = tel.spans("POST /books/analyze-cover")
    (job_span,) = tel.spans("queue.process cover-jobs")
    assert request.kind is SpanKind.SERVER and job_span.kind is SpanKind.CONSUMER
    assert job_span.context.trace_id == request.context.trace_id
    assert job_span.parent is not None
    assert job_span.parent.span_id == request.context.span_id
    # What a trace is looked up by.
    assert request.attributes is not None and job_span.attributes is not None
    assert request.attributes["glance.job_id"] == job_id
    assert job_span.attributes["glance.job_id"] == job_id

    # The pipeline's stages are children in the same trace.
    stage = tel.spans("scan.fetch_sources")
    assert stage and all(s.context.trace_id == request.context.trace_id for s in stage)
    assert (await _job_status(job_id, queue_worker)) == JobStatus.DONE.value


async def _job_status(job_id: int, worker: QueueWorker) -> str:
    async with worker._session_factory() as db:
        from app.models.job import Job

        job = await db.get(Job, job_id)
        assert job is not None
        return job.status


async def test_a_message_without_a_traceparent_starts_its_own_trace(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await queue_worker.process_one()

    (job_span,) = tel.spans("queue.process cover-jobs")
    assert job_span.parent is None


async def test_probes_are_not_traced_but_requests_are(
    tel: Telemetry, traced_client: AsyncClient
) -> None:
    await traced_client.get("/health")
    await traced_client.get("/ready")
    # /ready still talks to the database, and with no request span above it those
    # SQL spans would be orphan roots; production's sampler drops those (see
    # `test_orphan_root_spans_are_dropped...`), so only request spans are asserted here.
    assert [s for s in tel.spans() if s.kind is SpanKind.SERVER] == []

    await traced_client.get("/users/me")  # 401 without a token: still a request
    assert [s.name for s in tel.spans() if s.kind is SpanKind.SERVER] == ["GET /users/me"]


async def test_sql_statements_are_traced(
    tel: Telemetry, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    engine = db_session_factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    telemetry.instrument_engine(engine)

    with telemetry.span("work"):
        async with db_session_factory() as db:
            await db.execute(text("SELECT 42 AS answer"))

    statements = [
        s
        for s in tel.spans()
        if s.attributes and "SELECT 42" in str(s.attributes.get("db.statement"))
    ]
    assert statements, [s.name for s in tel.spans()]
    assert statements[0].parent is not None


# --------------------------------------------------------------------- logs


@pytest.fixture
def json_logs() -> Iterator[None]:
    configure_logging(debug=False)
    try:
        yield
    finally:
        structlog.reset_defaults()
        configure_logging(debug=False)


def test_log_lines_inside_a_span_carry_the_trace_ids(
    tel: Telemetry, json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = structlog.get_logger("test")
    logger.info("outside")
    with telemetry.span("work") as current:
        logger.info("inside")
        context = current.get_span_context()

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    outside, inside = lines[-2], lines[-1]
    assert "trace_id" not in outside and "span_id" not in outside
    assert inside["trace_id"] == f"{context.trace_id:032x}"
    assert inside["span_id"] == f"{context.span_id:016x}"


def test_log_lines_are_unchanged_when_telemetry_is_off(
    json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with telemetry.span("work"):
        structlog.get_logger("test").info("inside")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "trace_id" not in line


# --------------------------------------------------------------------- metrics


async def test_a_finished_scan_is_counted_with_its_method_and_wait(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await queue_worker.process_one()

    assert tel.points("glance.scans") == {
        (("kind", "scan"), ("method", "ocr"), ("outcome", "done")): 1
    }
    assert tel.points("glance.scan.duration") == {(("outcome", "done"),): 1}
    assert tel.total("glance.queue.wait") == 1


async def test_a_scan_that_fails_is_counted_as_failed(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store, store_photo=False)
    await _enqueue(job_queue, job_id)

    await queue_worker.process_one()

    assert tel.points("glance.scans") == {
        (("kind", "scan"), ("method", "none"), ("outcome", "failed")): 1
    }


async def test_an_unreadable_cover_is_counted_as_unrecognized_with_a_duration(
    tel: Telemetry,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    class _Unreadable:
        async def identify(self, image_bytes: bytes) -> Any:
            raise CoverNotRecognized("nope")

    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_Unreadable(),  # type: ignore[arg-type]
        data_fetcher=_build_fake_data_fetcher(),
        settings=get_settings(),
    )
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    assert tel.points("glance.scans") == {
        (("kind", "scan"), ("method", "none"), ("outcome", "unrecognized")): 1
    }
    assert tel.points("glance.scan.duration") == {(("outcome", "unrecognized"),): 1}


async def test_a_poisoned_message_is_counted(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)
    for _ in range(get_settings().job_max_attempts):
        assert await job_queue.receive(30) is not None
        job_queue.expire_visibility()

    await queue_worker.process_one()

    assert tel.points("glance.scans") == {
        (("kind", "scan"), ("method", "none"), ("outcome", "poisoned")): 1
    }
    assert tel.total("glance.queue.wait") == 0, "a poisoned message never started"


async def test_a_hung_attempt_is_counted_apart_from_final_outcomes(
    tel: Telemetry,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    class _Hung:
        async def identify(self, image_bytes: bytes) -> Any:
            await asyncio.sleep(30)

    worker = QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_Hung(),  # type: ignore[arg-type]
        data_fetcher=_build_fake_data_fetcher(),
        settings=get_settings().model_copy(update={"job_max_runtime_seconds": 1}),
    )
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    # Not a final outcome: the failure ratio must see one failure per hung job
    # (its eventual `poisoned`), not one per attempt.
    assert tel.points("glance.scans") == {}
    assert tel.points("glance.scan.attempt_timeouts") == {(("kind", "scan"),): 1}
    assert (await _job(db_session_factory, job_id)).status == JobStatus.RUNNING.value


async def test_a_correction_is_counted_as_a_manual_refresh(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    job_id = await _make_job(db_session_factory, blob_store, status=JobStatus.RUNNING)
    async with db_session_factory() as db:
        job = await _job(db_session_factory, job_id)
        job = await db.merge(job)
        job.result = {"title": "Dune", "author": "Frank Herbert"}
        await db.commit()
    await _enqueue(job_queue, job_id, JobKind.REFRESH)

    await queue_worker.process_one()

    assert tel.points("glance.scans") == {
        (("kind", "refresh"), ("method", "manual"), ("outcome", "done")): 1
    }
    assert tel.total("glance.queue.wait") == 0, "a correction did not wait since the upload"


async def test_ocr_fallback_is_counted_with_its_reason(tel: Telemetry) -> None:
    unconfirmed = VisionService(
        ocr=OcrService(FakeOcrEngine([])),
        ollama=FakeOllamaClient('{"title": "Zzzz Unknown", "author": null}'),
        lookup=FakeTitleLookup([]),
        settings=get_settings(),
    )
    await unconfirmed.identify(_cover_bytes())

    unavailable = VisionService(
        ocr=OcrService(FakeOcrEngine([])),
        ollama=_UnavailableAIClient(),
        lookup=FakeTitleLookup([BookCandidate(title="Dune", authors=["Frank Herbert"])]),
        settings=get_settings(),
    )
    with pytest.raises(Exception):  # noqa: B017 - the outage is re-raised; only the count matters
        await unavailable.identify(_cover_bytes())

    assert tel.points("glance.vision.ocr_fallback") == {
        (("reason", "unconfirmed"),): 1,
        (("reason", "provider_unavailable"),): 1,
    }
    assert {s.name for s in tel.spans()} >= {"scan.vision", "scan.ocr"}


async def test_groq_rate_limits_and_bad_requests_are_counted(tel: Telemetry) -> None:
    from groq import BadRequestError, RateLimitError
    from httpx import Request, Response

    from app.core.exceptions import AIProviderUnavailable

    settings = Settings(groq_api_key="test-key", groq_max_retries=1)
    request = Request("POST", "https://api.groq.com")
    completions = _FakeCompletions(
        errors=[
            RateLimitError("slow down", response=Response(429, request=request), body=None),
            RateLimitError("slow down", response=Response(429, request=request), body=None),
        ]
    )
    with pytest.raises(AIProviderUnavailable):
        await _build_client(settings, completions).generate(model="m", prompt="p")

    completions = _FakeCompletions(
        errors=[BadRequestError("bad", response=Response(400, request=request), body=None)]
    )
    with pytest.raises(AIProviderUnavailable):
        await _build_client(settings, completions).generate(model="m", prompt="p")

    assert tel.points("glance.ai.errors") == {
        (("model", "m"), ("provider", "groq"), ("reason", "rate_limited")): 2,
        (("model", "m"), ("provider", "groq"), ("reason", "bad_request")): 1,
    }


async def test_summaries_are_counted_by_availability_and_cache(
    tel: Telemetry, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from app.models.book import Book

    (book_id,) = await _persist(db_session_factory, build_dune())
    service, _, _ = await _make_service(grounded_responder)
    for _ in range(2):  # generated, then served from the stored summary
        async with db_session_factory() as db:
            book = await db.get(Book, book_id)
            assert book is not None
            await service.summarize(db, book)

    broken, _, _ = await _make_service(lambda _prompt: "not usable output")
    (other_id,) = await _persist(db_session_factory, build_foundation())
    async with db_session_factory() as db:
        other = await db.get(Book, other_id)
        assert other is not None
        await broken.summarize(db, other)

    assert tel.points("glance.summaries") == {
        (("available", True), ("cached", False)): 1,
        (("available", True), ("cached", True)): 1,
        (("available", False), ("cached", False)): 1,
    }
    assert {"rag.ingest", "rag.retrieve", "rag.synthesize"} <= {s.name for s in tel.spans()}


def test_a_corpus_leak_is_counted_and_still_raises(tel: Telemetry) -> None:
    foreign = RetrievedChunk(
        id="b2:0", book_id=2, content="x", source="s", kind="k", heading=None, url=None,
        license=None, distance=0.1,
    )  # fmt: skip

    with pytest.raises(CorpusLeak):
        _assert_no_leak(1, [foreign])

    assert tel.total("glance.corpus_leaks") == 1
    _assert_no_leak(2, [foreign])  # no leak: nothing counted
    assert tel.total("glance.corpus_leaks") == 1


# --------------------------------------------------------------------- the pending-age gauge


async def test_oldest_pending_age_is_zero_without_pending_jobs(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as db:
        assert await job_metrics.oldest_pending_age_seconds(db) == 0.0


async def test_oldest_pending_age_measures_the_oldest_pending_job_only(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    from datetime import timedelta

    from app.models.job import Job

    old = await _make_job(db_session_factory, blob_store, photo="a.jpg")
    await _make_job(db_session_factory, blob_store, photo="b.jpg")
    await _make_job(db_session_factory, blob_store, photo="c.jpg", status=JobStatus.DONE)
    async with db_session_factory() as db:
        job = await db.get(Job, old)
        assert job is not None
        now = job.created_at + timedelta(seconds=300)

        assert await job_metrics.oldest_pending_age_seconds(db, now=now) == pytest.approx(300.0)


async def test_the_gauge_reports_what_the_monitor_cached(
    tel: Telemetry,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
) -> None:
    metrics.register_pending_age_gauge()
    metrics.register_pending_age_gauge()  # idempotent: no duplicate instrument
    assert tel.points("glance.jobs.oldest_pending_age") == {}, "nothing cached yet: no data"

    await _make_job(db_session_factory, blob_store)
    monitor = asyncio.create_task(job_metrics.run_pending_age_monitor(db_session_factory, 0.05))
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if metrics._pending_age_seconds is not None:
                break
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor

    (value,) = tel.points("glance.jobs.oldest_pending_age").values()
    assert 0.0 <= value < 30.0


def test_the_queue_message_type_ignores_unknown_fields_but_validates_known_ones() -> None:
    with pytest.raises(ValueError):
        JobMessage.model_validate_json('{"kind": "scan", "job_id": 0}')
    assert JobMessage.model_validate_json('{"kind": "scan", "job_id": 1, "evil": 1}').job_id == 1
    parsed = JobMessage.model_validate_json(
        '{"kind": "scan", "job_id": 1, "traceparent": "00-' + "a" * 32 + "-" + "b" * 16 + '-01"}'
    )
    assert parsed.traceparent is not None


# --------------------------------------------------------------------- review fixes


async def test_an_orphaned_pending_job_ages_out_of_the_gauge(
    db_session_factory: async_sessionmaker[AsyncSession], blob_store: InMemoryBlobStore
) -> None:
    from datetime import timedelta

    from app.models.job import Job

    orphan = await _make_job(db_session_factory, blob_store, photo="a.jpg")
    async with db_session_factory() as db:
        job = await db.get(Job, orphan)
        assert job is not None
        created = job.created_at

        inside = created + timedelta(seconds=600)
        age = await job_metrics.oldest_pending_age_seconds(db, now=inside, window_seconds=3600)
        assert age == pytest.approx(600.0)
        outside = created + timedelta(seconds=3601)
        assert (
            await job_metrics.oldest_pending_age_seconds(db, now=outside, window_seconds=3600)
            == 0.0
        )


def _worker_with(
    vision: Any,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
    **settings: Any,
) -> QueueWorker:
    return QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=vision,
        data_fetcher=_build_fake_data_fetcher(),
        settings=get_settings().model_copy(update=settings),
    )


class _Unreadable:
    async def identify(self, image_bytes: bytes) -> Any:
        raise CoverNotRecognized("nope")


class _Hung:
    async def identify(self, image_bytes: bytes) -> Any:
        await asyncio.sleep(30)


async def test_failed_and_timed_out_jobs_have_an_error_span_and_done_ones_do_not(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    from opentelemetry.trace import StatusCode

    async def run(worker: QueueWorker) -> ReadableSpan:
        tel.exporter.clear()
        await _enqueue(job_queue, await _make_job(db_session_factory, blob_store))
        await worker.process_one()
        (span,) = tel.spans("queue.process cover-jobs")
        return span

    assert (await run(queue_worker)).status.status_code is not StatusCode.ERROR

    unreadable = _worker_with(_Unreadable(), db_session_factory, blob_store, job_queue)
    assert (await run(unreadable)).status.status_code is StatusCode.ERROR

    hung = _worker_with(
        _Hung(), db_session_factory, blob_store, job_queue, job_max_runtime_seconds=1
    )
    assert (await run(hung)).status.status_code is StatusCode.ERROR


async def test_a_poisoned_message_has_an_error_span(
    tel: Telemetry,
    queue_worker: QueueWorker,
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    from opentelemetry.trace import StatusCode

    await _enqueue(job_queue, await _make_job(db_session_factory, blob_store))
    for _ in range(get_settings().job_max_attempts):
        assert await job_queue.receive(30) is not None
        job_queue.expire_visibility()

    await queue_worker.process_one()

    (span,) = tel.spans("queue.process cover-jobs")
    assert span.status.status_code is StatusCode.ERROR


async def test_the_timeout_log_line_carries_the_job_and_trace_ids(
    tel: Telemetry,
    json_logs: None,
    capsys: pytest.CaptureFixture[str],
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    worker = _worker_with(
        _Hung(), db_session_factory, blob_store, job_queue, job_max_runtime_seconds=1
    )
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    (span,) = tel.spans("queue.process cover-jobs")
    assert span.context is not None
    line = next(
        json.loads(raw)
        for raw in capsys.readouterr().out.splitlines()
        if '"queue_job_timed_out"' in raw
    )
    assert line["job_id"] == job_id
    assert line["trace_id"] == f"{span.context.trace_id:032x}"


async def test_a_failing_attempt_log_line_carries_the_job_and_trace_ids(
    tel: Telemetry,
    json_logs: None,
    capsys: pytest.CaptureFixture[str],
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> None:
    worker = _worker_with(_Hung(), db_session_factory, blob_store, job_queue)

    async def _boom(*_: Any) -> None:
        raise RuntimeError("database down")

    worker._handle_job = _boom  # type: ignore[method-assign]
    job_id = await _make_job(db_session_factory, blob_store)
    await _enqueue(job_queue, job_id)

    await worker.process_one()

    out = [json.loads(raw) for raw in capsys.readouterr().out.splitlines() if raw.startswith("{")]
    (line,) = [entry for entry in out if entry.get("event") == "queue_message_failed"]
    assert line["job_id"] == job_id
    assert len(line["trace_id"]) == 32


async def test_shutdown_flushes_tracer_and_meter_independently_with_a_timeout() -> None:
    calls: list[tuple[str, Any]] = []

    class _Provider:
        def __init__(self, name: str, broken: bool = False) -> None:
            self.name, self.broken = name, broken

        def force_flush(self, timeout_millis: int | None = None) -> bool:
            calls.append((f"{self.name}.flush", timeout_millis))
            if self.broken:
                raise RuntimeError("receiver down")
            return True

        def shutdown(self) -> None:
            calls.append((f"{self.name}.shutdown", None))
            if self.broken:
                raise RuntimeError("receiver down")

    telemetry._state = telemetry._State(
        service_name="x",
        tracer_provider=_Provider("tracer", broken=True),  # type: ignore[arg-type]
        meter_provider=_Provider("meter"),  # type: ignore[arg-type]
        owns_providers=True,
    )
    telemetry.shutdown_telemetry()  # must not raise

    assert calls == [
        ("tracer.flush", 5000),
        ("meter.flush", 5000),
        ("tracer.shutdown", None),
        ("meter.shutdown", None),
    ]
    assert not telemetry.is_enabled()


async def test_ollama_failures_are_counted_like_groq_ones(tel: Telemetry) -> None:
    from ollama import ResponseError

    from app.core.exceptions import OllamaUnavailable
    from app.services.ollama_client import AsyncOllamaClient

    class _Failing:
        async def generate(self, **_: Any) -> Any:
            raise ResponseError("model not found")

    client = AsyncOllamaClient(Settings())
    client._client = _Failing()  # type: ignore[assignment]
    with pytest.raises(OllamaUnavailable):
        await client.generate(model="moondream", prompt="p")

    assert tel.points("glance.ai.errors") == {
        (("model", "moondream"), ("provider", "ollama"), ("reason", "bad_request")): 1
    }

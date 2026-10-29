"""Common pytest fixtures for all Glance backend tests."""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import build_data_fetcher, build_vision_service
from app.core.config import get_settings
from app.db.session import Base, get_db, get_session_factory
from app.main import app
from app.models import (  # noqa: F401  (registers the models on Base.metadata)
    Book,
    Job,
    JournalEntry,
    LibraryEntry,
    RecommendationState,
    RefreshToken,
    TextSource,
    User,
)
from app.models.book import SourceKind, SourceName
from app.models.vector import BookChunk, BookProfileRow  # noqa: F401
from app.services.data_fetcher import BookDataFetcher
from app.services.ocr_service import OcrService, TextCandidate
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult
from app.services.storage import AzureBlobStore, AzureJobQueue, get_blob_store, get_job_queue
from app.services.title_lookup import BookCandidate
from app.services.vision_service import VisionService
from app.workers.queue_worker import QueueWorker
from tests.fakes import (
    FakeContentSource,
    FakeOcrEngine,
    FakeOllamaClient,
    FakeTitleLookup,
    InMemoryBlobStore,
    InMemoryJobQueue,
)

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://glance:glance@127.0.0.1:5433/glance_test"


def _build_fake_vision_service() -> VisionService:
    """A deterministic `VisionService` for the HTTP test suite.

    Scripted to resolve via the OCR path with high confidence, so tests
    that only care about the job lifecycle (not recognition itself) don't
    need real RapidOCR/Ollama/network calls.
    """
    ocr = OcrService(
        FakeOcrEngine(
            [
                TextCandidate(text="Dune", score=0.95, relative_height=40, line_index=0),
                TextCandidate(text="Frank Herbert", score=0.9, relative_height=20, line_index=1),
            ]
        )
    )
    ollama = FakeOllamaClient()
    lookup = FakeTitleLookup([BookCandidate(title="Dune", authors=["Frank Herbert"])])
    return VisionService(ocr=ocr, ollama=ollama, lookup=lookup, settings=get_settings())


def _build_fake_data_fetcher() -> BookDataFetcher:
    """A `BookDataFetcher` over scripted sources, for the HTTP test suite.

    The queue worker fixture runs the real pipeline, so without this every
    job test would reach out to Google Books, Open Library and Wikipedia.
    Scripted to match the fake vision service's "Dune".
    """
    google = FakeContentSource(
        SourceName.GOOGLE_BOOKS,
        SourceResult(
            source=SourceName.GOOGLE_BOOKS,
            metadata=BookMetadata(
                title="Dune",
                author="Frank Herbert",
                description="A desert planet, a spice, an empire.",
                categories=["Fiction"],
                cover_url="https://example.test/dune.jpg",
                average_rating=4.5,
                ratings_count=120,
            ),
            passages=[
                SourcePassage(
                    kind=SourceKind.DESCRIPTION,
                    content="A desert planet, a spice, an empire.",
                )
            ],
            url="https://example.test/dune",
        ),
    )
    return BookDataFetcher(sources=[google], settings=get_settings())


def _test_database_url() -> str:
    """The PostgreSQL database the suite runs on.

    Overridable with `TEST_DATABASE_URL` (the `test` compose service points
    it at the in-network host). The default is the `glance_test` database of
    `docker compose up -d postgres`, reached through the host port mapping.
    """
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


async def _prepare_schema(url: str) -> None:
    """Recreates the extension and every table, once per test session.

    Starts from an empty `public` schema, so leftovers of an earlier
    (possibly crashed) run, or of a differently-shaped schema, cannot leak
    in. Tables come from the models; `tests/test_migrations.py` is what
    proves the migrations produce the same schema, in a database of its own.
    """
    # This wipes the whole schema, and the dev database shares host, port and
    # credentials with the test one: a mistyped TEST_DATABASE_URL would
    # destroy real data. Only a database named for tests is ever touched.
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(
            f"Refusing to wipe database {database!r}: the test database's name must end in "
            "'_test'. Check TEST_DATABASE_URL."
        )

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database_schema() -> None:
    """Requires PostgreSQL and builds the schema once; fails the run if it is absent.

    Never skips: the vector tables need pgvector, and the
    isolation tests must run against the real store (a fake would only
    prove our own filter-passing code agrees with itself). A suite that
    quietly skipped half its tests when the database was down would report
    green for exactly the code it exists to check.
    """
    url = _test_database_url()
    try:
        asyncio.run(_prepare_schema(url))
    except Exception as exc:  # noqa: BLE001 - any failure here means "no usable database"
        pytest.exit(
            "PostgreSQL with pgvector is required for the test suite but could not be "
            f"used at {make_url(url).render_as_string(hide_password=True)} "
            f"({type(exc).__name__}: {exc}). Start it with `docker compose up -d postgres` "
            "(from the repo root), or point TEST_DATABASE_URL at a pgvector-enabled server.",
            returncode=2,
        )


async def _probe_azurite() -> None:
    """Round-trips a blob and a queue message through the configured storage."""
    from app.services.storage import JobKind, JobMessage

    settings = get_settings()
    blobs = AzureBlobStore(settings.azure_storage_connection_string, "glance-startup-check")
    queue = AzureJobQueue(settings.azure_storage_connection_string, "glance-startup-check")
    try:
        await blobs.put("probe", b"x")
        await blobs.delete("probe")
        await queue.send(JobMessage(kind=JobKind.SCAN, job_id=1))
        received = await queue.receive(1)
        assert received is not None
        await queue.delete(received)
    finally:
        await blobs.close()
        await queue.close()


@pytest.fixture(scope="session", autouse=True)
def _azurite(_database_schema: None) -> None:
    """Requires Azurite (Blob + Queue emulator); fails the run if it is absent.

    Same stance as PostgreSQL: the storage adapters are tested against the
    real emulator, because a fake would only prove our code agrees with our
    own assumptions about queue semantics. Never skips.
    """
    try:
        asyncio.run(_probe_azurite())
    except Exception as exc:  # noqa: BLE001 - any failure here means "no usable storage"
        pytest.exit(
            "Azurite (the Azure Storage emulator) is required for the test suite but could "
            f"not be used ({type(exc).__name__}: {exc}). Start it with "
            "`docker compose up -d postgres azurite` (from the repo root), or point "
            "AZURE_STORAGE_CONNECTION_STRING at a storage account / emulator.",
            returncode=2,
        )


@pytest.fixture
def blob_store() -> InMemoryBlobStore:
    """The in-memory blob store the api routes and the queue worker share in a test."""
    return InMemoryBlobStore()


@pytest.fixture
def job_queue() -> InMemoryJobQueue:
    """The in-memory job queue the api routes and the queue worker share in a test."""
    return InMemoryJobQueue()


@pytest.fixture
def queue_worker(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> QueueWorker:
    """A queue worker over the fakes: fake vision, scripted catalog, test database."""
    return QueueWorker(
        queue=job_queue,
        blob_store=blob_store,
        session_factory=db_session_factory,
        vision_service=_build_fake_vision_service(),
        data_fetcher=_build_fake_data_fetcher(),
        settings=get_settings(),
    )


@pytest.fixture
async def db_session_factory(
    _database_schema: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A clean PostgreSQL database per test.

    The schema exists once per session; each test starts by emptying every
    table (`TRUNCATE ... RESTART IDENTITY CASCADE`), which is far cheaper
    than dropping and recreating them and makes ids restart at 1 in every
    test, as the assertions on `b1:...` chunk ids expect.

    Yields:
        An `async_sessionmaker` bound to the clean database, unique to the
        current test.
    """
    engine = create_async_engine(_test_database_url(), poolclass=NullPool)
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))

    opened: list[AsyncSession] = []

    class _TrackedSession(AsyncSession):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            opened.append(self)

    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False, class_=_TrackedSession
    )
    try:
        yield factory
    finally:
        # Some tests never close the sessions they open. A session left in a
        # transaction holds locks that would block the next test's TRUNCATE
        # forever, so they are closed here.
        for session in opened:
            await session.close()
        await engine.dispose()


@pytest.fixture
async def client(
    db_session_factory: async_sessionmaker[AsyncSession],
    blob_store: InMemoryBlobStore,
    job_queue: InMemoryJobQueue,
) -> AsyncIterator[AsyncClient]:
    """Async HTTP client bound to the app, with the database overridden to a test one.

    Yields:
        An `AsyncClient` httpx configured with the application's ASGI transport.
    """

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = lambda: db_session_factory
    app.dependency_overrides[build_vision_service] = _build_fake_vision_service
    app.dependency_overrides[build_data_fetcher] = _build_fake_data_fetcher
    app.dependency_overrides[get_blob_store] = lambda: blob_store
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()

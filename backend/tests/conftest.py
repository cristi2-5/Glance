"""Common pytest fixtures for all Glance backend tests."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import build_data_fetcher, build_vision_service
from app.core.config import get_settings
from app.db.session import Base, get_db, get_session_factory
from app.main import app
from app.models import (  # noqa: F401  (registers the models on Base.metadata)
    Book,
    Job,
    JournalEntry,
    LibraryEntry,
    RefreshToken,
    TextSource,
    User,
)
from app.models.book import SourceKind, SourceName
from app.services.data_fetcher import BookDataFetcher
from app.services.ocr_service import OcrService, TextCandidate
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult
from app.services.title_lookup import BookCandidate
from app.services.vision_service import VisionService
from tests.fakes import FakeContentSource, FakeOcrEngine, FakeOllamaClient, FakeTitleLookup


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

    The background task started by `POST /books/analyze-cover` runs for
    real under `ASGITransport`, so without this override every job test
    would reach out to Google Books, Open Library and Wikipedia. Scripted
    to match the fake vision service's "Dune".
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


@pytest.fixture
async def db_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """In-memory SQLite engine, isolated per test, with all tables created.

    Yields:
        An `async_sessionmaker` bound to a clean database, unique to the
        current test.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.fixture
async def client(
    db_session_factory: async_sessionmaker[AsyncSession],
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
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()

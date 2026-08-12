"""Tests for `BookDataFetcher` — the cache lookup, merge and persist logic.

Sources are faked throughout; no HTTP and no network.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.models.book import Book, SourceKind, SourceName, TextSource
from app.services.data_fetcher import BookDataFetcher, normalize_key
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult
from tests.fakes import FakeContentSource


@pytest.fixture
async def db(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncSession:
    """A session on the isolated in-memory database."""
    return db_session_factory()


def _google(**meta: object) -> SourceResult:
    """A matched Google Books result carrying the given metadata."""
    defaults: dict[str, object] = {
        "title": "Dune",
        "author": "Frank Herbert",
        "description": "A desert planet.",
        "categories": ["Fiction"],
        "cover_url": "https://books.test/dune.jpg",
        "average_rating": 4.5,
    }
    defaults.update(meta)
    return SourceResult(
        source=SourceName.GOOGLE_BOOKS,
        metadata=BookMetadata(**defaults),  # type: ignore[arg-type]
        passages=[SourcePassage(kind=SourceKind.DESCRIPTION, content="A desert planet.")],
        url="https://books.test/dune",
    )


def _wikipedia() -> SourceResult:
    """A matched Wikipedia result carrying a reception passage."""
    return SourceResult(
        source=SourceName.WIKIPEDIA,
        metadata=BookMetadata(),
        passages=[
            SourcePassage(
                kind=SourceKind.RECEPTION,
                heading="Reception",
                content="Dune won the Hugo Award.",
            )
        ],
        url="https://en.wikipedia.org/wiki/Dune",
        license="CC BY-SA 4.0",
    )


def _fetcher(*sources: FakeContentSource, settings: Settings | None = None) -> BookDataFetcher:
    """Builds a fetcher over the given fake sources."""
    return BookDataFetcher(sources=list(sources), settings=settings or get_settings())


# --- Normalization ----------------------------------------------------------


def test_normalize_key_folds_case_accents_and_punctuation() -> None:
    assert normalize_key("DUNE", "Frank Herbert") == normalize_key("dune", "frank herbert")
    assert normalize_key("Cărțile", "X") == normalize_key("Cartile", "X")
    assert normalize_key("The  Hobbit!", None) == normalize_key("the hobbit", "")


def test_normalize_key_separates_title_from_author() -> None:
    # Two different books must not collide just because the concatenation
    # happens to match.
    assert normalize_key("A B", "C") != normalize_key("A", "B C")


# --- Cache miss -------------------------------------------------------------


async def test_cache_miss_queries_sources_and_persists(db: AsyncSession) -> None:
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())
    wiki = FakeContentSource(SourceName.WIKIPEDIA, _wikipedia())

    book = await _fetcher(google, wiki).fetch(db, "Dune", "Frank Herbert")

    assert book.id is not None
    assert book.metadata_found is True
    assert book.title == "Dune"
    assert book.description == "A desert planet."
    assert book.average_rating == 4.5
    assert google.calls == [("Dune", "Frank Herbert")]

    kinds = {source.kind for source in book.text_sources}
    assert kinds == {SourceKind.DESCRIPTION.value, SourceKind.RECEPTION.value}

    reception = next(s for s in book.text_sources if s.kind == SourceKind.RECEPTION.value)
    assert reception.license == "CC BY-SA 4.0"
    assert reception.book_id == book.id


async def test_cache_miss_merges_metadata_in_source_priority_order(db: AsyncSession) -> None:
    # Google Books wins on the fields both carry; Open Library fills the
    # gaps and its subjects are unioned in rather than overwriting.
    google = FakeContentSource(
        SourceName.GOOGLE_BOOKS, _google(description="Google blurb.", isbn_13=None)
    )
    open_library = FakeContentSource(
        SourceName.OPEN_LIBRARY,
        SourceResult(
            source=SourceName.OPEN_LIBRARY,
            metadata=BookMetadata(
                description="Open Library blurb.",
                categories=["Ecology"],
                isbn_13="9780441013593",
            ),
        ),
    )

    book = await _fetcher(google, open_library).fetch(db, "Dune", "Frank Herbert")

    assert book.description == "Google blurb."
    assert book.isbn_13 == "9780441013593", "gap filled by the lower-priority source"
    assert book.categories == ["Fiction", "Ecology"]


# --- Cache hit --------------------------------------------------------------


async def test_cache_hit_returns_without_touching_any_source(db: AsyncSession) -> None:
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())
    fetcher = _fetcher(google)

    first = await fetcher.fetch(db, "Dune", "Frank Herbert")
    second = await fetcher.fetch(db, "Dune", "Frank Herbert")

    assert second.id == first.id
    assert len(google.calls) == 1, "the second fetch must be served from SQLite"


async def test_cache_hit_survives_differing_case_and_accents(db: AsyncSession) -> None:
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())
    fetcher = _fetcher(google)

    first = await fetcher.fetch(db, "Dune", "Frank Herbert")
    second = await fetcher.fetch(db, "  DUNE ", "frank  herbert")

    assert second.id == first.id
    assert len(google.calls) == 1


async def test_expired_cache_entry_refetches_without_duplicating_passages(
    db: AsyncSession,
) -> None:
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())
    wiki = FakeContentSource(SourceName.WIKIPEDIA, _wikipedia())
    fetcher = _fetcher(google, wiki, settings=Settings(book_cache_ttl_days=30))

    book = await fetcher.fetch(db, "Dune", "Frank Herbert")
    passages_before = len(book.text_sources)

    book.sources_fetched_at = datetime.utcnow() - timedelta(days=31)
    await db.commit()

    refreshed = await fetcher.fetch(db, "Dune", "Frank Herbert")

    assert refreshed.id == book.id
    assert len(google.calls) == 2, "a stale entry must be re-fetched"
    assert len(refreshed.text_sources) == passages_before, "passages replaced, not appended"

    total = await db.scalar(select(func.count()).select_from(TextSource))
    assert total == passages_before


# --- Failure paths ----------------------------------------------------------


async def test_no_catalog_match_still_returns_a_book_flagged_not_found(
    db: AsyncSession,
) -> None:
    # The client renders "metadata not found" — this must never be a 500.
    google = FakeContentSource(SourceName.GOOGLE_BOOKS)
    wiki = FakeContentSource(SourceName.WIKIPEDIA)

    book = await _fetcher(google, wiki).fetch(db, "Ocolul Pamantului", "Jules Verne")

    assert book.id is not None
    assert book.metadata_found is False
    assert book.description is None
    assert book.text_sources == []
    assert book.title == "Ocolul Pamantului", "falls back to the vision reading"
    assert book.author == "Jules Verne"


async def test_metadata_found_but_no_reception_returns_book_with_fewer_passages(
    db: AsyncSession,
) -> None:
    # Google Books matched, Wikipedia has no article — the analogue of
    # "scraping returned zero reviews": still a complete, useful answer.
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())
    wiki = FakeContentSource(SourceName.WIKIPEDIA)

    book = await _fetcher(google, wiki).fetch(db, "Dune", "Frank Herbert")

    assert book.metadata_found is True
    assert book.description == "A desert planet."
    assert [s.kind for s in book.text_sources] == [SourceKind.DESCRIPTION.value]


async def test_a_source_that_raises_does_not_fail_the_fetch(db: AsyncSession) -> None:
    exploding = FakeContentSource(SourceName.WIKIPEDIA, raises=RuntimeError("bug in the parser"))
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())

    book = await _fetcher(google, exploding).fetch(db, "Dune", "Frank Herbert")

    assert book.metadata_found is True
    assert book.description == "A desert planet."


async def test_total_outage_is_not_cached_as_an_empty_entry(db: AsyncSession) -> None:
    # The bug this guards: caching a transient outage would serve an empty
    # book for the whole TTL. An unreachable source must leave the entry
    # unsettled so the next scan retries.
    down_google = FakeContentSource(
        SourceName.GOOGLE_BOOKS, SourceResult.unavailable(SourceName.GOOGLE_BOOKS)
    )
    down_wiki = FakeContentSource(
        SourceName.WIKIPEDIA, SourceResult.unavailable(SourceName.WIKIPEDIA)
    )
    fetcher = _fetcher(down_google, down_wiki)

    book = await fetcher.fetch(db, "Dune", "Frank Herbert")
    assert book.sources_fetched_at is None
    assert book.metadata_found is False

    await fetcher.fetch(db, "Dune", "Frank Herbert")
    assert len(down_google.calls) == 2, "an outage must not settle the cache"

    # And exactly one row, not one per retry.
    assert await db.scalar(select(func.count()).select_from(Book)) == 1


async def test_a_reachable_no_match_does_settle_the_cache(db: AsyncSession) -> None:
    # The mirror of the test above: "we asked, there is no such book" is a
    # real answer and is worth caching, or every scan of an uncatalogued
    # book would re-query all three sources forever.
    google = FakeContentSource(SourceName.GOOGLE_BOOKS)
    fetcher = _fetcher(google)

    book = await fetcher.fetch(db, "Untranslated Edition", None)
    assert book.sources_fetched_at is not None

    await fetcher.fetch(db, "Untranslated Edition", None)
    assert len(google.calls) == 1


async def test_fetch_is_scoped_to_the_requested_book_only(db: AsyncSession) -> None:
    # Guards the "on demand, per book" rule: one scan must never leave
    # unrelated titles behind in the database.
    google = FakeContentSource(SourceName.GOOGLE_BOOKS, _google())

    await _fetcher(google).fetch(db, "Dune", "Frank Herbert")

    titles = (await db.scalars(select(Book.title))).all()
    assert list(titles) == ["Dune"]

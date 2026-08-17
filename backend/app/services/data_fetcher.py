"""Lazy, per-book fetching and caching of everything known about a book.

The entry point is `BookDataFetcher.fetch`, called with the `{title,
author}` the vision step produced. It is **on demand and per book**: only
the title just scanned is ever fetched, and the database is never
pre-populated with unrelated works.

The flow:

1. Look the book up in SQLite by a normalized title+author key. A fresh
   hit (metadata present and text sources within the TTL) returns
   immediately, with no external call at all.
2. On a miss, query the three official sources — Google Books, Open
   Library, Wikipedia — and merge them.
3. If the merge produced no cover image, run the fallback chain in
   `cover_fallback.py` to try and recover one.
4. Persist the result and return the book either way.

Nothing in step 2 is allowed to fail the request. A source that times
out, refuses, or simply has no such book is recorded as absent and the
rest of the result is returned regardless: no catalog match yields a
`metadata_found=False` book carrying just the vision-derived title, and
no Wikipedia article yields an empty passage list. The client renders
those states; it never sees a 500 because an external site was slow.

The one thing an outage must *not* do is get cached. `SourceResult`
separates "reached it, no such book" from "could not reach it", and only
the former is persisted as a settled answer — otherwise a transient 503
would pin an empty entry in SQLite for the entire TTL.
"""

import asyncio
import dataclasses
import unicodedata
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.book import Book, SourceName, TextSource
from app.services.sources.base import BookMetadata, ContentSource, SourceResult
from app.services.sources.cover_fallback import CoverFallback, OfficialCoverFallback
from app.services.sources.google_books import GoogleBooksSource
from app.services.sources.open_library import OpenLibrarySource
from app.services.sources.wikipedia import WikipediaSource

logger = structlog.get_logger(__name__)


def normalize_key(title: str, author: str | None = None) -> str:
    """Builds the cache key for a title+author pair.

    Case, accents, punctuation and surrounding articles vary between what
    the vision model reads off a cover and what a catalog stores, so the
    raw strings make a poor key: "DUNE"/"Dune" and "Cărțile"/"Cartile"
    would each cache twice and re-fetch forever. Folding all of that away
    gives one stable entry per book.

    Args:
        title: The book title.
        author: The author, when known.

    Returns:
        A `"title|author"` key, accent- and case-folded, with runs of
        non-alphanumeric characters collapsed to single spaces.
    """
    return f"{_fold(title)}|{_fold(author or '')}"


def _fold(value: str) -> str:
    """Case-folds, strips accents, and collapses punctuation in a string.

    Args:
        value: The string to normalize.

    Returns:
        The folded form, or an empty string for empty input.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() else " " for ch in without_accents.casefold())
    return " ".join(cleaned.split())


class BookDataFetcher:
    """Fetches and caches book metadata and prose from the official sources.

    Sources are injected rather than constructed, so tests can supply
    fakes and the production wiring lives in one factory
    (`build_data_fetcher`).
    """

    def __init__(
        self,
        sources: list[ContentSource],
        settings: Settings,
        cover_fallback: CoverFallback | None = None,
    ) -> None:
        self._sources = sources
        self._cover_fallback = cover_fallback
        self._ttl = timedelta(days=settings.book_cache_ttl_days)
        self._empty_ttl = timedelta(hours=settings.empty_book_cache_ttl_hours)

    async def fetch(self, db: AsyncSession, title: str, author: str | None = None) -> Book:
        """Returns the cached book, fetching it from the sources on a miss.

        Args:
            db: The database session to read and write through.
            title: The title recognized from the cover.
            author: The author recognized from the cover, when any.

        Returns:
            The persisted `Book`, with its `text_sources` loaded. Check
            `metadata_found` to tell a catalogued book from one carrying
            only the vision-derived title.
        """
        key = normalize_key(title, author)
        book = await self._load_cached(db, key)

        if book is not None and self._is_fresh(book):
            logger.info("book_cache_hit", key=key, book_id=book.id)
            return book

        logger.info("book_cache_miss", key=key, refreshing=book is not None)
        results = await self._gather(title, author)
        metadata = _merge_metadata([result for result in results if result.matched])
        metadata = await self._resolve_cover(metadata, results)
        return await self._persist(db, key, title, author, results, metadata, existing=book)

    async def _resolve_cover(
        self, metadata: BookMetadata, results: list[SourceResult]
    ) -> BookMetadata:
        """Fills in a cover image when no source supplied one.

        Runs only on the gap: if Google Books or Open Library already
        contributed a thumbnail through the merge, this makes no requests
        at all. See `cover_fallback.py` for the chain and its licensing
        order.

        Args:
            metadata: The merged catalog metadata.
            results: What each source returned, for the Wikipedia article
                title the last leg needs.

        Returns:
            `metadata` unchanged, or a copy carrying the recovered cover.
        """
        if metadata.cover_url or self._cover_fallback is None:
            return metadata

        article = next(
            (
                result.record_ref
                for result in results
                if result.source == SourceName.WIKIPEDIA and result.matched
            ),
            None,
        )
        cover_url = await self._cover_fallback.find_cover(
            isbn_13=metadata.isbn_13, isbn_10=metadata.isbn_10, wikipedia_article=article
        )
        if cover_url is None:
            return metadata

        return dataclasses.replace(metadata, cover_url=cover_url)

    async def _load_cached(self, db: AsyncSession, key: str) -> Book | None:
        """Loads a cached book by its normalized key.

        Args:
            db: The database session.
            key: The normalized title+author key.

        Returns:
            The cached `Book`, or `None` if it has never been fetched.
        """
        result = await db.execute(select(Book).where(Book.normalized_key == key))
        return result.scalar_one_or_none()

    def _is_fresh(self, book: Book) -> bool:
        """Decides whether a cached book can be served without re-fetching.

        A book whose sources have never been gathered is never fresh, even
        if the row exists — that is the half-populated state left by an
        earlier outage, and it should be retried rather than served.

        An entry the catalogs yielded nothing for expires far sooner than a
        populated one. Emptiness is rarely a durable fact — a bare catalog
        record gets filled in, a source that was degraded recovers — and
        pinning it for the full TTL makes the gap permanent from the user's
        side, because rescanning the book just re-serves the empty row.

        Args:
            book: The cached book.

        Returns:
            `True` when the entry is complete and within its TTL.
        """
        if book.sources_fetched_at is None:
            return False
        ttl = self._ttl if book.metadata_found else self._empty_ttl
        return datetime.utcnow() - book.sources_fetched_at < ttl

    async def _gather(self, title: str, author: str | None) -> list[SourceResult]:
        """Queries every source concurrently.

        The sources are independent and each already bounded by its own
        timeout, so running them together costs one round trip instead of
        three — which matters on a pipeline the user is waiting on.

        Args:
            title: The book title.
            author: The author, when known.

        Returns:
            One `SourceResult` per source, in source-priority order. A
            source that raised unexpectedly is reported as unavailable
            rather than propagating.
        """
        gathered = await asyncio.gather(
            *(source.fetch(title, author) for source in self._sources),
            return_exceptions=True,
        )

        results: list[SourceResult] = []
        for source, outcome in zip(self._sources, gathered, strict=True):
            if isinstance(outcome, BaseException):
                # A source raising is a bug in that source, not a reason to
                # fail the scan — the others may still have everything.
                logger.exception(
                    "content_source_raised", source=source.name.value, error=str(outcome)
                )
                results.append(SourceResult.unavailable(source.name))
            else:
                results.append(outcome)
        return results

    async def _persist(
        self,
        db: AsyncSession,
        key: str,
        title: str,
        author: str | None,
        results: list[SourceResult],
        metadata: BookMetadata,
        existing: Book | None,
    ) -> Book:
        """Writes the merged source results into the cache and commits.

        Args:
            db: The database session.
            key: The normalized cache key.
            title: The vision-derived title, used when no catalog matched.
            author: The vision-derived author, likewise.
            results: What each source returned, for the passages.
            metadata: The merged catalog metadata, cover fallback included.
            existing: The stale row being refreshed, if there was one.

        Returns:
            The persisted `Book`.
        """
        matched = [result for result in results if result.matched]

        book = existing or Book(normalized_key=key)
        # Catalog spellings are canonical, but never let a source blank out
        # what the cover told us.
        book.title = metadata.title or title
        book.author = metadata.author or author
        book.description = metadata.description
        book.categories = metadata.categories or None
        book.cover_url = metadata.cover_url
        book.isbn_13 = metadata.isbn_13
        book.isbn_10 = metadata.isbn_10
        book.average_rating = metadata.average_rating
        book.ratings_count = metadata.ratings_count

        passages = [
            TextSource(
                source=result.source.value,
                kind=passage.kind.value,
                heading=passage.heading,
                content=passage.content,
                url=result.url,
                license=result.license,
            )
            for result in matched
            for passage in result.passages
        ]

        # `metadata_found` answers "is there anything to show?", not "does a
        # catalog have a record?". Those come apart constantly: Google Books
        # and Open Library both hold bare entries — title and author, no
        # description, no cover, no subjects — for editions outside the
        # English-language mainstream, and Romanian ones are almost all like
        # that. Keying the flag on `matched` alone marked those books "found"
        # and left the client rendering its success state over an empty
        # result, which reads as a broken app rather than an honest gap.
        #
        # Title and author deliberately don't count as content: they came off
        # the cover, so a book whose only "metadata" is its own title tells
        # the user nothing they didn't photograph.
        book.metadata_found = has_content(metadata, passages)

        # Replace the collection wholesale rather than appending, so a
        # refresh after the TTL expires does not accumulate a second copy
        # of the same article's passages. `delete-orphan` drops the old rows.
        #
        # Both branches assign without reading the collection first: a new
        # Book is still transient here, and a refreshed one was loaded
        # eagerly (`lazy="selectin"`) by `_load_cached`. Touching it in any
        # way that needs a load would emit IO from sync context, which
        # async SQLAlchemy cannot do.
        if existing is None:
            book.text_sources = passages
            db.add(book)
        else:
            book.text_sources[:] = passages

        await db.flush()

        # Only settle the TTL if at least one source actually answered.
        # If every source was unreachable, leave the timestamp untouched so
        # the next scan retries instead of serving an empty entry for a month.
        if any(result.available for result in results):
            book.sources_fetched_at = datetime.utcnow()
        else:
            logger.warning("all_content_sources_unavailable", key=key, title=title)

        # Read the count before committing. The session is configured with
        # `expire_on_commit=False`, so the collection stays loaded — but
        # reading it after a commit that expired anything would trigger a
        # lazy load from sync context, which async SQLAlchemy cannot do.
        passage_count = len(book.text_sources)
        await db.commit()

        logger.info(
            "book_cached",
            key=key,
            book_id=book.id,
            metadata_found=book.metadata_found,
            passages=passage_count,
            sources_matched=[result.source.value for result in matched],
        )
        return book


def has_content(metadata: BookMetadata, passages: list[TextSource]) -> bool:
    """Decides whether a fetch produced anything worth showing the user.

    Public because Module 6b writes `Book` rows too — the candidates it
    discovers in the catalogs — and `metadata_found` has to mean the same
    thing on those rows as on scanned ones. A second, "obviously
    equivalent" check written over there is how the flag would end up with
    two definitions and the client with two success states.

    Args:
        metadata: The merged catalog metadata.
        passages: The prose passages gathered for the RAG corpus.

    Returns:
        `True` if any substantive field was obtained. `title` and `author`
        are excluded on purpose — they originate from the cover, so they
        are not evidence a catalog contributed anything.
    """
    return bool(
        metadata.description
        or metadata.cover_url
        or metadata.categories
        or metadata.average_rating is not None
        or passages
    )


def _merge_metadata(results: list[SourceResult]) -> BookMetadata:
    """Merges per-source metadata, first non-empty value winning.

    `results` arrives in source-priority order, so Google Books' richer
    catalog fields take precedence and Open Library fills the gaps.
    Categories are the exception: the two taxonomies complement each
    other, so they are unioned rather than overwritten.

    Args:
        results: The results of the sources that matched the book.

    Returns:
        The merged metadata.
    """
    merged = BookMetadata()
    categories: list[str] = []

    for result in results:
        source_meta = result.metadata
        for label in source_meta.categories:
            if label not in categories:
                categories.append(label)

        merged = BookMetadata(
            title=merged.title or source_meta.title,
            author=merged.author or source_meta.author,
            description=merged.description or source_meta.description,
            categories=categories,
            cover_url=merged.cover_url or source_meta.cover_url,
            isbn_13=merged.isbn_13 or source_meta.isbn_13,
            isbn_10=merged.isbn_10 or source_meta.isbn_10,
            average_rating=(
                merged.average_rating
                if merged.average_rating is not None
                else source_meta.average_rating
            ),
            ratings_count=(
                merged.ratings_count
                if merged.ratings_count is not None
                else source_meta.ratings_count
            ),
        )

    return merged


def build_data_fetcher() -> BookDataFetcher:
    """Factory for the production `BookDataFetcher`.

    Returns:
        A fetcher over the three official sources, in priority order:
        Google Books (richest metadata), Open Library (CC0 gap-filler),
        Wikipedia (the critical-reception corpus) — plus the cover-image
        fallback that runs when none of them yielded a thumbnail.
    """
    settings = get_settings()
    return BookDataFetcher(
        sources=[
            GoogleBooksSource(settings),
            OpenLibrarySource(settings),
            WikipediaSource(settings),
        ],
        settings=settings,
        cover_fallback=OfficialCoverFallback(settings),
    )

"""Tests for Module 6b: the profile vector, candidate discovery, and ranking.

**These run against a real ephemeral ChromaDB, not a fake store** — the
same reasoning as `test_vector_store.py`. The properties that matter most
here are properties of Chroma's own behaviour: that `$nin` really excludes
the reader's library, that a cosine "distance" really inverts into the
similarity the API reports as a score, and that querying without a filter
really returns the whole collection (which is the *correct* query in this
collection, and forbidden in the other one). A fake store would only prove
that our filter-passing code calls itself.

What is faked is everything with a cost: the catalogs (`FakeCandidateSource`)
and the embedding model (`HashingEmbeddingClient`, whose vectors track
lexical overlap so "the space opera candidate ranked nearest the space
opera the reader liked" is behaviour rather than luck).

The failure modes worth asserting directly are the quiet ones. A
recommendation for a book already on the reader's shelf renders exactly
like a good one. An explanation naming the wrong library book is a fluent
sentence. An author excluded on the strength of one bad rating simply
never appears, and nothing anywhere says why.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path

import chromadb
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.session import Base
from app.models.book import Book, SourceName
from app.models.library import LibraryEntry, ReadingStatus
from app.models.recommendation import RecommendationState
from app.models.user import User
from app.services.profile_store import BookProfile, ChromaProfileStore
from app.services.recommendation_service import (
    RecommendationService,
    build_profile_document,
    reset_discovery_guard,
)
from app.services.sources.base import BookMetadata
from tests.fakes import FakeCandidateSource, HashingEmbeddingClient

# --- Fixture shelf -------------------------------------------------------------
#
# Two clearly separated topics, so a ranking that ignored the profile
# vector would be visible rather than merely suspicious: desert-empire
# space opera on one side, coastal cookery on the other. The vocabulary is
# deliberately non-overlapping, because the offline embedder scores lexical
# overlap and shared filler words would blur the two clusters together.

DESERT_BLURB = (
    "A desert planet where noble houses feud over a rare spice that grants "
    "prescience. An heir crosses the sand, joins the desert tribes and leads "
    "them against the empire."
)
EMPIRE_BLURB = (
    "A galactic empire decays while a mathematician predicts its fall. Traders "
    "and psychologists steer a foundation of scholars through the collapse of "
    "imperial rule across the stars."
)
COOKERY_BLURB = (
    "Coastal kitchens, grilled sardines, lemon and olive oil. Seasonal recipes "
    "from fishing villages, with notes on markets, herbs and the family table."
)
BAKING_BLURB = (
    "Sourdough, rye and pastry from a village bakery. Recipes for bread, tarts "
    "and preserves, with notes on flour, ovens and the family table."
)


def make_book(
    title: str,
    author: str,
    categories: list[str] | None,
    description: str | None,
    sources_fetched_at: datetime | None = None,
) -> Book:
    """A cached book row, as either the fetcher or discovery would store it."""
    return Book(
        normalized_key=f"{title.casefold()}|{(author or '').casefold()}",
        title=title,
        author=author,
        categories=categories,
        description=description,
        metadata_found=True,
        sources_fetched_at=sources_fetched_at,
    )


@pytest.fixture
def profile_store() -> Iterator[ChromaProfileStore]:
    """A profile store over a clean, in-memory Chroma instance.

    `EphemeralClient`s with identical settings share one in-memory system,
    so the explicit `reset` either side is what actually isolates a test.
    """
    client = chromadb.EphemeralClient(
        settings=chromadb.Settings(allow_reset=True, anonymized_telemetry=False)
    )
    client.reset()
    yield ChromaProfileStore(client)
    client.reset()


@pytest.fixture(autouse=True)
def clean_discovery_guard() -> Iterator[None]:
    """Clears the module-level discovery guard around every test.

    The guard has to be module-level — the service is built per request,
    so instance state would guard nothing. The cost is that one test's run
    would otherwise suppress the next one's, and the failure would look
    like a logic bug in whichever test happened to run second.
    """
    reset_discovery_guard()
    yield
    reset_discovery_guard()


@pytest.fixture
def embedder() -> HashingEmbeddingClient:
    """The deterministic offline embedding client."""
    return HashingEmbeddingClient()


@pytest.fixture
async def user_id(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[int]:
    """A persisted reader."""
    async with db_session_factory() as db:
        user = User(email="reader-6b@example.com", hashed_password="x")
        db.add(user)
        await db.commit()
        yield user.id


def build_service(
    embedder: HashingEmbeddingClient,
    store: ChromaProfileStore,
    sources: list[FakeCandidateSource] | None = None,
    spacing: float = 0.0,
    min_discovery_interval: float = 0.0,
) -> RecommendationService:
    """A `RecommendationService` over fakes and the real ephemeral store.

    `spacing` and `min_discovery_interval` both default to `0.0` rather
    than to their configured values. They exist to keep Google Books from
    shedding load under bursts, and no fake sheds anything — paying them
    in every test would add seconds per discovery test for nothing, and
    the minute-long interval would make most of these tests assert the
    opposite of what they mean. The tests that are *about* those two set
    them explicitly.
    """
    return RecommendationService(
        embeddings=embedder,
        profile_store=store,
        sources=list(sources or []),
        settings=get_settings().model_copy(
            update={
                "recommendation_query_spacing_seconds": spacing,
                "recommendation_min_discovery_interval_seconds": min_discovery_interval,
            }
        ),
    )


async def shelve(
    db: AsyncSession,
    user_id: int,
    book: Book,
    rating: int | None = None,
    status: ReadingStatus = ReadingStatus.READ,
) -> Book:
    """Persists a book and puts it in the reader's library."""
    db.add(book)
    await db.flush()
    db.add(
        LibraryEntry(
            user_id=user_id,
            book_id=book.id,
            status=status.value,
            rating=rating,
            scan_count=1,
            first_scanned_at=datetime.utcnow(),
        )
    )
    await db.commit()
    return book


async def seed_pool(
    db: AsyncSession, store: ChromaProfileStore, embedder: HashingEmbeddingClient, books: list[Book]
) -> list[Book]:
    """Persists candidate books and writes their profile vectors directly."""
    for book in books:
        db.add(book)
    await db.commit()

    profiles = []
    for book in books:
        document = build_profile_document(book, 1200)
        assert document is not None
        profiles.append(BookProfile(book_id=book.id, document=document))

    vectors = await embedder.embed([profile.document for profile in profiles])
    await store.upsert(profiles, vectors)
    return books


# --- The profile document ------------------------------------------------------


def test_a_book_with_no_blurb_and_no_genres_gets_no_profile_document() -> None:
    """A bare catalog record is left out of the collection, not embedded thin.

    Romanian editions are almost all bare records — title and author and
    nothing else. Embedding one produces a vector encoding little more
    than the shapes of two proper nouns, which then ranks against real
    candidates arbitrarily. Absent is more honest than arbitrary.
    """
    bare = make_book("Baltagul", "Mihail Sadoveanu", None, None)
    assert build_profile_document(bare, 1200) is None


def test_the_profile_document_is_built_the_same_way_for_every_book() -> None:
    """Title, genres and blurb, in that order, however the book was obtained.

    The reader's profile is an average of library books' vectors and it is
    compared against candidates' vectors. If the two were assembled
    differently — one from a full record, one from a search hit — cosine
    similarity would be measuring the assembly as much as the book, and it
    would do so silently.
    """
    scanned = make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB)
    discovered = make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB)

    assert build_profile_document(scanned, 1200) == build_profile_document(discovered, 1200)


def test_the_profile_document_truncates_a_long_blurb() -> None:
    """Past a paragraph a blurb describes the publisher, not the book."""
    book = make_book("Long", "Author", ["Fiction"], "word " * 2000)
    document = build_profile_document(book, 100)

    assert document is not None
    assert len(document) < 300


# --- Cold start ----------------------------------------------------------------


async def test_nothing_rated_yet_returns_no_recommendations_and_no_catalog_calls(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A reader who has rated nothing gets an honest empty answer.

    `based_on=0` is what lets the client ask for a rating instead of
    saying "no recommendations", which would read as a broken feature on a
    brand-new account. And discovery must not fire: there are no
    preferences to seed it with, so every query would be a wasted request
    against a quota.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(db, user_id, make_book("Dune", "Frank Herbert", ["Sci-fi"], DESERT_BLURB))
        result = await service.recommend(db, user_id)

    assert result.based_on == 0
    assert result.recommendations == []
    assert source.queries == []


async def test_liked_books_the_catalogs_describe_thinly_count_as_a_cold_start(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """Rating a bare record 5 still leaves nothing to build a profile from.

    Reported as `based_on=0` rather than as an empty list, because for the
    reader the situation is the same as never having rated anything: there
    is no usable signal, and the screen that asks for one is the right one.
    """
    service = build_service(embedder, profile_store, [FakeCandidateSource(SourceName.GOOGLE_BOOKS)])

    async with db_session_factory() as db:
        # The bare record the catalogs actually hold for such an edition:
        # a title and an author, nothing else. Preferences still derive an
        # author from it — `LibraryPreferences.based_on` is 1 here — which
        # is exactly why the two numbers are different fields.
        await shelve(db, user_id, make_book("Baltagul", "Sadoveanu", None, None), rating=5)
        result = await service.recommend(db, user_id)

    assert result.based_on == 0
    assert result.recommendations == []


# --- Ranking -------------------------------------------------------------------


async def test_recommendations_rank_by_similarity_to_what_the_reader_liked(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """The space opera outranks the cookbook, and the score is a similarity.

    The whole feature in one assertion. `score` is checked as *descending*
    rather than merely present: Chroma reports distance, and an inverted
    comparison anywhere in the chain would recommend the least similar
    books with no error raised at any point.
    """
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        liked = make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB)
        await shelve(db, user_id, liked, rating=5)

        await seed_pool(
            db,
            profile_store,
            embedder,
            [
                make_book("Foundation", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB),
                make_book("Coastal Table", "A Cook", ["Cooking"], COOKERY_BLURB),
            ],
        )
        result = await service.recommend(db, user_id)

    titles = [entry.book.title for entry in result.recommendations]
    assert titles[0] == "Foundation"
    assert result.based_on == 1

    scores = [entry.score for entry in result.recommendations]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)


async def test_books_already_in_the_library_are_never_recommended(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """The exclusion is pushed into Chroma, and this proves Chroma honours it.

    A recommendation for a book on the reader's own shelf renders exactly
    like a good one — same card, same explanation, nothing to notice. The
    book excluded here is the reader's *highest-rated* one, so it is by
    construction the nearest thing in the collection to their profile
    vector and would top the list if the filter did nothing.
    """
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        liked = make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB)
        shelved = make_book("Foundation", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB)
        await shelve(db, user_id, liked, rating=5)
        await shelve(db, user_id, shelved, rating=None, status=ReadingStatus.WANT_TO_READ)

        # Both library books get vectors, exactly as the pipeline would
        # leave them — the filter is the only thing keeping them out.
        await seed_pool(db, profile_store, embedder, [])
        for book in (liked, shelved):
            document = build_profile_document(book, 1200)
            assert document is not None
            vectors = await embedder.embed([document])
            await profile_store.upsert([BookProfile(book_id=book.id, document=document)], vectors)

        await seed_pool(
            db,
            profile_store,
            embedder,
            [make_book("Village Bakery", "B Baker", ["Cooking"], BAKING_BLURB)],
        )
        result = await service.recommend(db, user_id)

    recommended_ids = {entry.book.id for entry in result.recommendations}
    assert liked.id not in recommended_ids
    assert shelved.id not in recommended_ids


async def test_a_disliked_author_is_excluded_from_recommendations(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """Rating one book 1 keeps that author out, however well they rank."""
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB),
            rating=5,
        )
        await shelve(
            db,
            user_id,
            make_book("Empire Notes", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB),
            rating=1,
        )
        await seed_pool(
            db,
            profile_store,
            embedder,
            [make_book("Foundation", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB)],
        )
        result = await service.recommend(db, user_id)

    assert [entry.book.title for entry in result.recommendations] == []


async def test_an_author_both_liked_and_disliked_is_not_excluded(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A like beats a dislike — otherwise a favourite author disappears.

    "I loved one Asimov and disliked another" is a fact about those two
    books, not about Asimov. Excluding him would delete the reader's own
    favourite author from their suggestions, and this is the specific
    reason the dislike signal is an overridable *set* rather than a vector
    subtracted from the profile: a set can defer legibly, arithmetic
    cannot.
    """
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Empire Rising", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB),
            rating=5,
        )
        await shelve(
            db,
            user_id,
            make_book("Empire Notes", "Isaac Asimov", ["Science fiction"], DESERT_BLURB),
            rating=2,
        )
        await seed_pool(
            db,
            profile_store,
            embedder,
            [make_book("Foundation", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB)],
        )
        result = await service.recommend(db, user_id)

    assert [entry.book.title for entry in result.recommendations] == ["Foundation"]


async def test_a_middling_rating_neither_recommends_nor_excludes(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A 3 is a shrug, and a shrug must not delete a genre.

    If 3 counted as a dislike, finishing one indifferent science-fiction
    novel would exclude every science-fiction recommendation the reader
    could ever get.
    """
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB),
            rating=5,
        )
        await shelve(
            db,
            user_id,
            make_book("Empire Notes", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB),
            rating=3,
        )
        await seed_pool(
            db,
            profile_store,
            embedder,
            [make_book("Foundation", "Someone Else", ["Science fiction"], EMPIRE_BLURB)],
        )
        result = await service.recommend(db, user_id)

    assert [entry.book.title for entry in result.recommendations] == ["Foundation"]


# --- Explanations --------------------------------------------------------------


async def test_the_explanation_names_the_liked_book_it_is_actually_nearest_to(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """Two liked books, two candidates, each explained by the right one.

    The point of computing the explanation rather than generating it. With
    a model, both sentences would be fluent and one would be false, with
    nothing to check it against — and a wrong reason beside a right
    recommendation is worse than no reason, because the reader trusts it.
    """
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        dune = make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB)
        cookery = make_book("Coastal Table", "A Cook", ["Cooking"], COOKERY_BLURB)
        await shelve(db, user_id, dune, rating=5)
        await shelve(db, user_id, cookery, rating=5)

        await seed_pool(
            db,
            profile_store,
            embedder,
            [
                make_book("Foundation", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB),
                make_book("Village Bakery", "B Baker", ["Cooking"], BAKING_BLURB),
            ],
        )
        result = await service.recommend(db, user_id)

    by_title = {entry.book.title: entry for entry in result.recommendations}
    assert "Dune" in by_title["Foundation"].explanation
    assert by_title["Foundation"].because_of_book_id == dune.id
    assert "Coastal Table" in by_title["Village Bakery"].explanation
    assert by_title["Village Bakery"].because_of_book_id == cookery.id


async def test_the_explanation_names_a_shared_genre_when_there_is_one(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """ "Because you liked X" alone says nothing about *this* book."""
    service = build_service(embedder, profile_store)

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Fiction", "Space opera"], DESERT_BLURB),
            rating=5,
        )
        await seed_pool(
            db,
            profile_store,
            embedder,
            [make_book("Foundation", "Isaac Asimov", ["Fiction", "Space opera"], EMPIRE_BLURB)],
        )
        result = await service.recommend(db, user_id)

    explanation = result.recommendations[0].explanation
    assert "space opera" in explanation
    # "Fiction" is shared too, and says nothing — it is the shelf, not the book.
    assert "fiction" not in explanation.replace("science fiction", "")


# --- Discovery -----------------------------------------------------------------


async def test_discovery_persists_candidates_as_books_with_no_fetched_sources(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A discovered book is a real row, but never a *settled* one.

    `sources_fetched_at` staying `None` is what `BookDataFetcher._is_fresh`
    reads. Setting it here would make a search hit look like a completed
    Module 4 fetch, and scanning that book would serve a bare catalog card
    with no passages behind it — for the full 30-day TTL, with a summary
    endpoint that has nothing to retrieve over.
    """
    source = FakeCandidateSource(
        SourceName.GOOGLE_BOOKS,
        {
            "subject:Science fiction": [
                BookMetadata(
                    title="Foundation",
                    author="Isaac Asimov",
                    description=EMPIRE_BLURB,
                    categories=["Science fiction"],
                )
            ]
        },
    )
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB),
            rating=5,
        )
        result = await service.recommend(db, user_id)

    async with db_session_factory() as db:
        stored = await db.scalar(select(Book).where(Book.title == "Foundation"))

    assert stored is not None
    assert stored.sources_fetched_at is None
    assert stored.metadata_found is True
    assert [entry.book.title for entry in result.recommendations] == ["Foundation"]


async def test_discovery_never_overwrites_a_book_that_was_already_cached(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A search hit must not flatten a fully-fetched row.

    An existing row may carry gathered passages, a generated summary and a
    cover recovered through the fallback chain. A bare discovery result
    has none of that, and letting it win would quietly downgrade a book
    every time someone else's recommendations happened to surface it.
    """
    fetched_at = datetime.utcnow() - timedelta(days=1)
    source = FakeCandidateSource(
        SourceName.GOOGLE_BOOKS,
        {
            "subject:Science fiction": [
                BookMetadata(title="Foundation", author="Isaac Asimov", description="thin")
            ]
        },
    )
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB),
            rating=5,
        )
        existing = make_book(
            "Foundation", "Isaac Asimov", ["Science fiction"], EMPIRE_BLURB, fetched_at
        )
        db.add(existing)
        await db.commit()

        await service.recommend(db, user_id)

    async with db_session_factory() as db:
        rows = list(await db.scalars(select(Book).where(Book.title == "Foundation")))

    assert len(rows) == 1
    assert rows[0].description == EMPIRE_BLURB
    assert rows[0].sources_fetched_at == fetched_at


async def test_discovery_merges_the_same_book_from_two_catalogs(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """One row, characterised by Google's blurb *and* Open Library's subjects.

    The two catalogs return the same work under the same title constantly,
    and each carries what the other lacks. Keeping whichever answered first
    would halve the document a candidate is ranked on, at random.
    """
    google = FakeCandidateSource(
        SourceName.GOOGLE_BOOKS,
        {
            "subject:Science fiction": [
                BookMetadata(
                    title="Foundation",
                    author="Isaac Asimov",
                    description=EMPIRE_BLURB,
                    categories=["Fiction"],
                )
            ]
        },
    )
    open_library = FakeCandidateSource(
        SourceName.OPEN_LIBRARY,
        {
            "subject:Science fiction": [
                BookMetadata(
                    title="FOUNDATION",
                    author="Isaac Asimov",
                    categories=["Psychohistory", "Galactic empire"],
                    cover_url="https://example.test/foundation.jpg",
                )
            ]
        },
    )
    service = build_service(embedder, profile_store, [google, open_library])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Science fiction"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

    async with db_session_factory() as db:
        rows = list(await db.scalars(select(Book).where(Book.title.ilike("foundation"))))

    assert len(rows) == 1
    assert rows[0].description == EMPIRE_BLURB
    assert rows[0].cover_url == "https://example.test/foundation.jpg"
    assert rows[0].categories is not None
    assert "Psychohistory" in rows[0].categories


async def test_generic_catalog_labels_are_never_used_as_discovery_queries(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """`subject:"Fiction"` describes the shelf, not the book.

    Google Books tags most novels `Fiction`. Seeding discovery with it
    returns an arbitrary slice of everything ever published, which then
    ranks badly against the profile and crowds out the specific subjects
    Open Library contributed.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Fiction", "Space opera"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

    assert "subject:Fiction" not in source.queries
    assert "subject:Space opera" in source.queries


async def test_a_fresh_pool_is_not_rediscovered_but_a_changed_taste_is(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """The TTL suppresses the second run; a new rating overrides the TTL.

    Both triggers are needed. The clock alone would serve yesterday's pool
    for a whole day after the reader rated their first book in a genre
    they had never touched — the exact moment their recommendations should
    change most.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)
        first_run = list(source.queries)

        await service.recommend(db, user_id)
        assert source.queries == first_run, "a fresh pool must not be rediscovered"

        await shelve(
            db,
            user_id,
            make_book("Coastal Table", "A Cook", ["Cooking"], COOKERY_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

    assert "subject:Cooking" in source.queries


async def test_an_unreachable_catalog_is_retried_rather_than_recorded_as_a_run(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """An outage is not an answer — same rule as `BookDataFetcher`.

    Recording it would pin an empty pool for the whole TTL: the reader
    would get no recommendations for a day because a catalog was briefly
    down, and rescanning or re-rating would not shake it loose.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS, available=False)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)
        state = await db.scalar(
            select(RecommendationState).where(RecommendationState.user_id == user_id)
        )
        assert state is not None
        assert state.refreshed_at is None

        first_run = len(source.queries)
        await service.recommend(db, user_id)

    assert len(source.queries) > first_run, "an outage must be retried on the next request"


async def test_a_source_that_raises_does_not_deny_the_reader_every_suggestion(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A bug in one catalog client is contained, exactly as in Module 4."""
    broken = FakeCandidateSource(SourceName.GOOGLE_BOOKS, raises=RuntimeError("boom"))
    service = build_service(embedder, profile_store, [broken])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera"], DESERT_BLURB),
            rating=5,
        )
        await seed_pool(
            db,
            profile_store,
            embedder,
            [make_book("Foundation", "Isaac Asimov", ["Space opera"], EMPIRE_BLURB)],
        )
        result = await service.recommend(db, user_id)

    assert [entry.book.title for entry in result.recommendations] == ["Foundation"]


async def test_queries_to_one_catalog_are_paced_not_fired_together(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """Seeds reach a source one at a time, spaced apart.

    Not a style preference — measured against the real API. The first
    implementation fanned every seed out at once and Google Books answered
    503 to 16 of 20 requests; the identical queries paced a second apart
    returned 200 on 5 of 5. Google sheds load on burst rate per key, and
    the request shape made no measurable difference at all.

    Nothing before this module fanned out: the scan path queries three
    different hosts concurrently but asks each exactly one question.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS)
    service = build_service(embedder, profile_store, [source], spacing=0.05)

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera", "Adventure"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

    assert len(source.queries) >= 2, "needs at least two seeds to be a pacing test"
    gaps = [
        later - earlier
        for earlier, later in zip(source.call_times, source.call_times[1:], strict=False)
    ]
    assert all(gap >= 0.04 for gap in gaps), f"queries were not paced: {gaps}"


async def test_a_catalog_that_goes_down_mid_run_stops_being_queried(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """One unavailable answer ends that source's run, it does not skip past it.

    By the time a result reports `available=False` the HTTP layer has
    already retried it, so it means the catalog is down *now* — and each
    remaining query would spend its whole timeout rediscovering that.
    openlibrary.org has extended outages during which every request hangs
    to the timeout; without this, one of them holds a synchronous request
    for minutes.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS, unavailable_after=1)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera", "Adventure"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

    # One good answer, one that reported the outage, then nothing.
    assert len(source.queries) == 2


async def test_a_degraded_run_is_retried_within_the_hour_not_the_day(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A pool built while a catalog was down expires on the short TTL.

    The bug this prevents: `reached` was true because *something*
    answered, so the run was recorded as complete and the half-built pool
    was pinned for a full day. One bad minute would have cost the reader a
    day of thin recommendations, with neither rescanning nor re-rating
    able to shake it loose. Same reasoning as Module 4's short TTL for an
    empty book.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS, unavailable_after=1)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera", "Adventure"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

        state = await db.scalar(
            select(RecommendationState).where(RecommendationState.user_id == user_id)
        )
        assert state is not None
        assert state.complete is False
        assert state.refreshed_at is not None, "a partial run still widened the pool"

        # Two hours on: past the degraded TTL, far inside the full one.
        state.refreshed_at = datetime.utcnow() - timedelta(hours=2)
        await db.commit()

        before = len(source.queries)
        await service.recommend(db, user_id)

    assert len(source.queries) > before, "a degraded run must be retried within the hour"


async def test_a_clean_run_is_not_retried_two_hours_later(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """The short TTL applies to degraded runs only, not to every run.

    The other half of the pair above: without this, the fix would have
    quietly turned the 24-hour TTL into a one-hour one for everybody and
    put discovery back on the catalogs many times a day.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS)
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

        state = await db.scalar(
            select(RecommendationState).where(RecommendationState.user_id == user_id)
        )
        assert state is not None
        assert state.complete is True

        state.refreshed_at = datetime.utcnow() - timedelta(hours=2)
        await db.commit()

        before = len(source.queries)
        await service.recommend(db, user_id)

    assert len(source.queries) == before


async def test_two_overlapping_requests_do_not_both_discover(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """Only one of two concurrent requests runs discovery; the other ranks.

    Observed in production, not imagined. Every library write invalidates
    the client's recommendation query, and a tab screen stays mounted — so
    rating a book three times in three seconds fired three refetches. Each
    changed the derived preferences, so each bypassed the TTL and started
    its own discovery: three concurrent runs, a dozen simultaneous catalog
    requests, and Google Books shedding load exactly as it had before the
    pacing fix. Pacing queries *within* a run does nothing about N runs.

    The loser skips rather than waits — queueing would make the duplicate
    request as slow as the original for a pool it is about to be handed.
    """
    source = FakeCandidateSource(
        SourceName.GOOGLE_BOOKS,
        {
            "author:Frank Herbert": [
                BookMetadata(
                    title="Children of Dune",
                    author="Frank Herbert",
                    description=EMPIRE_BLURB,
                    categories=["Space opera"],
                )
            ]
        },
    )
    service = build_service(embedder, profile_store, [source])

    async with db_session_factory() as setup:
        await shelve(
            setup,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera"], DESERT_BLURB),
            rating=5,
        )

    async def request() -> None:
        async with db_session_factory() as db:
            await service.recommend(db, user_id)

    await asyncio.gather(request(), request())

    assert len(source.queries) == len(
        set(source.queries)
    ), f"discovery ran more than once concurrently: {source.queries}"


async def test_discovery_does_not_restart_on_every_rapid_rating(
    db_session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """A taste change is honoured, but not more often than the floor allows.

    The sequential half of the same problem: three ratings a second apart
    each change the fingerprint, so the TTL never suppresses anything and
    every one of them starts a fresh discovery run.

    The reader loses nothing that matters — the profile vector is rebuilt
    from their ratings on *every* request, so the ranking is current
    immediately. Only the candidate pool waits.
    """
    source = FakeCandidateSource(SourceName.GOOGLE_BOOKS)
    service = build_service(embedder, profile_store, [source], min_discovery_interval=60.0)

    async with db_session_factory() as db:
        await shelve(
            db,
            user_id,
            make_book("Dune", "Frank Herbert", ["Space opera"], DESERT_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)
        after_first = len(source.queries)
        assert after_first > 0, "the first request must discover"

        # A brand-new taste, which changes the seed and defeats the TTL.
        await shelve(
            db,
            user_id,
            make_book("Coastal Table", "A Cook", ["Cooking"], COOKERY_BLURB),
            rating=5,
        )
        await service.recommend(db, user_id)

    assert len(source.queries) == after_first, "discovery restarted inside the interval"


async def test_a_candidate_another_run_just_stored_does_not_fail_the_request(
    tmp_path: Path,
    embedder: HashingEmbeddingClient,
    profile_store: ChromaProfileStore,
) -> None:
    """The check-then-insert race resolves to a skip, not a 500.

    Reading the known keys and inserting the new ones straddles an
    `await`, so two overlapping runs both see a key as absent and both
    insert it. The loser used to hit
    `UNIQUE constraint failed: books.normalized_key` — and because the
    inserts were one batch, that single collision discarded every other
    row and failed the whole request with a 500.

    `_claim_discovery` makes this rare; the savepoint is what makes it
    harmless, and it has to, because that guard is in-process and would
    not survive a second worker.

    Runs against a **file-backed** database rather than the suite's shared
    in-memory one. The `StaticPool` fixture hands every session the same
    DBAPI connection, so two "concurrent" sessions are one transaction and
    neither the collision nor the recovery is real there. On a file, they
    get a connection each — which is also what production has, and is the
    only configuration in which this test could ever have failed.

    The guard is deliberately left disabled so the runs really do overlap.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        candidate = BookMetadata(
            title="Children of Dune",
            author="Frank Herbert",
            description=EMPIRE_BLURB,
            categories=["Space opera"],
        )
        service = build_service(embedder, profile_store)

        async def persist() -> int:
            async with sessions() as db:
                return await service._persist_candidates(db, [candidate])

        stored = await asyncio.gather(persist(), persist())

        # Exactly one run created it; the other found it already there.
        assert sorted(stored) == [0, 1]

        async with sessions() as db:
            rows = list(await db.scalars(select(Book).where(Book.title == "Children of Dune")))
        assert len(rows) == 1
    finally:
        await engine.dispose()


# --- The endpoint --------------------------------------------------------------


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    """Registers and logs a user in, returning an auth header for them."""
    password = "super-secret-password"
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_recommendations_require_authentication(client: AsyncClient) -> None:
    """The endpoint is about *this* reader, so there is no anonymous answer."""
    response = await client.get("/users/me/recommendations")
    assert response.status_code == 401


async def test_a_new_account_gets_a_200_with_an_empty_shelf(client: AsyncClient) -> None:
    """An empty result is a `200`, never a 404 or an error.

    "Nothing to suggest yet" is the ordinary state of a new account. The
    client distinguishes it from a failure by the status code, and from
    "the catalogs had nothing new" by `based_on`.
    """
    headers = await _register(client, "empty-6b@example.com")
    response = await client.get("/users/me/recommendations", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["recommendations"] == []
    assert body["based_on"] == 0


# --- The profile store itself --------------------------------------------------


async def test_the_profile_store_refuses_to_pair_a_book_with_another_vector(
    profile_store: ChromaProfileStore,
) -> None:
    """A length mismatch is caught, not written.

    Silently misaligned vectors would make every recommendation
    confidently wrong, with nothing anywhere to notice it.
    """
    with pytest.raises(ValueError):
        await profile_store.upsert([BookProfile(book_id=1, document="a")], [[0.1], [0.2]])


async def test_the_profile_store_returns_similarity_and_the_vector_it_ranked_on(
    profile_store: ChromaProfileStore, embedder: HashingEmbeddingClient
) -> None:
    """Chroma reports distance; the store reports a score, and the embedding.

    Returning the vector alongside is what lets the explanation be computed
    without a second round trip per recommendation.
    """
    profiles = [BookProfile(book_id=1, document=DESERT_BLURB)]
    vectors = await embedder.embed([DESERT_BLURB])
    await profile_store.upsert(profiles, vectors)

    found = await profile_store.query(vectors[0], n_results=5, exclude_book_ids=set())

    assert len(found) == 1
    assert found[0].book_id == 1
    assert found[0].score == pytest.approx(1.0, abs=1e-4)
    assert len(found[0].vector) == embedder.dimensions


async def test_the_profile_store_query_excludes_the_ids_it_is_given(
    profile_store: ChromaProfileStore, embedder: HashingEmbeddingClient
) -> None:
    """The `$nin` filter is Chroma's, so it is tested against real Chroma.

    An unfiltered query returning everything is the *correct* behaviour in
    this collection — the inverse of `vector_store.py`'s invariant — so
    both directions are asserted here, together, to keep the contrast on
    the record.
    """
    documents = {1: DESERT_BLURB, 2: EMPIRE_BLURB, 3: COOKERY_BLURB}
    profiles = [BookProfile(book_id=key, document=text) for key, text in documents.items()]
    vectors = await embedder.embed([profile.document for profile in profiles])
    await profile_store.upsert(profiles, vectors)

    unfiltered = await profile_store.query(vectors[0], n_results=10, exclude_book_ids=set())
    assert {found.book_id for found in unfiltered} == {1, 2, 3}

    filtered = await profile_store.query(vectors[0], n_results=10, exclude_book_ids={1, 3})
    assert {found.book_id for found in filtered} == {2}


async def test_the_profile_store_skips_books_it_has_no_vector_for(
    profile_store: ChromaProfileStore, embedder: HashingEmbeddingClient
) -> None:
    """A book too thinly catalogued to embed is absent, not an error.

    `_ensure_profiles` asks for every liked book's vector before deciding
    which need embedding, so "not stored" has to be an ordinary answer.
    """
    vectors = await embedder.embed([DESERT_BLURB])
    await profile_store.upsert([BookProfile(book_id=7, document=DESERT_BLURB)], vectors)

    found = await profile_store.vectors_for([7, 8, 9])

    assert set(found) == {7}
    assert len(found[7]) == embedder.dimensions

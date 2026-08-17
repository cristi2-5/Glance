"""Content-based recommendations, from the reader's own ratings.

Purely content-based: one user, guaranteed cold start, no collaborative
filtering to do. The pipeline, end to end:

1. **Derive** — the reader's favourite genres and authors, from books they
   rated 4+ (`library_service.derive_preferences`).
2. **Discover** — ask Google Books and Open Library for books in those
   genres and by those authors, and persist what comes back as ordinary
   `Book` rows. Skipped when the pool was already built for these same
   preferences recently; see `RecommendationState`.
3. **Profile** — embed every book locally with `nomic-embed-text`, then
   average the vectors of the reader's liked books, weighted by rating.
4. **Rank** — one similarity search over `book_profiles`, excluding the
   reader's whole library, then the dislike exclusions and a noise floor.
5. **Explain** — name the liked book each suggestion is nearest to.

Five decisions in here are load-bearing, and each has a failure mode worth
stating.

**Discovery paces its queries; it does not fan them out.** This one was
learned the hard way and then measured. The first implementation fired
every seed at every source simultaneously — up to ten concurrent requests
— and Google Books answered 503 to most of them. Fired together, five
queries failed 16 times out of 20; paced one second apart, the identical
queries succeeded 5 out of 5. Google sheds load on *burst rate per key*,
and the request shape (`printType`, `orderBy`, `maxResults`) was measured
to make no difference at all.

Nothing before this module provoked it: the scan path queries three
different hosts at once but asks each exactly one question. Discovery was
the first code here to ask one host five questions at once, and it is the
reason `recommendation_query_spacing_seconds` exists. A source is also
dropped for the rest of the run at its first unavailable answer — with
openlibrary.org fully down, retrying every remaining seed would hold a
synchronous request for minutes to learn nothing.

**Candidates cannot come from ChromaDB's chunk collection.** It holds only
books the reader has already scanned — precisely the set that must be
filtered out — so the honest answer from it would always be zero
recommendations. The cold start here is not "no ratings", it is "nothing
to choose from", and only the catalogs can fix that.

**A discovered candidate is persisted as a real `Book` row**, with
`sources_fetched_at` left `None`. That gives it a stable id the client can
link to, lets its vector be cached instead of recomputed per request, and
makes "Want to read" the ordinary library write rather than a second path.
The null timestamp is what keeps it honest: `BookDataFetcher._is_fresh`
treats such a row as stale, so scanning that book later fetches its
sources properly instead of serving a search hit as a completed lookup.

**Low ratings feed an exclusion set, never the vector.** Subtracting a
disliked book's vector from the profile produces a direction that no
longer corresponds to anything the reader said, and therefore a suggestion
that cannot be explained — while explanation is half of what makes a
recommendation usable. Excluding an author or a genre outright is blunt,
but it is *statable*. And it defers to the likes: an author on both lists
stays, because "I loved one and disliked another" is a fact about the
books, not about the author.

**The explanation is computed, not generated.** It names the nearest
contributing book in the reader's library, which is a fact we can check. A
model asked to justify a recommendation would write something fluent about
a book it was never shown — the same class of failure Module 5 spends its
verification step on, with no citation to check it against.
"""

import asyncio
import math
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.book import Book
from app.models.library import LibraryEntry
from app.models.recommendation import RecommendationState
from app.schemas.library import LibraryBook, LibraryPreferences
from app.schemas.recommendation import Recommendation, RecommendationList
from app.services import library_service
from app.services.data_fetcher import has_content, normalize_key
from app.services.embeddings import EmbeddingClient, get_embedding_client
from app.services.profile_store import (
    BookProfile,
    ProfileStore,
    ScoredBook,
    get_profile_store,
)
from app.services.sources.base import (
    BookMetadata,
    CandidateSource,
    DiscoveryQuery,
    DiscoveryResult,
)
from app.services.sources.google_books import GoogleBooksSource
from app.services.sources.open_library import OpenLibrarySource

logger = structlog.get_logger(__name__)

#: Catalog labels too broad to be a discovery query. Google Books tags most
#: novels `Fiction` and most children's books `Juvenile Fiction` — top-level
#: BISAC headings that describe the shelf, not the book. Seeding discovery
#: with one returns an arbitrary slice of everything ever published, which
#: then ranks poorly against the profile and crowds out the specific
#: subjects Open Library contributed ("Dystopias", "Desert survival").
#: Folded and matched exactly, so "Science fiction" is untouched.
_GENERIC_SUBJECTS = frozenset(
    {
        "fiction",
        "juvenile fiction",
        "juvenile nonfiction",
        "nonfiction",
        "general",
        "literary collections",
        "literary criticism",
        "books",
    }
)


# Which readers have a discovery run in flight, and when each last began.
#
# **Module-level because the service is built per request.**
# `build_recommendation_service` is a FastAPI dependency, so instance state
# would be a fresh, empty guard on every call and would guard nothing. The
# expensive collaborators it wraps (`get_embedding_client`,
# `get_profile_store`) are `lru_cache`d for the same reason.
#
# In-process only, which is correct for this deployment: one uvicorn
# worker on one laptop. A second worker would need this in SQLite, and the
# `_persist_candidates` savepoint is what keeps that case *correct* rather
# than merely rare — this guard is an efficiency measure, not the
# integrity mechanism.
_discovery_running: set[int] = set()
_discovery_last_started: dict[int, float] = {}


def _claim_discovery(user_id: int, min_interval_seconds: float) -> bool:
    """Takes the right to run discovery for a reader, if it is free.

    Two things this prevents, both observed in production rather than
    imagined:

    1. **Overlapping runs.** Every library write invalidates the client's
       recommendation query, and a tab screen stays mounted, so rating a
       book three times in three seconds fired three refetches. Each
       changed the derived preferences, so each bypassed the TTL and
       started its own discovery — three concurrent runs, a dozen
       simultaneous catalog requests, and Google Books shedding load
       again. Pacing queries *within* a run does nothing about N runs.
    2. **Back-to-back runs.** The same rapid re-rating sequentially, where
       overlap alone would not catch it.

    Deliberately a *skip*, not a wait: a caller that cannot discover ranks
    against the pool as it stands, which is milliseconds. Queueing would
    make the duplicate request as slow as the original for a result it is
    about to receive anyway.

    **The reader's ranking is never stale because of this.** The profile
    vector is recomputed from their ratings on every request; only the
    *candidate pool* lags, by at most `min_interval_seconds`.

    Check and claim happen with no `await` between them, so the event loop
    cannot interleave two callers here.

    Args:
        user_id: The reader discovery would run for.
        min_interval_seconds: How long after a run starts before another
            may begin.

    Returns:
        `True` if the caller may discover, and must then call
        `_release_discovery`.
    """
    if user_id in _discovery_running:
        return False

    started = _discovery_last_started.get(user_id)
    now = time.monotonic()
    if started is not None and now - started < min_interval_seconds:
        return False

    _discovery_running.add(user_id)
    _discovery_last_started[user_id] = now
    return True


def _release_discovery(user_id: int) -> None:
    """Releases the discovery claim taken by `_claim_discovery`.

    Args:
        user_id: The reader whose run has finished.
    """
    _discovery_running.discard(user_id)


def reset_discovery_guard() -> None:
    """Clears the in-process discovery guard.

    For tests: the guard is module-level (see above), so without this one
    test's run would suppress the next one's and the failure would look
    like a logic bug in whichever test happened to run second.
    """
    _discovery_running.clear()
    _discovery_last_started.clear()


@dataclass(frozen=True)
class _SourceRun:
    """What one catalog produced during one discovery run.

    Attributes:
        results: The answers it gave, one per query it got through.
        reached: Whether it answered at all. `False` means the source was
            down from the first query.
        complete: Whether it answered *every* query. `False` means the run
            stopped early — see `_discover_from_source`.
    """

    results: list[DiscoveryResult]
    reached: bool
    complete: bool


@dataclass(frozen=True)
class _DiscoveryOutcome:
    """What a whole discovery run achieved, across every source.

    The three states the caller distinguishes:

    - `reached=False` — nothing answered. Not recorded as a run at all, so
      the next request retries rather than serving an unbuilt pool.
    - `reached=True, complete=False` — degraded. Recorded, but on the short
      TTL, so it self-heals within the hour.
    - `complete=True` — the run we meant to make. Full TTL.

    Attributes:
        reached: Whether any source answered any query.
        complete: Whether every source answered every query.
        stored: How many new `Book` rows were created.
    """

    reached: bool
    complete: bool
    stored: int


def _rating_weight(rating: int) -> float:
    """Returns how much a rating contributes to the profile vector.

    A 4 counts once and a 5 counts twice: the reader distinguished the two,
    so the profile should as well, and anchoring the scale at the favourite
    floor keeps that proportional without a tuning table.

    Args:
        rating: The reader's rating, at or above the favourite floor.

    Returns:
        The weight, `1.0` at the floor and rising by one per point.
    """
    return float(rating - library_service.FAVORITE_RATING_FLOOR + 1)


def build_profile_document(book: Book, max_chars: int) -> str | None:
    """Builds the text a book's profile vector is computed from.

    **Every book in `book_profiles` must go through this function.** The
    reader's profile vector is an average of library books' vectors and it
    is compared against candidates' vectors; if the two were assembled from
    differently-shaped text — one a bare title, the other a title plus a
    blurb — cosine similarity would be measuring the assembly as much as
    the book. That failure is silent and produces a plausible ranking.

    Args:
        book: The book to characterise.
        max_chars: Ceiling on the description text included.

    Returns:
        The document, or `None` when the catalogs gave us nothing but the
        book's own title and author. Such a book is deliberately left out
        of the collection rather than embedded on two words: its vector
        would encode little more than the shapes of some proper nouns, and
        it would then rank against readers and candidates arbitrarily,
        which is worse than being absent. Romanian editions the catalogs
        hold bare records for are the normal case here.
    """
    if not book.description and not book.categories:
        return None

    lines = [book.title if not book.author else f"{book.title} by {book.author}."]
    if book.categories:
        lines.append(f"Genres: {', '.join(book.categories)}.")
    if book.description:
        lines.append(book.description.strip()[:max_chars])
    return "\n".join(lines)


class RecommendationService:
    """Builds a reader's recommendations from their ratings.

    Every collaborator is injected so the whole pipeline runs against fakes
    in tests — no network, no Ollama.
    """

    def __init__(
        self,
        embeddings: EmbeddingClient,
        profile_store: ProfileStore,
        sources: list[CandidateSource],
        settings: Settings,
    ) -> None:
        self._embeddings = embeddings
        self._store = profile_store
        self._sources = sources
        self._settings = settings

    async def recommend(
        self, db: AsyncSession, user_id: int, limit: int | None = None
    ) -> RecommendationList:
        """Returns books this reader might like next, most similar first.

        Args:
            db: The current database session.
            user_id: The reader.
            limit: Maximum recommendations, defaulting to
                `Settings.recommendation_default_limit`.

        Returns:
            The recommendations plus `based_on`, the number of liked books
            that actually fed the profile vector. An empty list with
            `based_on=0` is the cold start and not an error; an empty list
            with `based_on>0` means the catalogs had nothing new. See
            `RecommendationList`.

        Raises:
            ExternalServiceUnavailable: If the local embedding model could
                not be reached. Propagated rather than swallowed, so the
                client retries instead of rendering "no recommendations"
                over an outage.
        """
        limit = limit or self._settings.recommendation_default_limit

        preferences = await library_service.derive_preferences(db, user_id)
        if preferences.based_on == 0:
            logger.info("recommendations_cold_start", user_id=user_id)
            return RecommendationList(recommendations=[], based_on=0)

        await self._refresh_candidates_if_stale(db, user_id, preferences)

        rated = await library_service.list_rated_entries(db, user_id)
        liked = [
            entry
            for entry in rated
            if entry.rating is not None and entry.rating >= library_service.FAVORITE_RATING_FLOOR
        ]
        await self._ensure_profiles(db, [entry.book for entry in liked])

        liked_vectors = await self._store.vectors_for([entry.book_id for entry in liked])
        contributing = [entry for entry in liked if entry.book_id in liked_vectors]
        if not contributing:
            # Every liked book is catalogued too thinly to characterise.
            # Reported as a cold start rather than as an empty result: the
            # reader has no usable profile, which is the same situation for
            # them as never having rated anything, and the client's "rate
            # something you loved" state is the right one.
            logger.info("recommendations_no_profile_vector", user_id=user_id, liked=len(liked))
            return RecommendationList(recommendations=[], based_on=0)

        profile = _weighted_average(
            [liked_vectors[entry.book_id] for entry in contributing],
            [_rating_weight(entry.rating) for entry in contributing if entry.rating is not None],
        )

        owned = await library_service.owned_book_ids(db, user_id)
        # Over-fetch: the dislike exclusions and the score floor are applied
        # in Python afterwards, so asking for exactly `limit` here would
        # return fewer than `limit` whenever anything is filtered.
        neighbours = await self._store.query(profile, n_results=limit * 4, exclude_book_ids=owned)
        if not neighbours:
            logger.info("recommendations_empty_pool", user_id=user_id, excluded=len(owned))
            return RecommendationList(recommendations=[], based_on=len(contributing))

        books = await _load_books(db, [neighbour.book_id for neighbour in neighbours])
        excluded_authors, excluded_genres = _exclusions(rated)

        recommendations: list[Recommendation] = []
        for neighbour in neighbours:
            if len(recommendations) >= limit:
                break
            book = books.get(neighbour.book_id)
            if book is None:
                # The vector outlived its row — a book deleted since the
                # profile was written. Skipping is right; there is nothing
                # to render.
                continue
            if neighbour.score < self._settings.recommendation_min_score:
                continue
            if _is_excluded(book, excluded_authors, excluded_genres):
                logger.info("recommendation_excluded", book_id=book.id, title=book.title)
                continue

            recommendations.append(_to_recommendation(book, neighbour, contributing, liked_vectors))

        logger.info(
            "recommendations_built",
            user_id=user_id,
            based_on=len(contributing),
            returned=len(recommendations),
            considered=len(neighbours),
        )
        return RecommendationList(recommendations=recommendations, based_on=len(contributing))

    async def _refresh_candidates_if_stale(
        self, db: AsyncSession, user_id: int, preferences: LibraryPreferences
    ) -> None:
        """Widens the shared candidate pool, if this reader's is out of date.

        Two independent triggers, both necessary. The clock alone would
        serve a stale pool for a whole day after the reader rated their
        first book in a new genre; the preference fingerprint alone would
        never pick up books published since. See `RecommendationState`.

        Never raises: discovery is an optimisation over a pool that already
        exists, and a catalog outage must degrade the suggestions rather
        than fail the request.

        Args:
            db: The current database session.
            user_id: The reader.
            preferences: Their currently derived preferences.
        """
        seed = _seed_fingerprint(preferences)
        state = await db.scalar(
            select(RecommendationState).where(RecommendationState.user_id == user_id)
        )
        if state is not None and state.seed == seed and self._is_pool_fresh(state):
            logger.info("recommendation_pool_fresh", user_id=user_id)
            return

        if not _claim_discovery(
            user_id, self._settings.recommendation_min_discovery_interval_seconds
        ):
            # Another request is already discovering for this reader, or one
            # finished moments ago. Rank against the pool as it stands
            # rather than queueing behind it — waiting would make this
            # request as slow as the one it is duplicating, for a pool it
            # is about to be handed anyway.
            logger.info("recommendation_discovery_skipped", user_id=user_id)
            return

        try:
            outcome = await self._discover(db, preferences)
        finally:
            _release_discovery(user_id)

        if state is None:
            state = RecommendationState(user_id=user_id, seed=seed)
            db.add(state)
        state.seed = seed
        state.complete = outcome.complete
        if outcome.reached:
            state.refreshed_at = datetime.utcnow()
        else:
            # Same rule as `BookDataFetcher`: an outage is not an answer,
            # so it must not be recorded as a completed run. The seed is
            # still stored — it describes what we *tried* to build, and
            # leaving it stale would re-run discovery against the same
            # unreachable catalogs on the very next request.
            logger.warning("recommendation_discovery_unavailable", user_id=user_id)
        await db.commit()

        logger.info(
            "recommendation_pool_refreshed",
            user_id=user_id,
            new_books=outcome.stored,
            reached=outcome.reached,
            complete=outcome.complete,
        )

    def _is_pool_fresh(self, state: RecommendationState) -> bool:
        """Decides whether a discovery run is still within its TTL.

        A run during which a catalog went down expires far sooner than a
        clean one. Same reasoning as `empty_book_cache_ttl_hours` in Module
        4: a degraded answer is not a settled fact, and giving it the full
        day would mean one bad minute costs the reader a day of thin
        recommendations, with rescanning and re-rating unable to shake it
        loose.

        Args:
            state: The reader's discovery bookkeeping.

        Returns:
            `True` when discovery ran recently enough to skip.
        """
        if state.refreshed_at is None:
            return False
        hours = (
            self._settings.recommendation_candidate_ttl_hours
            if state.complete
            else self._settings.recommendation_degraded_ttl_hours
        )
        return datetime.utcnow() - state.refreshed_at < timedelta(hours=hours)

    async def _discover(
        self, db: AsyncSession, preferences: LibraryPreferences
    ) -> "_DiscoveryOutcome":
        """Queries the catalogs for candidates and persists the new ones.

        **Sources run in parallel; the queries within a source do not.**
        The first implementation fanned every seed out at every source at
        once — up to ten simultaneous requests — and Google Books answered
        503 to most of them. That was measured rather than guessed at: the
        same five queries fired together failed 16 times out of 20, while
        paced one second apart they succeeded 5 out of 5. Google sheds load
        on burst rate per key, and the request *shape* made no measurable
        difference at all.

        Nothing before this module fanned out. The scan path queries three
        different hosts concurrently but issues exactly one request to each,
        so it never provoked this; discovery was the first code here to ask
        one host five questions at once.

        Args:
            db: The current database session.
            preferences: The genres and authors to seed the queries with.

        Returns:
            What the run achieved — see `_DiscoveryOutcome`.
        """
        queries = _seed_queries(preferences, self._settings)
        if not queries:
            return _DiscoveryOutcome(reached=False, complete=False, stored=0)

        gathered = await asyncio.gather(
            *(self._discover_from_source(source, queries) for source in self._sources),
            return_exceptions=True,
        )

        reached = False
        complete = True
        candidates: dict[str, BookMetadata] = {}
        for outcome in gathered:
            if isinstance(outcome, BaseException):
                # A source raising is a bug in that source, not a reason to
                # deny the reader every suggestion.
                logger.exception("candidate_source_raised", error=str(outcome))
                complete = False
                continue
            for result in outcome.results:
                for candidate in result.candidates:
                    _collect_candidate(candidates, candidate)
            reached = reached or outcome.reached
            complete = complete and outcome.complete

        stored = await self._persist_candidates(db, list(candidates.values()))
        return _DiscoveryOutcome(reached=reached, complete=complete, stored=stored)

    async def _discover_from_source(
        self, source: CandidateSource, queries: list[DiscoveryQuery]
    ) -> "_SourceRun":
        """Runs every seed query against one source, one at a time.

        **Stops at the source's first unavailable answer.** By the time a
        `DiscoveryResult` reports `available=False` the HTTP layer has
        already retried it, so that is not a blip — it is this catalog
        being down right now, and the remaining queries would each spend
        the full timeout finding that out again. openlibrary.org has
        extended outages during which every request hangs to the timeout;
        without this, one of them would hold a synchronous request for
        minutes.

        The run is then reported incomplete, which is what earns it the
        short TTL rather than a full day of a half-built pool.

        Args:
            source: The catalog to query.
            queries: The seed queries, in order.

        Returns:
            What this source produced, and whether it answered throughout.
        """
        spacing = self._settings.recommendation_query_spacing_seconds
        results: list[DiscoveryResult] = []
        reached = False

        for index, query in enumerate(queries):
            if index > 0 and spacing > 0:
                await asyncio.sleep(spacing)

            result = await source.discover(query, self._settings.recommendation_results_per_query)
            if not result.available:
                logger.warning(
                    "discovery_source_unavailable",
                    source=source.name.value,
                    query=query.label(),
                    completed=index,
                    remaining=len(queries) - index,
                )
                return _SourceRun(results=results, reached=reached, complete=False)

            reached = True
            results.append(result)

        return _SourceRun(results=results, reached=reached, complete=True)

    async def _persist_candidates(self, db: AsyncSession, candidates: list[BookMetadata]) -> int:
        """Stores discovered books that are not in the cache already.

        **An existing row is never overwritten.** It may be a book someone
        scanned, with fetched sources, a generated summary and a cover
        chain behind it — all of which a bare search hit would flatten. The
        candidate is simply dropped in favour of what is already known; the
        ranking then treats that row like any other.

        **Each insert gets its own savepoint**, because reading the known
        keys and inserting the new ones is a check-then-act across an
        `await`. Two discovery runs that overlap both see a key as absent
        and both insert it; the loser hits
        `UNIQUE constraint failed: books.normalized_key`. That is not
        hypothetical — it crashed a real request, because every library
        write invalidates the client's recommendation query and rapid
        re-rating fired three overlapping runs.

        A batched `add_all` + one `flush` makes that failure *total*: one
        colliding row discards the other fifty-nine and fails the whole
        request. Per-row savepoints turn it into "somebody else already
        stored this one", which is the truth. Same shape as
        `library_service.record_scan`, and the `add` lives inside the
        savepoint for the same reason — a rollback must also expunge the
        pending object, or the next commit re-flushes it and raises again.

        `_claim_discovery` makes the collision rare; this is what makes it
        harmless. The guard is in-process and would not survive a second
        worker, so the integrity has to live here.

        Args:
            db: The current database session.
            candidates: The merged, deduplicated discovery results.

        Returns:
            How many new rows were created.
        """
        limit = self._settings.recommendation_max_new_candidates
        keys = [normalize_key(c.title or "", c.author) for c in candidates]
        known = set(
            await db.scalars(select(Book.normalized_key).where(Book.normalized_key.in_(keys)))
        )

        created: list[Book] = []
        for candidate, key in zip(candidates, keys, strict=True):
            if len(created) >= limit:
                break
            if key in known or not candidate.title:
                continue
            known.add(key)
            book = Book(
                normalized_key=key,
                title=candidate.title,
                author=candidate.author,
                description=candidate.description,
                categories=candidate.categories or None,
                cover_url=candidate.cover_url,
                isbn_13=candidate.isbn_13,
                isbn_10=candidate.isbn_10,
                average_rating=candidate.average_rating,
                ratings_count=candidate.ratings_count,
                metadata_found=has_content(candidate, []),
                # Left `None` deliberately: this row's prose has never
                # been gathered, only its catalog card. `_is_fresh`
                # reads exactly this field, so a later scan of the same
                # book runs the real Module 4 fetch instead of serving
                # a search hit as a settled lookup.
                sources_fetched_at=None,
            )
            try:
                async with db.begin_nested():
                    db.add(book)
                    await db.flush()
            except IntegrityError:
                logger.info("candidate_race_resolved", key=key, title=candidate.title)
                continue
            created.append(book)

        if not created:
            return 0

        # Already added and flushed, each inside its own savepoint above.
        await self._ensure_profiles(db, created)
        await db.commit()
        return len(created)

    async def _ensure_profiles(self, db: AsyncSession, books: list[Book]) -> None:
        """Embeds and stores the profile vectors of books that lack one.

        Idempotent and incremental: books already in `book_profiles` are
        skipped, so a reader opening the screen twice pays no embedding
        cost the second time. That is what keeps ranking cheap enough for a
        synchronous endpoint on a CPU-only laptop.

        Args:
            db: The current database session, for the commit.
            books: The books to make sure have vectors.

        Raises:
            ExternalServiceUnavailable: If the local embedding model is
                unreachable.
        """
        if not books:
            return

        existing = await self._store.vectors_for([book.id for book in books])
        profiles: list[BookProfile] = []
        for book in books:
            if book.id in existing:
                continue
            document = build_profile_document(
                book, self._settings.recommendation_document_max_chars
            )
            if document is None:
                logger.info("profile_document_too_thin", book_id=book.id, title=book.title)
                continue
            profiles.append(BookProfile(book_id=book.id, document=document))

        if not profiles:
            return

        vectors = await self._embeddings.embed([profile.document for profile in profiles])
        await self._store.upsert(profiles, vectors)


def _collect_candidate(candidates: dict[str, BookMetadata], candidate: BookMetadata) -> None:
    """Merges one discovered book into the deduplicated candidate set.

    The catalogs return the same work many times over — several editions
    from Google Books, the same work again from Open Library — so they are
    keyed by the normalized title+author pair the book cache already uses,
    which folds case, accents and punctuation away.

    On a collision the richer record wins field by field rather than the
    first or the last: Google Books usually carries the blurb and Open
    Library the granular subjects, and a book characterised by both ranks
    better than one characterised by whichever source answered first.

    Args:
        candidates: The set so far, keyed by normalized title+author.
        candidate: The book to merge in.
    """
    if not candidate.title:
        return

    key = normalize_key(candidate.title, candidate.author)
    existing = candidates.get(key)
    if existing is None:
        candidates[key] = candidate
        return

    merged_categories = list(existing.categories)
    for label in candidate.categories:
        if label not in merged_categories:
            merged_categories.append(label)

    candidates[key] = replace(
        existing,
        author=existing.author or candidate.author,
        description=existing.description or candidate.description,
        categories=merged_categories,
        cover_url=existing.cover_url or candidate.cover_url,
        isbn_13=existing.isbn_13 or candidate.isbn_13,
        isbn_10=existing.isbn_10 or candidate.isbn_10,
        average_rating=(
            existing.average_rating
            if existing.average_rating is not None
            else candidate.average_rating
        ),
        ratings_count=(
            existing.ratings_count
            if existing.ratings_count is not None
            else candidate.ratings_count
        ),
    )


def _seed_queries(preferences: LibraryPreferences, settings: Settings) -> list[DiscoveryQuery]:
    """Turns derived preferences into the queries discovery will run.

    Args:
        preferences: The reader's favourite genres and authors.
        settings: For how many of each to use.

    Returns:
        One query per usable seed, genres first. Empty when every derived
        genre was too generic to query and no author survived — in which
        case discovery is skipped rather than run on nothing.
    """
    subjects = [
        genre
        for genre in preferences.favorite_genres
        if genre.strip().casefold() not in _GENERIC_SUBJECTS
    ][: settings.recommendation_seed_genres]

    authors = preferences.favorite_authors[: settings.recommendation_seed_authors]

    return [DiscoveryQuery(subject=subject) for subject in subjects] + [
        DiscoveryQuery(author=author) for author in authors
    ]


def _seed_fingerprint(preferences: LibraryPreferences) -> str:
    """Builds the stable fingerprint of a reader's derived preferences.

    Sorted and folded, so the fingerprint changes when the *set* of
    preferences changes and not when two equally-frequent genres swap
    places — the latter would re-run discovery for no new books.

    Args:
        preferences: The derived preferences.

    Returns:
        A comparable string, stored on `RecommendationState.seed`.
    """
    genres = sorted(genre.casefold() for genre in preferences.favorite_genres)
    authors = sorted(author.casefold() for author in preferences.favorite_authors)
    return f"{'|'.join(genres)}||{'|'.join(authors)}"


def _exclusions(rated: list[LibraryEntry]) -> tuple[set[str], set[str]]:
    """Derives the author and genre exclusion sets from low ratings.

    **A like always beats a dislike.** An author or genre that appears on
    both lists is not excluded: rating one Verne 5 and another 2 says
    something about those two books, not about Verne, and dropping him
    would delete the reader's own favourite author from their suggestions.
    This is also why the dislike signal is an exclusion set at all rather
    than negative vector arithmetic — a set can be overridden legibly, a
    subtracted vector cannot.

    Args:
        rated: Every entry this reader has rated, both ends of the scale.

    Returns:
        The folded author names and genre labels to exclude.
    """
    liked_authors: set[str] = set()
    liked_genres: set[str] = set()
    disliked_authors: set[str] = set()
    disliked_genres: set[str] = set()

    for entry in rated:
        if entry.rating is None:
            continue
        book = entry.book
        if entry.rating >= library_service.FAVORITE_RATING_FLOOR:
            authors, genres = liked_authors, liked_genres
        elif entry.rating <= library_service.DISLIKED_RATING_CEILING:
            authors, genres = disliked_authors, disliked_genres
        else:
            continue

        if book.author:
            authors.add(book.author.casefold().strip())
        for category in book.categories or []:
            genres.add(category.casefold().strip())

    return disliked_authors - liked_authors, disliked_genres - liked_genres


def _is_excluded(book: Book, authors: set[str], genres: set[str]) -> bool:
    """Decides whether a candidate falls under this reader's exclusions.

    Args:
        book: The candidate.
        authors: Folded author names to exclude.
        genres: Folded genre labels to exclude.

    Returns:
        `True` when the book's author or any of its genres is excluded.
    """
    if book.author and book.author.casefold().strip() in authors:
        return True
    return any((category.casefold().strip() in genres) for category in book.categories or [])


def _to_recommendation(
    book: Book,
    neighbour: ScoredBook,
    contributing: list[LibraryEntry],
    liked_vectors: dict[int, list[float]],
) -> Recommendation:
    """Builds one recommendation, explanation included.

    Args:
        book: The recommended book.
        neighbour: Its score and vector, from the profile search.
        contributing: The liked entries that fed the profile vector.
        liked_vectors: Their stored vectors.

    Returns:
        The recommendation.
    """
    source_entry = _nearest_liked(neighbour.vector, contributing, liked_vectors)

    if source_entry is None:
        # No vector to compare against — possible only if the store
        # returned a profile without its embedding. Say less rather than
        # naming a book we did not actually measure against.
        explanation = "Close to what you have been rating highly."
        because_of = None
    else:
        explanation = _explain(book, source_entry.book)
        because_of = source_entry.book_id

    return Recommendation(
        book=LibraryBook.model_validate(book),
        score=round(neighbour.score, 4),
        explanation=explanation,
        because_of_book_id=because_of,
    )


def _nearest_liked(
    vector: list[float],
    contributing: list[LibraryEntry],
    liked_vectors: dict[int, list[float]],
) -> LibraryEntry | None:
    """Finds which liked book a candidate is closest to.

    This is what makes the explanation a fact rather than a guess: the book
    named is the one that actually pulled this candidate into range.

    Args:
        vector: The candidate's profile vector.
        contributing: The liked entries that fed the profile.
        liked_vectors: Their stored vectors.

    Returns:
        The nearest entry, or `None` when there is nothing to compare.
    """
    if not vector:
        return None

    best: LibraryEntry | None = None
    best_similarity = -2.0
    for entry in contributing:
        liked = liked_vectors.get(entry.book_id)
        if not liked:
            continue
        similarity = _cosine(vector, liked)
        # Strict `>` keeps the first of equals, and `contributing` arrives
        # in a deterministic order — so two identical requests explain a
        # recommendation the same way rather than alternating between two
        # equally-near books, which reads as a bug.
        if similarity > best_similarity:
            best, best_similarity = entry, similarity
    return best


def _explain(candidate: Book, source: Book) -> str:
    """Writes the reason one book is being suggested.

    Deterministic and derived from the two rows — see the module docstring
    on why no model is involved. The shared genre is added when there is
    one because it is the part a reader can check at a glance; without it,
    "because you liked X" is true but says nothing about *this* book.

    Args:
        candidate: The recommended book.
        source: The liked book it is nearest to.

    Returns:
        A sentence naming the liked book, and the genre they share if any.
    """
    shared = _shared_category(candidate, source)
    if shared:
        return f"Because you liked {source.title} — also {shared.lower()}"
    return f"Because you liked {source.title}"


def _shared_category(candidate: Book, source: Book) -> str | None:
    """Returns a genre both books carry, in the candidate's spelling.

    Args:
        candidate: The recommended book.
        source: The liked book.

    Returns:
        The shared label, or `None`. When several are shared, the most
        specific-looking one wins: the longest label, ties broken
        alphabetically so the sentence does not change between two
        identical requests.
    """
    source_labels = {label.casefold().strip() for label in source.categories or []}
    shared = [
        label
        for label in candidate.categories or []
        if label.casefold().strip() in source_labels
        and label.casefold().strip() not in _GENERIC_SUBJECTS
    ]
    if not shared:
        return None
    return sorted(shared, key=lambda label: (-len(label), label.casefold()))[0]


def _weighted_average(vectors: list[list[float]], weights: list[float]) -> list[float]:
    """Averages vectors by weight and normalizes the result to unit length.

    Normalizing matters: Chroma's cosine space compares direction, and an
    un-normalized average would still *work* but would make the profile's
    magnitude depend on how many books the reader has rated — which is
    exactly the quantity that must not affect what gets recommended.

    Args:
        vectors: The vectors to average. Assumed non-empty and same length.
        weights: One weight per vector.

    Returns:
        The unit-length weighted average.
    """
    dimensions = len(vectors[0])
    total = [0.0] * dimensions
    for vector, weight in zip(vectors, weights, strict=True):
        for index in range(min(dimensions, len(vector))):
            total[index] += vector[index] * weight

    norm = math.sqrt(sum(value * value for value in total))
    if norm == 0.0:
        # No direction to rank by. Cannot happen with real embeddings, but
        # a zero vector would make Chroma return an arbitrary ordering
        # presented as similarity, so it is given one arbitrary component
        # explicitly rather than silently.
        total[0] = 1.0
        return total
    return [value / norm for value in total]


def _cosine(left: list[float], right: list[float]) -> float:
    """Returns the cosine similarity of two vectors.

    Args:
        left: One vector.
        right: The other.

    Returns:
        The similarity, or `0.0` if either vector has no magnitude.
    """
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size]))
    right_norm = math.sqrt(sum(value * value for value in right[:size]))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


async def _load_books(db: AsyncSession, book_ids: list[int]) -> dict[int, Book]:
    """Loads the `Book` rows behind a set of ranked ids.

    The vector store holds only ids: every field the client renders — title,
    author, cover, categories, rating — is read from SQLite, so there is one
    place a book's details can be wrong rather than two that can disagree.

    Args:
        db: The current database session.
        book_ids: The books to load.

    Returns:
        A mapping of id to book, missing any that no longer exist.
    """
    if not book_ids:
        return {}
    books = await db.scalars(select(Book).where(Book.id.in_(book_ids)))
    return {book.id: book for book in books}


def build_recommendation_service() -> RecommendationService:
    """Factory for the production `RecommendationService`.

    Returns:
        A service embedding locally through Ollama, ranking in the local
        `book_profiles` Chroma collection, and discovering candidates in
        the two catalogs. Wikipedia is not a candidate source: it answers
        "what is said about this book", not "what other books are like it".
    """
    settings = get_settings()
    return RecommendationService(
        embeddings=get_embedding_client(),
        profile_store=get_profile_store(),
        sources=[GoogleBooksSource(settings), OpenLibrarySource(settings)],
        settings=settings,
    )


__all__ = [
    "RecommendationService",
    "build_profile_document",
    "build_recommendation_service",
]

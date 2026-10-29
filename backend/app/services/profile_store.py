"""One vector per book, in the `book_profiles` table (PostgreSQL + pgvector).

**This module queries across books on purpose, and that is exactly what
`vector_store.py` forbids.** The two are not in tension; they answer
different questions and are kept apart so neither has to bend:

| | `book_chunks` (`vector_store.py`) | `book_profiles` (here) |
|---|---|---|
| unit | one passage of one book | one whole book |
| question | "what does *this book's* corpus say about X?" | "which books are like this?" |
| `book_id` | **mandatory filter**, enforced several ways | the answer, never a constraint |
| wrong answer | a cited summary of the wrong book | a worse suggestion |

Module 5's filter is a correctness invariant because a passage from
*Foundation* inside a summary of *Dune* is fluent, plausible, carries a
real citation, and is undetectable downstream. Nothing of the sort applies
here: a recommendation that surfaces the wrong neighbour is visibly a bad
recommendation, and the reader is the check. So rather than weakening that
filter, flagging around it, or granting it an exception, recommendations
get their own table with its own rule. Two tables, two rules, neither
relaxed — and the chunk model is not importable from here (a test forbids it).

The practical consequence to remember: **never store a chunk here and
never store a profile there.** A profile document is built from the
catalog record (title, author, categories, blurb) and is not citable
material; the chunk ids Module 5 hands the client would not resolve to it.
The tables are separate and the columns differ, so a stray row cannot
exist.

Like the chunk store, this runs on the **caller's session** and never
commits: a candidate `Book` persisted a moment earlier by the same request
is not visible to another session until commit, and the foreign key would
make a second session's insert wait on it. There is no ANN index here
either — the table holds hundreds to low thousands of rows, so an exact
scan is both correct and fast; see `app/models/vector.py`.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vector import BookProfileRow

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BookProfile:
    """One book's whole-record document, ready to embed.

    Attributes:
        book_id: The `Book` row this describes.
        document: The text the vector is built from. Must be produced by
            the *same* builder for every book — a candidate characterised
            by title and subjects and a library book characterised by a
            full blurb are not comparable if they were assembled
            differently. See `recommendation_service.build_profile_document`.
    """

    book_id: int
    document: str


@dataclass(frozen=True)
class ScoredBook:
    """One neighbour returned by a profile similarity search.

    Attributes:
        book_id: The book found.
        score: Cosine **similarity**, clamped to `[0, 1]`. The database
            reports distance; this is converted here so callers never have
            to remember which direction is better — a score is a score,
            and a reversed comparison would silently recommend the least
            similar books with no error anywhere.
        vector: The book's stored embedding, returned alongside so the
            explanation ("because you liked X") can be computed without a
            second round trip per recommendation.
    """

    book_id: int
    score: float
    vector: list[float]


class ProfileStore(Protocol):
    """Abstraction over the whole-book vector store, so it can be faked.

    Every method takes the caller's `AsyncSession` first; see the module
    docstring.
    """

    async def upsert(
        self, db: AsyncSession, profiles: list[BookProfile], embeddings: list[list[float]]
    ) -> None:
        """Writes book profiles and their vectors, replacing any for the same books."""
        ...

    async def vectors_for(self, db: AsyncSession, book_ids: list[int]) -> dict[int, list[float]]:
        """Returns the stored vectors of the given books, skipping any missing."""
        ...

    async def query(
        self,
        db: AsyncSession,
        embedding: list[float],
        n_results: int,
        exclude_book_ids: set[int],
    ) -> list[ScoredBook]:
        """Returns the books nearest a vector, excluding the given ids."""
        ...

    async def delete_book(self, db: AsyncSession, book_id: int) -> None:
        """Removes a book's profile vector."""
        ...


class PgProfileStore:
    """`ProfileStore` backed by the `book_profiles` table. Stateless."""

    async def upsert(
        self, db: AsyncSession, profiles: list[BookProfile], embeddings: list[list[float]]
    ) -> None:
        """Writes book profiles and their vectors.

        Joins the caller's transaction; does not commit.

        Args:
            db: The caller's session.
            profiles: The profiles to store.
            embeddings: One vector per profile, in the same order.

        Raises:
            ValueError: If the two lists have different lengths — that
                would pair each book with another's vector, and every
                recommendation downstream would be confidently wrong.
            sqlalchemy.exc.IntegrityError: If a profile's book does not
                exist (the foreign key).
        """
        if len(profiles) != len(embeddings):
            raise ValueError(
                f"Got {len(profiles)} profiles but {len(embeddings)} embeddings; "
                "refusing to upsert."
            )
        if not profiles:
            return

        # Rows go in by `book_id`, whatever order the caller built them in.
        # Two readers who liked the same books with different ratings would
        # otherwise lock the same rows in opposite orders and deadlock.
        pairs = sorted(zip(profiles, embeddings, strict=True), key=lambda pair: pair[0].book_id)
        statement = insert(BookProfileRow).values(
            [
                {"book_id": profile.book_id, "document": profile.document, "embedding": vector}
                for profile, vector in pairs
            ]
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[BookProfileRow.book_id],
                set_={
                    "document": statement.excluded.document,
                    "embedding": statement.excluded.embedding,
                },
            )
        )
        logger.info("book_profiles_upserted", count=len(profiles))

    async def vectors_for(self, db: AsyncSession, book_ids: list[int]) -> dict[int, list[float]]:
        """Returns the stored vectors of the given books.

        Books with no stored profile are simply absent from the result
        rather than raising: a book the catalogs described too thinly to
        characterise has no vector by design, and the caller reports how
        many books actually fed the profile.

        Args:
            db: The caller's session.
            book_ids: The books to look up.

        Returns:
            A mapping of book id to vector, for those that have one.
        """
        if not book_ids:
            return {}

        rows = await db.execute(
            select(BookProfileRow.book_id, BookProfileRow.embedding).where(
                BookProfileRow.book_id.in_(book_ids)
            )
        )
        return {row.book_id: [float(value) for value in row.embedding] for row in rows}

    async def query(
        self,
        db: AsyncSession,
        embedding: list[float],
        n_results: int,
        exclude_book_ids: set[int],
    ) -> list[ScoredBook]:
        """Returns the books nearest a vector, excluding the given ids.

        The exclusion is part of the SQL rather than applied afterwards so
        `n_results` means what it says: filtering a fetched page in
        Python would quietly return fewer books the more the reader has
        already read, which is the direction that matters least to notice
        and most to get wrong.

        Args:
            db: The caller's session.
            embedding: The query vector — normally the reader's profile.
            n_results: Maximum books to return.
            exclude_book_ids: Books that must not be returned, typically
                everything already in the reader's library.

        Returns:
            The nearest books, most similar first. Empty when the table
            holds nothing but exclusions.
        """
        if n_results <= 0:
            return []

        distance = BookProfileRow.embedding.cosine_distance(embedding).label("distance")
        statement = select(BookProfileRow.book_id, BookProfileRow.embedding, distance)
        # An unfiltered query is the *correct* query in this table, unlike
        # in `vector_store.py`: nothing to exclude means nothing to add.
        if exclude_book_ids:
            statement = statement.where(BookProfileRow.book_id.not_in(sorted(exclude_book_ids)))
        statement = statement.order_by(distance, BookProfileRow.book_id).limit(n_results)

        rows = (await db.execute(statement)).all()
        scored = [
            ScoredBook(
                book_id=row.book_id,
                score=_similarity(row.distance),
                vector=[float(value) for value in row.embedding],
            )
            for row in rows
        ]
        logger.info(
            "book_profiles_queried",
            requested=n_results,
            returned=len(scored),
            excluded=len(exclude_book_ids),
        )
        return scored

    async def delete_book(self, db: AsyncSession, book_id: int) -> None:
        """Removes a book's profile vector.

        Args:
            db: The caller's session.
            book_id: The book whose profile to drop.
        """
        await db.execute(delete(BookProfileRow).where(BookProfileRow.book_id == book_id))
        logger.info("book_profile_deleted", book_id=book_id)


def _similarity(distance: float | None) -> float:
    """Converts a cosine distance into a similarity score in `[0, 1]`.

    pgvector's cosine distance is `1 - similarity`, so this inverts it.
    Clamped because floating-point error puts a perfect match a hair below
    zero distance, and a negative score would sort and render as if it
    meant something. A `NULL` or `NaN` distance (pgvector yields `NaN` for
    a zero vector, which has no direction) scores 0: an unmeasurable
    neighbour must not rank above a measured one.

    Args:
        distance: The distance reported by the database.

    Returns:
        The similarity, between 0 and 1 inclusive.
    """
    if distance is None or math.isnan(distance):
        return 0.0
    return min(1.0, max(0.0, 1.0 - float(distance)))


@lru_cache
def get_profile_store() -> PgProfileStore:
    """Returns the shared `PgProfileStore`.

    Stateless (every call uses the caller's session), so this is just a
    shared instance, kept as a factory so call sites did not have to change.

    Returns:
        A `PgProfileStore`.
    """
    return PgProfileStore()

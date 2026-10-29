"""Vector storage for book chunks, in the `book_chunks` table (PostgreSQL + pgvector).

**The retrieval filter on `book_id` is a correctness invariant, not a
ranking preference.** A summary of *Dune* built partly from *Foundation*'s
reception passages would be fluent, plausible, and wrong — and, because
every claim would still carry a real citation, wrong in a way that looks
verified. Nothing downstream can detect that; the prompt certainly
cannot. So the constraint is enforced here, structurally, in four
independent ways, plus one the database adds:

1. **`book_id` is a required positional parameter of `query`.** There is
   no overload that omits it and no default, so a caller cannot forget it
   and cannot pass a filter of their own.
2. **The `WHERE book_chunks.book_id = :book_id` is built inside this
   module**, from that parameter alone. Callers never supply raw filters
   or a `select`, so a refactor elsewhere cannot drop the clause.
3. **Every returned row is re-checked against the requested `book_id`**
   before it is handed back, and a mismatch raises `CorpusLeak`.
4. **Only this module may touch the `BookChunk` model.** A test
   (`tests/test_vector_store.py`) scans `app/` and fails if any other file
   references it, so nobody can write a second, unfiltered chunk query
   next to the guarded one.
5. **Foreign keys** (`book_id` -> `books`, `text_source_id` ->
   `text_sources`, both `ON DELETE CASCADE`): a chunk of a book that does
   not exist, or of a source that was replaced, cannot persist.

The risks these guard against are the SQL spellings of the ones the
previous in-process vector store had (an empty filter returned the whole
collection and a misspelled metadata key matched nothing, both silently). In SQL the traps
are: a refactor that drops the `WHERE` (the query still succeeds and ranks
the whole corpus), and — subtler — **an ANN index**. pgvector applies the
filter *after* an HNSW/IVFFlat scan, so with an index a query for a small
book can silently return fewer rows than exist, or none. That is why
`app/models/vector.py` has no such index and why the test suite includes
a "small book among many far-away books" case.

**The store runs on the caller's session and never commits.** A store with
a session of its own would not see rows the caller has written but not yet
committed (a freshly persisted `Book`), and with the foreign key Postgres
would make its insert wait on the caller's open transaction — the same
coroutine, so a hang. Writes therefore join the caller's transaction; the
caller decides when it commits.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vector import BookChunk
from app.services.chunking import Chunk

logger = structlog.get_logger(__name__)

# Rows per INSERT statement. Each row binds 10 parameters and asyncpg caps a
# statement at 32,767, so 500 leaves a wide margin. A book has ~10-60 chunks
# and never gets near it, but an unbounded VALUES list is a limit nobody has
# hit and nobody has tested, so the loop is there instead.
_UPSERT_BATCH_SIZE = 500


class CorpusLeak(RuntimeError):
    """Raised when a retrieval returned a chunk belonging to another book.

    Not a `GlanceError`: this is an invariant violation, not a
    user-facing condition. It means the `book_id` filter silently stopped
    working, and the correct response is a 500 and a loud log — never a
    summary built on another book's passages.
    """


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk returned from a similarity search.

    Attributes:
        id: The chunk id, as produced by `chunking.Chunk`.
        book_id: The book it belongs to. Always equal to the queried book.
        content: The chunk text.
        source: Which official source produced it.
        kind: What the passage is — description, subjects, plot, reception.
        heading: The originating section heading, or `None`.
        url: A link back to the source page, or `None`.
        license: The licence the text is published under, or `None`.
        distance: Cosine distance from the query vector; lower is closer.
    """

    id: str
    book_id: int
    content: str
    source: str
    kind: str
    heading: str | None
    url: str | None
    license: str | None
    distance: float


class VectorStore(Protocol):
    """Abstraction over the chunk vector store, so it can be faked in tests.

    Every method takes the caller's `AsyncSession` first; see the module
    docstring for why the store never owns one.
    """

    async def upsert(
        self, db: AsyncSession, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        """Writes chunks and their vectors, replacing any with the same ids."""
        ...

    async def query(
        self, db: AsyncSession, book_id: int, embedding: list[float], n_results: int
    ) -> list[RetrievedChunk]:
        """Returns the nearest chunks *belonging to `book_id`*, never others."""
        ...

    async def delete_book(self, db: AsyncSession, book_id: int) -> None:
        """Removes every chunk belonging to a book."""
        ...

    async def count_for_book(self, db: AsyncSession, book_id: int) -> int:
        """Returns how many chunks are stored for a book."""
        ...


class PgVectorStore:
    """`VectorStore` backed by the `book_chunks` table. Stateless."""

    async def upsert(
        self, db: AsyncSession, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        """Writes chunks and their vectors, replacing any with the same ids.

        Joins the caller's transaction; does not commit.

        Args:
            db: The caller's session.
            chunks: The chunks to store.
            embeddings: One vector per chunk, in the same order.

        Raises:
            ValueError: If the two lists have different lengths — that
                would silently pair each chunk with another's vector.
            sqlalchemy.exc.IntegrityError: If a chunk's book or text
                source does not exist (the foreign keys).
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Got {len(chunks)} chunks but {len(embeddings)} embeddings; refusing to upsert."
            )
        if not chunks:
            return

        for start in range(0, len(chunks), _UPSERT_BATCH_SIZE):
            batch = chunks[start : start + _UPSERT_BATCH_SIZE]
            vectors = embeddings[start : start + _UPSERT_BATCH_SIZE]
            statement = insert(BookChunk).values(
                [
                    {
                        "id": chunk.id,
                        "book_id": chunk.book_id,
                        "text_source_id": chunk.text_source_id,
                        "content": chunk.content,
                        "source": chunk.source,
                        "kind": chunk.kind,
                        "heading": chunk.heading,
                        "url": chunk.url,
                        "license": chunk.license,
                        "embedding": vector,
                    }
                    for chunk, vector in zip(batch, vectors, strict=True)
                ]
            )
            excluded = statement.excluded
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=[BookChunk.id],
                    set_={
                        "book_id": excluded.book_id,
                        "text_source_id": excluded.text_source_id,
                        "content": excluded.content,
                        "source": excluded.source,
                        "kind": excluded.kind,
                        "heading": excluded.heading,
                        "url": excluded.url,
                        "license": excluded.license,
                        "embedding": excluded.embedding,
                    },
                )
            )
        logger.info("chunks_upserted", book_id=chunks[0].book_id, count=len(chunks))

    async def query(
        self, db: AsyncSession, book_id: int, embedding: list[float], n_results: int
    ) -> list[RetrievedChunk]:
        """Returns the nearest chunks belonging to `book_id`.

        `book_id` is required and the `WHERE` clause is built here from it
        alone — see the module docstring for why the filter is not left to
        callers. The results are re-checked against it before returning.
        The sort is an exact one over that book's rows (there is no ANN
        index to truncate it), with the chunk id as a tie-break so equal
        distances come back in a stable order.

        Args:
            db: The caller's session.
            book_id: The only book whose chunks may be returned.
            embedding: The query vector.
            n_results: Maximum number of chunks to return.

        Returns:
            The matching chunks, nearest first. Empty if the book has no
            chunks stored.

        Raises:
            CorpusLeak: If a chunk from another book came back, meaning
                the filter silently stopped working.
        """
        if n_results <= 0:
            return []

        distance = BookChunk.embedding.cosine_distance(embedding).label("distance")
        statement = (
            select(
                BookChunk.id,
                BookChunk.book_id,
                BookChunk.content,
                BookChunk.source,
                BookChunk.kind,
                BookChunk.heading,
                BookChunk.url,
                BookChunk.license,
                distance,
            )
            .where(BookChunk.book_id == book_id)
            .order_by(distance, BookChunk.id)
            .limit(n_results)
        )
        rows = (await db.execute(statement)).all()

        chunks = [
            RetrievedChunk(
                id=row.id,
                book_id=row.book_id,
                content=row.content,
                source=row.source,
                kind=row.kind,
                heading=row.heading,
                url=row.url,
                license=row.license,
                distance=float(row.distance),
            )
            for row in rows
        ]
        _assert_no_leak(book_id, chunks)

        logger.info("chunks_retrieved", book_id=book_id, count=len(chunks), requested=n_results)
        return chunks

    async def delete_book(self, db: AsyncSession, book_id: int) -> None:
        """Removes every chunk belonging to a book.

        Called before re-ingesting, so chunks from a previous ingest
        cannot be retrieved and cited alongside the new ones. Joins the
        caller's transaction; does not commit.

        Args:
            db: The caller's session.
            book_id: The book whose chunks to drop.
        """
        await db.execute(delete(BookChunk).where(BookChunk.book_id == book_id))
        logger.info("chunks_deleted", book_id=book_id)

    async def count_for_book(self, db: AsyncSession, book_id: int) -> int:
        """Returns how many chunks are stored for a book.

        Args:
            db: The caller's session.
            book_id: The book to count chunks for.

        Returns:
            The number of stored chunks; `0` if the book was never ingested.
        """
        result = await db.execute(
            select(func.count()).select_from(BookChunk).where(BookChunk.book_id == book_id)
        )
        return int(result.scalar_one())


def _assert_no_leak(book_id: int, chunks: list[RetrievedChunk]) -> None:
    """Verifies every retrieved chunk belongs to the requested book.

    The last line of defence behind the `WHERE` clause. A query that lost
    its filter still succeeds, so without this a broken filter would
    surface as a confidently-cited summary of the wrong book rather than
    as an error.

    Args:
        book_id: The book that was queried.
        chunks: The chunks the database returned.

    Raises:
        CorpusLeak: If any chunk belongs to a different book.
    """
    foreign = [chunk for chunk in chunks if chunk.book_id != book_id]
    if not foreign:
        return

    logger.error(
        "corpus_leak_detected",
        requested_book_id=book_id,
        foreign_chunk_ids=[chunk.id for chunk in foreign],
        foreign_book_ids=sorted({chunk.book_id for chunk in foreign}),
    )
    raise CorpusLeak(
        f"Retrieval for book {book_id} returned {len(foreign)} chunk(s) from another book. "
        "The book_id filter is not working; refusing to build a summary."
    )


@lru_cache
def get_vector_store() -> PgVectorStore:
    """Returns the shared `PgVectorStore`.

    The store holds no connection or state (every call uses the caller's
    session), so this is just a shared instance, kept as a factory so the
    call sites and the FastAPI dependency wiring did not have to change.

    Returns:
        A `PgVectorStore`.
    """
    return PgVectorStore()

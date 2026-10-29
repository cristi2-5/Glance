"""The vector tables: `book_chunks` (RAG passages) and `book_profiles` (recommendations).

These replaced two collections of an in-process vector library that wrote
files to the api container's disk. Moving them into the Postgres we
already run buys one thing that library could never give: **the database
enforces what used to be a convention.** A chunk now has a foreign key to its book
and to the `TextSource` row it was cut from, both `ON DELETE CASCADE`, so

- a chunk for a book that does not exist cannot be written, and
- when Module 4 refreshes a book's sources (the `delete-orphan` cascade on
  `Book.text_sources` deletes and recreates the rows), the chunks cut from
  the old rows disappear in the same transaction. The next summary finds
  the book un-ingested and rebuilds the corpus, instead of retrieving
  passages whose sources no longer exist.

**There is deliberately no HNSW or IVFFlat index on either `embedding`
column.** pgvector applies a `WHERE` clause *after* the approximate scan:
an HNSW index returns its nearest ~40 candidates (`hnsw.ef_search`)
across *all* books and only then filters them to `book_id = X`. If those
40 belong to other books, the query for X returns fewer rows than exist —
or none — and nothing raises. That is the same failure shape as the old
store's silent-miss filter, and it would present as "this book has no passages".
Instead `book_chunks` has a b-tree on `book_id`, so the planner reads the
one book's ~10-60 chunks and sorts them by exact distance: correct by
construction and fast at this size. `book_profiles` is queried across
books but holds hundreds to low thousands of rows, so an exact scan is
fine too.

If a table ever outgrows that (roughly 100k+ rows scanned per query),
add HNSW *together with* `SET hnsw.iterative_scan = strict_order` (or
`relaxed_order`, pgvector >= 0.8) so a filtered query keeps scanning until
it has enough matches, and extend `tests/test_vector_store.py`'s
"small book among many" test — it exists to fail if that is forgotten.

Only `app/services/vector_store.py` may query `BookChunk`; a test enforces
it. See that module for the `book_id` invariant.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

#: Dimension of `nomic-embed-text` output. A different embedding model
#: needs a new migration and a re-embed of everything, not just a new name.
EMBEDDING_DIMENSIONS = 768


class BookChunk(Base):
    """One retrievable passage of one book, with its vector and provenance.

    Attributes:
        id: Stable chunk id, `b{book_id}:s{source_id}:{index}` — encodes
            the book, so a chunk is traceable without a second lookup.
        book_id: The book it belongs to. Every retrieval filters on it.
        text_source_id: The `TextSource` row it was cut from.
        content: The chunk text.
        source: Which official source produced it (`SourceName` value).
        kind: What the passage is (`SourceKind` value).
        heading: The originating section heading, or `None`.
        url: A link back to the source page, or `None`.
        license: The licence the text is published under, or `None`.
        embedding: The `nomic-embed-text` vector.
    """

    __tablename__ = "book_chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("text_sources.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)


class BookProfileRow(Base):
    """One whole-book vector, for recommendations (queried *across* books).

    Named `...Row` because `profile_store.BookProfile` is the in-memory
    value the store accepts; this is its stored form.

    Attributes:
        book_id: The book described; also the primary key (one per book).
        document: The text the vector was built from.
        embedding: The `nomic-embed-text` vector.
    """

    __tablename__ = "book_profiles"

    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    document: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)

"""The `Book` and `TextSource` models — the cached corpus behind RAG and recommendations.

A `Book` row is the cache entry for one recognized cover: the catalog
metadata (description, categories, cover image, ISBN, rating) keyed by a
normalized title+author pair. `TextSource` rows hang off it and hold the
prose gathered about that book — descriptions, subject lists, plot
summaries and, most importantly, *critical reception* — each tagged with
its origin and licence so Module 5 can cite what it retrieved.

`TextSource` is deliberately not a `reviews` table. No mainstream review
site permits scraping its review pages (Goodreads, LibraryThing and
Amazon all `Disallow` exactly those paths), so the critical-opinion
corpus comes from Wikipedia's *Reception* sections under CC BY-SA
instead. See the "Content sources" decision in CLAUDE.md.
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class SourceName(enum.StrEnum):
    """The official sources a `TextSource` can come from."""

    GOOGLE_BOOKS = "google_books"
    OPEN_LIBRARY = "open_library"
    WIKIPEDIA = "wikipedia"


class SourceKind(enum.StrEnum):
    """What a `TextSource` passage actually is.

    `RECEPTION` is the critical-opinion material — the closest official
    equivalent to the user reviews this project deliberately does not
    scrape, and the highest-value retrieval target for Module 5.
    """

    DESCRIPTION = "description"
    SUBJECTS = "subjects"
    PLOT = "plot"
    RECEPTION = "reception"


class Book(Base):
    """A book whose catalog metadata has been fetched and cached.

    One row per distinct title+author pair, deduplicated on
    `normalized_key` so that "Dune / Frank Herbert" and "DUNE / frank
    herbert" resolve to the same cache entry.

    Attributes:
        id: Unique identifier.
        normalized_key: Case- and accent-folded "title|author", the cache
            lookup key. Unique.
        title: The title as the catalog spells it, or the vision guess
            when no catalog matched.
        author: The author, when known.
        description: Publisher/catalog blurb, `None` when unavailable.
        categories: Genre/subject labels, as a JSON list.
        cover_url: URL of the catalog's cover thumbnail.
        isbn_13: ISBN-13, when the catalog reports one.
        isbn_10: ISBN-10, when the catalog reports one.
        average_rating: Mean reader rating, on the source's own scale.
        ratings_count: How many ratings `average_rating` averages.
        metadata_found: `False` when no catalog matched the title at all —
            the row then holds only the vision-derived title/author, and
            the client shows a "metadata not found" state.
        sources_fetched_at: When the `TextSource` rows were last refreshed.
            `None` means text has never been gathered for this book; the
            TTL check in `BookDataFetcher` reads this field.
        created_at: When the entry was first cached.
        updated_at: When the entry was last refreshed.
        text_sources: The prose gathered about this book.
    """

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_key: Mapped[str] = mapped_column(
        String(500), nullable=False, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    author: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    isbn_13: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    isbn_10: Mapped[str | None] = mapped_column(String(20), nullable=True)
    average_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratings_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    metadata_found: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sources_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    text_sources: Mapped[list["TextSource"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TextSource(Base):
    """One passage of prose about a book, from one official source.

    Rows are the retrieval units Module 5 chunks and embeds. Each carries
    provenance (`source`, `url`, `license`) so a generated summary can
    cite where a statement came from.

    Attributes:
        id: Unique identifier.
        book_id: The book this passage describes.
        source: Which official source produced it (`SourceName`).
        kind: What the passage is (`SourceKind`) — description, subjects,
            plot, or critical reception.
        heading: The section heading it was taken from, when the source
            has sections (Wikipedia); `None` otherwise.
        content: The passage text.
        url: A link back to the page it came from, for citation.
        license: The licence the text is published under (e.g. `CC BY-SA
            4.0` for Wikipedia, `CC0` for Open Library) — recorded because
            attribution obligations differ per source.
        created_at: When the passage was cached.
        book: The parent book.
    """

    __tablename__ = "text_sources"
    __table_args__ = (Index("ix_text_sources_book_kind", "book_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    license: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    book: Mapped["Book"] = relationship(back_populates="text_sources")

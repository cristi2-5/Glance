"""The `LibraryEntry` model — everything one user has done with one book.

Until Module 6 there was **no** link at all between a `User` and a
`Book`. A `Job` carries a `user_id`, but a job is the technical record of
one scan: it expires from usefulness the moment it is read, it is
duplicated every time the same cover is photographed again, and it holds
the vision reading rather than the user's opinion. It is not a library.

So this table is the relation, and it is deliberately **one** table
rather than separate `reading_history`, `ratings` and `reviews` ones.
Status, rating and personal note are all attributes of the same fact —
"this user, this book" — and splitting them would mean three rows that
can disagree about whether the relation exists at all (a rating for a
book that is not in the history is not a state with a meaning).

The unique constraint on `(user_id, book_id)` is the part that matters in
practice. Rescanning the same cover is the normal case, not an edge one:
without the constraint the same book appears three times in the history,
"books scanned" counts three, and the Module 6 profile vector weighs that
book three times over. With it, a rescan bumps `scan_count` and nothing
else moves.

The reader's own writing lives in `JournalEntry` rows hanging off this
one, not in a column here. Module 6a shipped a single `review` field and
it was wrong twice over: it was asked for on the scan result screen,
seconds after the cover was photographed, and it could hold only one
opinion, overwritten each time. See `app/models/journal.py`.

**Nothing the reader writes enters the RAG corpus.** Module 5's whole
guarantee is that every claim in a summary is traceable to a cited
official source. If a reader's own note became a citable chunk, the
summary could quote them back at themselves formatted as criticism —
fluent, correctly cited, and false. See the "Retrieval is filtered on
`book_id`" and "The prompt is not the enforcement" decisions in
CLAUDE.md.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.book import Book
    from app.models.journal import JournalEntry


class ReadingStatus(enum.StrEnum):
    """Where a book stands for one user.

    `SCANNED` is the state the pipeline creates on its own and means
    exactly "this passed in front of the camera" — no intent claimed. The
    other three are the user's explicit statements, and only they are
    treated as intent by Module 6.
    """

    SCANNED = "scanned"
    WANT_TO_READ = "want_to_read"
    READING = "reading"
    READ = "read"


#: Ratings the API accepts, mirrored by the `CheckConstraint` below so a
#: bad value cannot be written by a path that skips schema validation.
MIN_RATING = 1
MAX_RATING = 5


class LibraryEntry(Base):
    """One user's relationship with one book.

    Created automatically by the cover pipeline when a scan resolves to a
    book, then enriched by the user through
    `PUT /books/{book_id}/library`.

    Attributes:
        id: Unique identifier.
        user_id: The owner. Every read path filters on it — an entry is
            private to the user it belongs to.
        book_id: The book, shared across users like the book row itself.
        status: `ReadingStatus` — where the book stands for this user.
        rating: The user's rating, 1-5, or `None` if never rated. The
            primary signal behind the Module 6 profile vector.
        journal: The reader's dated notes about this book, oldest first.
            Deleted with the entry.
        scan_count: How many times this user has scanned this cover. The
            reason a rescan is not a second history entry.
        first_scanned_at: When the book first entered this library.
            `None` for an entry the user created without scanning.
        read_at: When the user marked it `READ`. Cleared if they move it
            back out of `READ`, so it never outlives the claim it records.
        rated_at: When `rating` was last set.
        created_at: When the entry was created.
        updated_at: When it was last touched.
        book: The book itself, eagerly loaded — every list view renders
            the title, author and cover, so lazy loading here would be a
            query per row.
    """

    __tablename__ = "library_entries"
    __table_args__ = (
        # The invariant this table exists to hold: one entry per user per
        # book, so a rescan updates rather than accumulates.
        UniqueConstraint("user_id", "book_id", name="uq_library_user_book"),
        CheckConstraint(
            f"rating IS NULL OR (rating >= {MIN_RATING} AND rating <= {MAX_RATING})",
            name="ck_library_rating_range",
        ),
        # The shape of every list query: this user's entries, newest first.
        Index("ix_library_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default=ReadingStatus.SCANNED.value, nullable=False, index=True
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scan_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    book: Mapped["Book"] = relationship(lazy="selectin")
    # Eager, because the ORM cascade needs the children loaded to delete
    # them: SQLite does not enforce `ON DELETE CASCADE` unless
    # `PRAGMA foreign_keys` is on, which it is not here.
    journal: Mapped[list["JournalEntry"]] = relationship(
        back_populates="library_entry",
        cascade="all, delete-orphan",
        order_by="JournalEntry.created_at",
        lazy="selectin",
    )

    @property
    def journal_count(self) -> int:
        """How many notes the reader has written about this book.

        Read by `LibraryEntryRead`, so a list of books carries the count
        without carrying every word of every note.
        """
        return len(self.journal)

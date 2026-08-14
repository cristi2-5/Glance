"""The `JournalEntry` model — dated notes a reader writes about a book.

This replaces the single `review` field `LibraryEntry` shipped with in
Module 6a. That field was asked for on the scan result screen, seconds
after the cover was photographed, which is the one moment the reader has
nothing to say — and it could only ever hold one opinion, overwritten
each time. A book read over two weeks produces several thoughts at
different points, and the interesting ones are usually not the last.

So notes are rows, not a column, and they carry their own timestamps.

**Journal text never enters the RAG corpus**, for the same reason the
`review` field never did: Module 5 guarantees every claim in a summary is
traceable to a cited official source, and a reader's own note promoted to
a citable chunk would let a summary quote them back at themselves
formatted as criticism — fluent, correctly cited, and false.

The parent is the **library entry**, not the book: a journal note is
something a specific reader wrote, and it has no meaning detached from
that relationship. Hanging it off `library_entries` also means removing a
book from your library takes its notes with it, through one `ON DELETE
CASCADE` rather than a cleanup step someone has to remember.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.library import LibraryEntry

#: Longest note accepted, mirrored by the client's input. Generous
#: enough for a page of thought, bounded so a paste of an entire book
#: cannot land in a text column nothing paginates.
MAX_JOURNAL_LENGTH = 5000


class JournalEntry(Base):
    """One dated note a reader wrote about a book in their library.

    Attributes:
        id: Unique identifier.
        library_entry_id: The reader-book relationship this belongs to.
            Ownership is resolved through it — there is no `user_id` here
            to drift out of agreement with the parent.
        content: The note itself.
        created_at: When it was written. This is what makes the journal a
            journal: entries are ordered by it and shown with their date,
            so a thought from chapter three stays visibly earlier than
            the one written at the end.
        updated_at: When it was last edited. Distinct from `created_at`,
            so fixing a typo does not silently move a note's place in the
            timeline.
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        # The shape of every read: one book's journal, oldest first.
        Index("ix_journal_entry_created", "library_entry_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_entry_id: Mapped[int] = mapped_column(
        ForeignKey("library_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    library_entry: Mapped["LibraryEntry"] = relationship(back_populates="journal")

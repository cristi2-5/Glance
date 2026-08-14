"""Dated notes a reader writes about a book in their library.

**Every function reaches the journal through the library entry**, and
`library_service` resolves that entry with its owner filter already
applied. There is no query here that starts from `journal_entries` and
checks ownership afterwards — the note id alone is guessable, and a
"fetch by id, then compare the user" shape is exactly the one that works
until someone writes the fetch and forgets the compare.

Journal text is never part of the RAG corpus. See `app/models/journal.py`
for why that boundary matters more here than it looks.
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidData, ResourceNotFound
from app.models.journal import JournalEntry
from app.services import library_service

logger = structlog.get_logger(__name__)


async def list_entries(db: AsyncSession, user_id: int, book_id: int) -> list[JournalEntry]:
    """Returns a book's journal for one reader, oldest note first.

    Chronological rather than newest-first: this is a journal, and its
    value is watching an opinion move between chapter three and the last
    page. Reversing it would put the conclusion above the doubt that
    produced it.

    Args:
        db: The current database session.
        user_id: The reader.
        book_id: The book whose journal to read.

    Returns:
        The notes, oldest first. Empty when nothing has been written.

    Raises:
        ResourceNotFound: If this book is not in the reader's library.
    """
    entry = await library_service.get_entry(db, user_id, book_id)
    statement = (
        select(JournalEntry)
        .where(JournalEntry.library_entry_id == entry.id)
        .order_by(JournalEntry.created_at, JournalEntry.id)
    )
    return list(await db.scalars(statement))


async def add_entry(db: AsyncSession, user_id: int, book_id: int, content: str) -> JournalEntry:
    """Writes a new note into a book's journal.

    The library entry is created on demand if the reader somehow reached
    the book without one — writing about a book is a stronger statement
    of ownership than scanning it, and answering it with a 404 because
    the bookkeeping row is missing would be absurd.

    Args:
        db: The current database session.
        user_id: The reader.
        book_id: The book being written about.
        content: The note. Trimmed; must not be blank after trimming.

    Returns:
        The created note.

    Raises:
        ResourceNotFound: If no cached book has this id.
        InvalidData: If the note is blank once trimmed.
    """
    text = _require_text(content)
    entry = await library_service.ensure_entry(db, user_id, book_id)

    note = JournalEntry(library_entry_id=entry.id, content=text)
    db.add(note)
    await db.commit()
    await db.refresh(note)

    logger.info("journal_entry_added", user_id=user_id, book_id=book_id, entry_id=note.id)
    return note


async def edit_entry(
    db: AsyncSession, user_id: int, book_id: int, entry_id: int, content: str
) -> JournalEntry:
    """Rewrites the text of one note.

    Only `updated_at` moves. `created_at` is what places the note in the
    timeline, and fixing a typo must not relocate a thought to the day it
    was corrected.

    Args:
        db: The current database session.
        user_id: The reader.
        book_id: The book the note belongs to.
        entry_id: The note to rewrite.
        content: The replacement text.

    Returns:
        The updated note.

    Raises:
        ResourceNotFound: If the book is not in the reader's library, or
            the note does not belong to it.
        InvalidData: If the replacement is blank once trimmed.
    """
    text = _require_text(content)
    note = await _owned_note(db, user_id, book_id, entry_id)

    note.content = text
    await db.commit()
    await db.refresh(note)

    logger.info("journal_entry_edited", user_id=user_id, book_id=book_id, entry_id=entry_id)
    return note


async def delete_entry(db: AsyncSession, user_id: int, book_id: int, entry_id: int) -> None:
    """Removes one note from a book's journal.

    Args:
        db: The current database session.
        user_id: The reader.
        book_id: The book the note belongs to.
        entry_id: The note to remove.

    Raises:
        ResourceNotFound: If the book is not in the reader's library, or
            the note does not belong to it.
    """
    note = await _owned_note(db, user_id, book_id, entry_id)
    await db.delete(note)
    await db.commit()
    logger.info("journal_entry_deleted", user_id=user_id, book_id=book_id, entry_id=entry_id)


async def _owned_note(db: AsyncSession, user_id: int, book_id: int, entry_id: int) -> JournalEntry:
    """Resolves a note, proving it belongs to this reader's copy of this book.

    The note is looked up **by its parent as well as its id**, so a note
    id belonging to someone else's library entry simply does not match —
    rather than being fetched and then judged, which is the shape that
    fails silently the day the judgement is left out.

    Args:
        db: The current database session.
        user_id: The reader.
        book_id: The book the note should belong to.
        entry_id: The note id.

    Returns:
        The note.

    Raises:
        ResourceNotFound: If the book is not in the reader's library, or
            no note with this id hangs off it.
    """
    entry = await library_service.get_entry(db, user_id, book_id)
    statement = select(JournalEntry).where(
        JournalEntry.id == entry_id,
        JournalEntry.library_entry_id == entry.id,
    )
    note: JournalEntry | None = await db.scalar(statement)
    if note is None:
        raise ResourceNotFound("This journal note was not found.")
    return note


def _require_text(content: str) -> str:
    """Trims a submitted note and refuses a blank one.

    A note of three spaces is not a note. Storing it would put a blank
    card in the timeline with a date on it and no obvious way to read
    what it says, because it says nothing.

    Args:
        content: The submitted text.

    Returns:
        The trimmed text.

    Raises:
        InvalidData: If nothing is left after trimming.
    """
    trimmed = content.strip()
    if not trimmed:
        raise InvalidData("A journal note cannot be empty.")
    return trimmed

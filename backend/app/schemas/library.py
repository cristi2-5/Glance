"""Pydantic schemas for the personal library: entries, stats, preferences."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.journal import MAX_JOURNAL_LENGTH
from app.models.library import MAX_RATING, MIN_RATING, ReadingStatus


class LibraryBook(BaseModel):
    """The book fields a library list needs, and no more.

    Deliberately narrower than `BookRead`: a history screen renders a
    cover, a title and a couple of chips, and shipping the full blurb and
    every ISBN for each of a hundred entries is payload nobody displays.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: str | None = None
    cover_url: str | None = None
    categories: list[str] = []
    average_rating: float | None = None


class LibraryEntryRead(BaseModel):
    """One book in the user's library, with what they have done with it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    book: LibraryBook
    status: ReadingStatus
    #: The user's own rating, distinct from `book.average_rating`, which is
    #: the catalog's. Both appear on the same card, so they must not be
    #: collapsed into one field.
    rating: int | None = None
    #: How many journal notes the reader has written. The notes themselves
    #: are fetched separately — a list of fifty books does not need every
    #: word anyone wrote about any of them.
    journal_count: int = 0
    scan_count: int
    first_scanned_at: datetime | None = None
    read_at: datetime | None = None
    updated_at: datetime


class LibraryEntryUpdate(BaseModel):
    """A change to one library entry.

    **Every field is optional, and "absent" is not "null".** Clearing a
    rating has to stay expressible while a status-only update leaves the
    rating alone. Pydantic distinguishes the two through
    `model_fields_set`, which the service reads — so `{"rating": null}`
    clears the rating while `{}` leaves it alone. Setting both to `None`
    by omission would otherwise be the accidental default of every
    partial update the client sends.
    """

    status: ReadingStatus | None = None
    rating: int | None = Field(default=None, ge=MIN_RATING, le=MAX_RATING)


class LibraryStats(BaseModel):
    """Counters for the profile header.

    Computed server-side from the whole library rather than from the
    length of whatever page the client happens to hold: once the history
    list paginates, `history.length` stops being the number of books.
    """

    books_scanned: int
    books_read: int
    ratings_given: int
    #: How many journal notes the reader has written, across every book.
    journal_notes: int
    #: The mean of this user's own ratings, `None` before the first one.
    average_rating: float | None = None


class LibraryPreferences(BaseModel):
    """Favorite genres and authors, **derived** from the library.

    Not a form the user fills in: derived from the books they rated well,
    so the profile can never disagree with the history shown underneath
    it. The client labels them as based on reading, because an inferred
    preference presented as a declared one is a small lie on screen.
    """

    favorite_genres: list[str] = []
    favorite_authors: list[str] = []
    #: How many entries the derivation had to work with. `0` means the
    #: lists are empty because nothing has been rated yet — an honest
    #: empty state, not a failure, and the client says so.
    based_on: int = 0


class JournalEntryRead(BaseModel):
    """One dated note from a reader's journal about a book."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    created_at: datetime
    #: Distinct from `created_at`, so the client can show "edited" without
    #: moving the note's place in the timeline.
    updated_at: datetime


class JournalEntryWrite(BaseModel):
    """The text of a journal note, on create or edit.

    `content` is required and non-empty: an empty note is a delete, and
    conflating the two would leave blank cards in the timeline that the
    reader has no obvious way to remove.
    """

    content: str = Field(min_length=1, max_length=MAX_JOURNAL_LENGTH)

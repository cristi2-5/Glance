"""The personal library: history, status, ratings, notes, and what they imply.

Every function here takes `user_id` as a **required** parameter and every
statement filters on it. That is the same reasoning as the `book_id`
filter in `vector_store.py`, one layer up: a library query that forgets
its owner returns other people's reading, and it returns it *plausibly* —
a list of books renders identically whoever they belong to, so nothing
downstream can notice. Ownership is therefore checked at the query, not
after it, and there is no "fetch the entry then compare the user" path
that could be written without the comparison.

Preferences are **derived**, never declared. The favorite genres and
authors on the profile are computed from the books this user actually
rated well, so the header can never contradict the history rendered under
it — which is exactly what a stale declared list would eventually do.
"""

from collections import Counter
from datetime import datetime
from typing import Any, TypeVar

import structlog
from sqlalchemy import Select, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFound
from app.models.book import Book
from app.models.journal import JournalEntry
from app.models.library import LibraryEntry, ReadingStatus
from app.schemas.library import LibraryEntryUpdate, LibraryPreferences, LibraryStats

logger = structlog.get_logger(__name__)

#: Any `SELECT`, whatever its row type — see `_owned`, which must return
#: the same statement type it was handed.
StatementT = TypeVar("StatementT", bound=Select[Any])

#: How many chips the profile shows. Beyond this the list stops being a
#: characterisation and becomes a dump of every genre ever touched.
MAX_FAVORITE_GENRES = 6
MAX_FAVORITE_AUTHORS = 5

#: The rating from which a book counts as a favorite for derivation. A 3
#: is "finished it", not "want more like it", and treating it as a
#: preference signal is what makes recommender output drift into mush.
FAVORITE_RATING_FLOOR = 4

#: The rating at or below which a book counts as actively disliked, feeding
#: the Module 6b exclusion set. 3 sits deliberately in neither band: it is
#: "that was fine", which is evidence of neither appetite nor aversion, and
#: forcing it into one is how a recommender ends up avoiding half a
#: library's genres on the strength of a shrug.
DISLIKED_RATING_CEILING = 2


async def record_scan(db: AsyncSession, user_id: int, book_id: int) -> LibraryEntry:
    """Records that a user scanned a book, without duplicating the entry.

    Called from the cover pipeline, so the history fills itself. Nobody
    manually curates a library in an app whose entire purpose is
    photographing covers, and a history that stays empty unless the user
    populates it stays empty.

    A rescan is the normal case — the same cover gets photographed again
    while testing, or because the user forgot they had it. It increments
    `scan_count` and touches nothing the user has set: a book scanned a
    second time must not lose the rating or note attached to it, and must
    not become a second row in the history.

    Args:
        db: The current database session.
        user_id: The scanning user.
        book_id: The book the scan resolved to.

    Returns:
        The created or updated entry.
    """
    now = datetime.utcnow()
    entry = await _get_entry(db, user_id, book_id)

    if entry is None:
        entry = LibraryEntry(
            user_id=user_id,
            book_id=book_id,
            status=ReadingStatus.SCANNED.value,
            scan_count=1,
            first_scanned_at=now,
        )
        try:
            # A savepoint, not the outer transaction: two scans of the
            # same cover can race here, and the loser must fall back to
            # reading the winner's row rather than poisoning the session
            # the caller is still using to complete the job. The `add`
            # lives *inside* it so the rollback also expunges the pending
            # object — left in `session.new`, it would be re-flushed by
            # the very next commit and raise all over again.
            async with db.begin_nested():
                db.add(entry)
                await db.flush()
        except IntegrityError:
            logger.info("library_entry_race_resolved", user_id=user_id, book_id=book_id)
            existing = await _get_entry(db, user_id, book_id)
            if existing is None:
                raise
            entry = existing
            entry.scan_count += 1
            entry.updated_at = now
    else:
        entry.scan_count += 1
        entry.updated_at = now
        if entry.first_scanned_at is None:
            entry.first_scanned_at = now

    await db.commit()
    await db.refresh(entry)
    return entry


async def discard_scan_artifact(db: AsyncSession, user_id: int, book_id: int) -> bool:
    """Removes an entry that only a misidentified scan ever created.

    Correcting a recognized title means the pipeline already recorded the
    *wrong* book in this user's library. Left there, the history fills
    with books the user never held — and, worse, the Module 6 profile
    vector learns from them.

    But a correction must not delete something the user actually did. So
    the entry is discarded only when it is a pure scan artifact: no
    rating, no journal note, and still in `SCANNED`, meaning nobody ever
    touched it deliberately. Anything else is the user's, and stays —
    even if they reached it by mistake, they since said something about
    it. Discarding it would take the journal notes with it, through the
    cascade.

    Args:
        db: The current database session.
        user_id: The owner of the entry.
        book_id: The previously recognized book.

    Returns:
        `True` if an entry was removed, `False` if there was none or it
        held user-supplied data.
    """
    entry = await _get_entry(db, user_id, book_id)
    if entry is None:
        return False

    touched = (
        entry.rating is not None
        or len(entry.journal) > 0
        or entry.status != ReadingStatus.SCANNED.value
    )
    if touched:
        logger.info("library_artifact_kept", user_id=user_id, book_id=book_id)
        return False

    await db.delete(entry)
    await db.commit()
    logger.info("library_artifact_discarded", user_id=user_id, book_id=book_id)
    return True


async def get_entry(db: AsyncSession, user_id: int, book_id: int) -> LibraryEntry:
    """Returns this user's entry for a book.

    Args:
        db: The current database session.
        user_id: The owner.
        book_id: The book.

    Returns:
        The entry.

    Raises:
        ResourceNotFound: If this user has no entry for this book. The
            404 is about *their* entry and never about the book, so
            another user having one is indistinguishable from nobody
            having one.
    """
    entry = await _get_entry(db, user_id, book_id)
    if entry is None:
        raise ResourceNotFound("This book is not in your library.")
    return entry


async def ensure_entry(db: AsyncSession, user_id: int, book_id: int) -> LibraryEntry:
    """Returns this user's entry for a book, creating an empty one if missing.

    Creating on demand is what lets a reader rate or write about a book
    without the bookkeeping row getting in the way. The pipeline has made
    it already in the normal case, but a book reached some other way — a
    corrected title, a link into the book screen — should not answer
    "rate this" with a 404 about a row the reader never knew existed.

    `scan_count` stays at 0 here, so `books_scanned` keeps counting what
    was actually photographed rather than everything the reader touched.

    Args:
        db: The current database session.
        user_id: The owner.
        book_id: The book.

    Returns:
        The existing or newly created entry. Not yet committed when newly
        created — the caller commits along with whatever it is doing.

    Raises:
        ResourceNotFound: If no cached book has this id. An entry pointing
            at a book that does not exist would render as a blank card
            forever.
    """
    book = await db.get(Book, book_id)
    if book is None:
        raise ResourceNotFound("No book with this id.")

    entry = await _get_entry(db, user_id, book_id)
    if entry is not None:
        return entry

    entry = LibraryEntry(
        user_id=user_id,
        book_id=book_id,
        status=ReadingStatus.SCANNED.value,
        scan_count=0,
    )
    db.add(entry)
    await db.flush()
    return entry


async def update_entry(
    db: AsyncSession,
    user_id: int,
    book_id: int,
    update: LibraryEntryUpdate,
) -> LibraryEntry:
    """Applies a partial change to one entry, creating it if it is missing.

    Creating on demand is what lets the user rate a book straight from
    the scan result: the pipeline has already made the entry in the
    normal case, but a book reached some other way (a corrected title, a
    summary opened from a link) should not answer "rate this" with a 404.

    **Absent and null are different.** Only the fields actually present in
    the request body are touched, so a status change cannot silently
    erase a rating, while `{"rating": null}` still clears it
    deliberately. See `LibraryEntryUpdate`.

    Args:
        db: The current database session.
        user_id: The owner of the entry.
        book_id: The book being updated.
        update: The fields to change. Fields left out are left alone.

    Returns:
        The updated entry.

    Raises:
        ResourceNotFound: If no cached book has this id. An entry pointing
            at a book that does not exist would render as a blank card
            forever.
    """
    entry = await ensure_entry(db, user_id, book_id)

    now = datetime.utcnow()
    provided = update.model_fields_set

    if "status" in provided and update.status is not None:
        entry.status = update.status.value
        # `read_at` records a claim the user made; it must not outlive it.
        # Moving a book back to "reading" and leaving the finish date
        # behind would put a book in the "finished on" list that the user
        # has explicitly said they have not finished.
        entry.read_at = now if update.status is ReadingStatus.READ else None

    if "rating" in provided:
        entry.rating = update.rating
        entry.rated_at = now if update.rating is not None else None

    entry.updated_at = now
    await db.commit()
    await db.refresh(entry)

    logger.info(
        "library_entry_updated",
        user_id=user_id,
        book_id=book_id,
        fields=sorted(provided),
    )
    return entry


async def delete_entry(db: AsyncSession, user_id: int, book_id: int) -> None:
    """Removes a book from the user's library.

    Args:
        db: The current database session.
        user_id: The owner of the entry.
        book_id: The book to remove.

    Raises:
        ResourceNotFound: If this user has no entry for this book. Note
            the 404 is on *the user's* entry, not the book: another user
            having one must be indistinguishable from nobody having one.
    """
    entry = await _get_entry(db, user_id, book_id)
    if entry is None:
        raise ResourceNotFound("This book is not in your library.")

    await db.delete(entry)
    await db.commit()
    logger.info("library_entry_deleted", user_id=user_id, book_id=book_id)


async def list_entries(
    db: AsyncSession,
    user_id: int,
    status: ReadingStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LibraryEntry]:
    """Returns the user's library, most recently touched first.

    Args:
        db: The current database session.
        user_id: The owner whose entries to return.
        status: Restrict to one reading status, or `None` for all.
        limit: Maximum entries to return.
        offset: How many to skip, for pagination.

    Returns:
        The matching entries, newest first. Empty when the library is.
    """
    statement = _owned(select(LibraryEntry), user_id)
    if status is not None:
        statement = statement.where(LibraryEntry.status == status.value)

    statement = statement.order_by(LibraryEntry.updated_at.desc(), LibraryEntry.id.desc())
    result = await db.scalars(statement.limit(limit).offset(offset))
    return list(result)


async def list_rated_entries(db: AsyncSession, user_id: int) -> list[LibraryEntry]:
    """Returns every entry this user has rated, whatever the score.

    Unpaginated, unlike `list_entries`, and that is the point: this feeds
    the Module 6b profile vector and its exclusion set, and a profile built
    from the first page of a library is a profile of whatever the reader
    happened to touch most recently.

    Both ends of the scale come back in one query. They are read for
    opposite purposes — high ratings build the profile vector, low ones
    build the author/genre exclusion set — but splitting the query would
    let the two be taken either side of a concurrent write, and an author
    could then be liked and excluded at once.

    Args:
        db: The current database session.
        user_id: The owner whose entries to return.

    Returns:
        The rated entries, ordered by rating descending then by book id, so
        two identical requests derive the same profile. Empty before the
        first rating, which is the normal state of a new account.
    """
    statement = (
        _owned(select(LibraryEntry), user_id)
        .where(LibraryEntry.rating.is_not(None))
        .order_by(LibraryEntry.rating.desc(), LibraryEntry.book_id)
    )
    return list(await db.scalars(statement))


async def owned_book_ids(db: AsyncSession, user_id: int) -> set[int]:
    """Returns the ids of every book in this user's library.

    The exclusion set for recommendations: a book the reader has already
    scanned, shelved or read is not a suggestion. Read as ids rather than
    entries because that is all the caller needs and a library can be long.

    Args:
        db: The current database session.
        user_id: The owner.

    Returns:
        The book ids, empty for a library that is.
    """
    statement = _owned(select(LibraryEntry.book_id), user_id)
    return set(await db.scalars(statement))


async def compute_stats(db: AsyncSession, user_id: int) -> LibraryStats:
    """Computes the profile counters from the whole library.

    Aggregated in **one** statement on purpose. Five separate counts can
    be taken either side of a concurrent write and produce a header that
    contradicts itself — "3 books, 4 ratings" — which reads as a broken
    app rather than a race.

    Args:
        db: The current database session.
        user_id: The user to count for.

    Returns:
        The counters. All zero for an empty library, with
        `average_rating=None` rather than `0.0`: never having rated
        anything is not the same as having rated everything zero.
    """
    # Journal notes live one table away, so they come in as a scalar
    # subquery rather than a second round trip — the point of aggregating
    # in one statement is that no counter can be read from a different
    # moment than its neighbours.
    journal_total = (
        select(func.count(JournalEntry.id))
        .join(LibraryEntry, JournalEntry.library_entry_id == LibraryEntry.id)
        .where(LibraryEntry.user_id == user_id)
        .scalar_subquery()
    )

    statement = _owned(
        select(
            func.count(case((LibraryEntry.first_scanned_at.is_not(None), 1))),
            func.count(case((LibraryEntry.status == ReadingStatus.READ.value, 1))),
            func.count(LibraryEntry.rating),
            func.avg(LibraryEntry.rating),
            journal_total,
        ),
        user_id,
    )
    row = (await db.execute(statement)).one()
    scanned, read, rated, average, notes = row

    return LibraryStats(
        books_scanned=scanned or 0,
        books_read=read or 0,
        ratings_given=rated or 0,
        journal_notes=notes or 0,
        average_rating=round(float(average), 2) if average is not None else None,
    )


async def derive_preferences(db: AsyncSession, user_id: int) -> LibraryPreferences:
    """Derives favorite genres and authors from what the user rated well.

    Only books rated `FAVORITE_RATING_FLOOR` or above count. Marking a
    book read says it was finished; rating it 4 or 5 says it was liked,
    and only the second is a preference. A library with nothing rated
    yet therefore yields **empty lists**, which is the honest answer —
    the client asks the user to rate something rather than inventing
    tastes out of whatever happened to pass the camera.

    Ties are broken alphabetically. Without that, equal counts come back
    in whatever order the rows arrive and the profile chips reshuffle
    between two identical requests, which looks like a bug.

    Args:
        db: The current database session.
        user_id: The user whose library to derive from.

    Returns:
        The derived preferences, with `based_on` reporting how many
        entries the derivation had to work with.
    """
    statement = _owned(select(LibraryEntry), user_id).where(
        LibraryEntry.rating.is_not(None),
        LibraryEntry.rating >= FAVORITE_RATING_FLOOR,
    )
    entries = list(await db.scalars(statement))

    genres: Counter[str] = Counter()
    authors: Counter[str] = Counter()

    for entry in entries:
        book = entry.book
        for category in book.categories or []:
            label = category.strip()
            if label:
                genres[label] += 1
        if book.author and book.author.strip():
            authors[book.author.strip()] += 1

    return LibraryPreferences(
        favorite_genres=_top(genres, MAX_FAVORITE_GENRES),
        favorite_authors=_top(authors, MAX_FAVORITE_AUTHORS),
        based_on=len(entries),
    )


def _owned(statement: StatementT, user_id: int) -> StatementT:
    """Applies the ownership filter every library query must carry.

    A single place to add it means a query written later cannot omit it
    by simply not thinking about it — see the module docstring.

    Generic in the statement type so the row type survives: narrowing to
    a common supertype here would erase `Select[tuple[LibraryEntry]]` and
    hand every caller an `Any` back, which is how a filter helper quietly
    disables type checking for everything downstream of it.

    Args:
        statement: The statement to constrain.
        user_id: The only user whose rows may be returned.

    Returns:
        The statement, filtered to that user.
    """
    return statement.where(LibraryEntry.user_id == user_id)


async def _get_entry(db: AsyncSession, user_id: int, book_id: int) -> LibraryEntry | None:
    """Returns this user's entry for a book, or `None`.

    Args:
        db: The current database session.
        user_id: The owner.
        book_id: The book.

    Returns:
        The entry, or `None` when this user has none for this book.
    """
    statement = _owned(select(LibraryEntry), user_id).where(LibraryEntry.book_id == book_id)
    entry: LibraryEntry | None = await db.scalar(statement)
    return entry


def _top(counts: "Counter[str]", limit: int) -> list[str]:
    """Returns the most frequent labels, ties broken alphabetically.

    Args:
        counts: Label frequencies.
        limit: How many labels to keep.

    Returns:
        The top labels, most frequent first.
    """
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    return [label for label, _ in ordered[:limit]]

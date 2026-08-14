"""Tests for the personal library: history, status, ratings, notes, stats.

The two properties worth the most here are the ones that fail *quietly*
in production. A rescan that duplicates the history looks like a
plausible list of books — nothing raises, and the profile just says a
number that is too large. A query that forgets its owner returns another
user's reading, rendered identically to the reader's own. Neither is
visible from the response shape, so both are asserted directly.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import InvalidData
from app.models.book import Book
from app.models.library import LibraryEntry, ReadingStatus
from app.models.user import User
from app.schemas.library import LibraryEntryUpdate
from app.services import journal_service, library_service


async def _register(client: AsyncClient, email: str = "reader@example.com") -> dict[str, str]:
    """Registers and logs a user in, returning an auth header for them."""
    password = "super-secret-password"
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _book(title: str, author: str, categories: list[str]) -> Book:
    """A minimal cached book, as the fetcher would have stored it."""
    return Book(
        normalized_key=f"{title.casefold()}|{author.casefold()}",
        title=title,
        author=author,
        categories=categories,
        metadata_found=True,
    )


async def _seed_books(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    """Persists a small shelf and returns its titles mapped to ids."""
    async with db_session_factory() as db:
        books = [
            _book("Dune", "Frank Herbert", ["Science fiction", "Adventure"]),
            _book("Foundation", "Isaac Asimov", ["Science fiction"]),
            _book("Baltagul", "Mihail Sadoveanu", ["Romanian literature"]),
        ]
        for book in books:
            db.add(book)
        await db.commit()
        return {book.title: book.id for book in books}


@pytest.fixture
async def user_id(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[int]:
    """A persisted user id, for the service-level tests."""
    async with db_session_factory() as db:
        user = User(email="service@example.com", hashed_password="x")
        db.add(user)
        await db.commit()
        yield user.id


# --- Rescan must not duplicate -------------------------------------------------


async def test_rescanning_the_same_book_updates_one_entry(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Scanning a cover three times leaves one entry with `scan_count=3`.

    The whole reason `(user_id, book_id)` is unique. Without it the
    history shows the same book three times and every counter derived
    from it — including Module 6's profile vector — weighs it three
    times over.
    """
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        for _ in range(3):
            await library_service.record_scan(db, user_id, books["Dune"])

        entries = list(await db.scalars(select(LibraryEntry)))

    assert len(entries) == 1
    assert entries[0].scan_count == 3
    assert entries[0].first_scanned_at is not None


async def test_rescan_preserves_the_users_rating_and_note(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """A second scan must not reset what the user recorded about the book."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        await library_service.record_scan(db, user_id, books["Dune"])
        await library_service.update_entry(
            db,
            user_id,
            books["Dune"],
            LibraryEntryUpdate(status=ReadingStatus.READ, rating=5),
        )
        await journal_service.add_entry(db, user_id, books["Dune"], "Still holds up.")
        entry = await library_service.record_scan(db, user_id, books["Dune"])

    assert entry.rating == 5
    assert entry.status == ReadingStatus.READ.value
    assert [note.content for note in entry.journal] == ["Still holds up."]
    assert entry.scan_count == 2


# --- Partial updates -----------------------------------------------------------


async def test_setting_a_status_does_not_erase_the_rating(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """An absent field is left alone; only present ones are applied.

    The trap `LibraryEntryUpdate` exists to close: with plain optional
    fields, every partial update the client sends would null out
    everything it did not mention.
    """
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        await library_service.update_entry(db, user_id, books["Dune"], LibraryEntryUpdate(rating=4))
        entry = await library_service.update_entry(
            db, user_id, books["Dune"], LibraryEntryUpdate(status=ReadingStatus.READING)
        )

    assert entry.rating == 4
    assert entry.status == ReadingStatus.READING.value


async def test_explicit_null_clears_the_rating(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """`{"rating": null}` is a deliberate clear, distinct from omitting it."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        await library_service.update_entry(db, user_id, books["Dune"], LibraryEntryUpdate(rating=4))
        entry = await library_service.update_entry(
            db, user_id, books["Dune"], LibraryEntryUpdate.model_validate({"rating": None})
        )

    assert entry.rating is None
    assert entry.rated_at is None


async def test_leaving_read_clears_the_finish_date(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """`read_at` records a claim and must not outlive it.

    A book moved back to "reading" that kept its finish date would appear
    in a "finished on" list the user has explicitly contradicted.
    """
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        entry = await library_service.update_entry(
            db, user_id, books["Dune"], LibraryEntryUpdate(status=ReadingStatus.READ)
        )
        assert entry.read_at is not None

        entry = await library_service.update_entry(
            db, user_id, books["Dune"], LibraryEntryUpdate(status=ReadingStatus.READING)
        )

    assert entry.read_at is None


async def test_a_blank_journal_note_is_refused(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Three spaces is not a note, and must not become a dated blank card."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        with pytest.raises(InvalidData):
            await journal_service.add_entry(db, user_id, books["Dune"], "   ")

        stats = await library_service.compute_stats(db, user_id)

    assert stats.journal_notes == 0


# --- Stats and derived preferences ---------------------------------------------


async def test_stats_count_the_whole_library(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Counters aggregate over every entry, not over one page of them."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        for book_id in books.values():
            await library_service.record_scan(db, user_id, book_id)

        await library_service.update_entry(
            db,
            user_id,
            books["Dune"],
            LibraryEntryUpdate(status=ReadingStatus.READ, rating=5),
        )
        await journal_service.add_entry(db, user_id, books["Dune"], "Excellent.")
        await journal_service.add_entry(db, user_id, books["Dune"], "Still thinking about it.")
        await library_service.update_entry(
            db, user_id, books["Foundation"], LibraryEntryUpdate(rating=3)
        )

        stats = await library_service.compute_stats(db, user_id)

    assert stats.books_scanned == 3
    assert stats.books_read == 1
    assert stats.ratings_given == 2
    assert stats.journal_notes == 2
    assert stats.average_rating == 4.0


async def test_empty_library_reports_no_average_rather_than_zero(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Never having rated is not the same as having rated everything zero."""
    async with db_session_factory() as db:
        stats = await library_service.compute_stats(db, user_id)

    assert stats.books_scanned == 0
    assert stats.average_rating is None


async def test_preferences_derive_only_from_well_rated_books(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """A 3-star book is "finished", not "want more like it"."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        await library_service.update_entry(db, user_id, books["Dune"], LibraryEntryUpdate(rating=5))
        await library_service.update_entry(
            db, user_id, books["Baltagul"], LibraryEntryUpdate(rating=3)
        )

        preferences = await library_service.derive_preferences(db, user_id)

    assert preferences.based_on == 1
    assert preferences.favorite_authors == ["Frank Herbert"]
    assert "Science fiction" in preferences.favorite_genres
    assert "Romanian literature" not in preferences.favorite_genres


async def test_preferences_are_empty_before_anything_is_rated(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Scanning alone is not a taste, and inventing one would be a lie on screen."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        for book_id in books.values():
            await library_service.record_scan(db, user_id, book_id)
        preferences = await library_service.derive_preferences(db, user_id)

    assert preferences.based_on == 0
    assert preferences.favorite_genres == []
    assert preferences.favorite_authors == []


async def test_preference_ties_are_ordered_deterministically(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Equal counts must not reshuffle between two identical requests.

    Chips that reorder on every refresh read as a bug, not as a tie.
    """
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        for title in ("Dune", "Foundation", "Baltagul"):
            await library_service.update_entry(
                db, user_id, books[title], LibraryEntryUpdate(rating=5)
            )
        first = await library_service.derive_preferences(db, user_id)
        second = await library_service.derive_preferences(db, user_id)

    assert first.favorite_authors == second.favorite_authors
    assert first.favorite_authors == sorted(first.favorite_authors, key=str.casefold)


# --- Correction discards the misidentified scan --------------------------------


async def test_correction_discards_an_untouched_scan_artifact(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """A misidentified book must not stay in the history the user reads."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        await library_service.record_scan(db, user_id, books["Dune"])
        discarded = await library_service.discard_scan_artifact(db, user_id, books["Dune"])
        remaining = list(await db.scalars(select(LibraryEntry)))

    assert discarded is True
    assert remaining == []


async def test_correction_keeps_an_entry_the_user_touched(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Once rated, the entry is the user's — a correction must not delete it."""
    books = await _seed_books(db_session_factory)

    async with db_session_factory() as db:
        await library_service.record_scan(db, user_id, books["Dune"])
        await library_service.update_entry(db, user_id, books["Dune"], LibraryEntryUpdate(rating=5))
        discarded = await library_service.discard_scan_artifact(db, user_id, books["Dune"])
        remaining = list(await db.scalars(select(LibraryEntry)))

    assert discarded is False
    assert len(remaining) == 1


# --- HTTP contract and ownership -----------------------------------------------


async def test_library_is_private_to_its_owner(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """One user's reading is invisible to another, in the list and per book.

    The failure this guards is silent: a list of books renders identically
    whoever they belong to, so a query that dropped its owner filter would
    look like a working feature.
    """
    books = await _seed_books(db_session_factory)
    alice = await _register(client, "alice@example.com")
    bob = await _register(client, "bob@example.com")

    created = await client.put(f"/books/{books['Dune']}/library", json={"rating": 5}, headers=alice)
    assert created.status_code == 200, created.text

    listed = await client.get("/users/me/library", headers=bob)
    assert listed.status_code == 200
    assert listed.json() == []

    # Bob must not even learn that someone has this book in a library.
    fetched = await client.get(f"/books/{books['Dune']}/library", headers=bob)
    assert fetched.status_code == 404

    stats = await client.get("/users/me/stats", headers=bob)
    assert stats.json()["ratings_given"] == 0


async def test_bob_cannot_delete_alices_entry(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A delete scoped to the wrong user is a 404, and changes nothing."""
    books = await _seed_books(db_session_factory)
    alice = await _register(client, "alice@example.com")
    bob = await _register(client, "bob@example.com")

    await client.put(f"/books/{books['Dune']}/library", json={"rating": 5}, headers=alice)

    removed = await client.delete(f"/books/{books['Dune']}/library", headers=bob)
    assert removed.status_code == 404

    still_there = await client.get(f"/books/{books['Dune']}/library", headers=alice)
    assert still_there.status_code == 200
    assert still_there.json()["rating"] == 5


async def test_deleting_an_entry_keeps_the_shared_book(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Removing a book from one library must not destroy the cached book.

    The `Book` row carries the fetched sources and the generated summary,
    and is shared by everyone who scans the same cover.
    """
    books = await _seed_books(db_session_factory)
    alice = await _register(client, "alice@example.com")

    await client.put(f"/books/{books['Dune']}/library", json={"rating": 5}, headers=alice)
    removed = await client.delete(f"/books/{books['Dune']}/library", headers=alice)
    assert removed.status_code == 204

    async with db_session_factory() as db:
        assert await db.get(Book, books["Dune"]) is not None


async def test_library_requires_authentication(client: AsyncClient) -> None:
    """Every library route is behind the access token."""
    for method, path in (
        ("get", "/users/me/library"),
        ("get", "/users/me/stats"),
        ("get", "/users/me/preferences"),
    ):
        response = await getattr(client, method)(path)
        assert response.status_code == 401, path


async def test_rating_an_unknown_book_is_404(client: AsyncClient) -> None:
    """An entry pointing at a book that does not exist is a blank card forever."""
    headers = await _register(client)
    response = await client.put("/books/9999/library", json={"rating": 5}, headers=headers)
    assert response.status_code == 404


async def test_rating_outside_one_to_five_is_rejected(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The bounds are validated, not merely documented."""
    books = await _seed_books(db_session_factory)
    headers = await _register(client)

    for rating in (0, 6, -1):
        response = await client.put(
            f"/books/{books['Dune']}/library", json={"rating": rating}, headers=headers
        )
        assert response.status_code == 422, rating


async def test_library_list_filters_by_status(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`?status=read` returns only what the user marked as finished."""
    books = await _seed_books(db_session_factory)
    headers = await _register(client)

    await client.put(f"/books/{books['Dune']}/library", json={"status": "read"}, headers=headers)
    await client.put(
        f"/books/{books['Foundation']}/library", json={"status": "reading"}, headers=headers
    )

    response = await client.get("/users/me/library?status=read", headers=headers)
    assert response.status_code == 200

    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["book"]["title"] == "Dune"


async def test_entry_carries_both_ratings_separately(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The user's rating and the catalog's average are distinct fields.

    Both appear on the same card, so collapsing them would make the
    reader's own score indistinguishable from the crowd's.
    """
    async with db_session_factory() as db:
        book = _book("Dune", "Frank Herbert", ["Science fiction"])
        book.average_rating = 4.5
        db.add(book)
        await db.commit()
        book_id = book.id

    headers = await _register(client)
    response = await client.put(f"/books/{book_id}/library", json={"rating": 3}, headers=headers)

    payload = response.json()
    assert payload["rating"] == 3
    assert payload["book"]["average_rating"] == 4.5

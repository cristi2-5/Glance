"""Tests for the reading journal: dated notes hanging off a library entry.

The property worth the most here is the one that fails *silently*: a note
id is a small integer and trivially guessable, so a lookup that fetches
by id and checks ownership afterwards works right up until someone writes
the fetch and forgets the check. `_owned_note` resolves through the
library entry instead, and that is asserted directly rather than assumed.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import InvalidData, ResourceNotFound
from app.models.book import Book
from app.models.journal import JournalEntry
from app.models.library import LibraryEntry
from app.models.user import User
from app.services import journal_service, library_service


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    """Registers and logs a user in, returning an auth header for them."""
    password = "super-secret-password"
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_book(db_session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Persists one cached book and returns its id."""
    async with db_session_factory() as db:
        book = Book(
            normalized_key="dune|frank herbert",
            title="Dune",
            author="Frank Herbert",
            categories=["Science fiction"],
            metadata_found=True,
        )
        db.add(book)
        await db.commit()
        return book.id


@pytest.fixture
async def user_id(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[int]:
    """A persisted user id, for the service-level tests."""
    async with db_session_factory() as db:
        user = User(email="service@example.com", hashed_password="x")
        db.add(user)
        await db.commit()
        yield user.id


# --- Writing and reading -------------------------------------------------------


async def test_notes_accumulate_instead_of_overwriting(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """The whole reason the journal replaced a single `review` column.

    A book read over two weeks produces several thoughts at different
    points, and the interesting ones are usually not the last.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        await journal_service.add_entry(db, user_id, book_id, "Slow start.")
        await journal_service.add_entry(db, user_id, book_id, "It clicked at part two.")
        notes = await journal_service.list_entries(db, user_id, book_id)

    assert [note.content for note in notes] == ["Slow start.", "It clicked at part two."]


async def test_writing_creates_the_library_entry_on_demand(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Writing about a book is a stronger claim on it than scanning it.

    Refusing the note because the bookkeeping row is missing would be a
    404 about something the reader never knew existed.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        await journal_service.add_entry(db, user_id, book_id, "Picked this up secondhand.")
        entries = list(await db.scalars(select(LibraryEntry)))

    assert len(entries) == 1
    # Never scanned, so it must not inflate the "books scanned" counter.
    assert entries[0].scan_count == 0


async def test_notes_are_ordered_oldest_first(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Chronological, so a conclusion never sits above the doubt that produced it."""
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        for text in ("first", "second", "third"):
            await journal_service.add_entry(db, user_id, book_id, text)
        notes = await journal_service.list_entries(db, user_id, book_id)

    assert [note.content for note in notes] == ["first", "second", "third"]


async def test_note_text_is_trimmed(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Leading and trailing whitespace never reaches storage."""
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        note = await journal_service.add_entry(db, user_id, book_id, "  a thought  \n")

    assert note.content == "a thought"


async def test_a_blank_note_is_refused(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """An empty note would be a dated blank card with no way to read it."""
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        with pytest.raises(InvalidData):
            await journal_service.add_entry(db, user_id, book_id, "   \n  ")


# --- Editing -------------------------------------------------------------------


async def test_editing_moves_updated_at_but_not_created_at(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Fixing a typo must not relocate a thought to the day it was corrected.

    `created_at` is what places a note in the timeline; if an edit moved
    it, the journal would silently reorder itself every time someone
    corrected a word.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        note = await journal_service.add_entry(db, user_id, book_id, "teh ending is great")
        written_at = note.created_at

        edited = await journal_service.edit_entry(
            db, user_id, book_id, note.id, "the ending is great"
        )

    assert edited.content == "the ending is great"
    assert edited.created_at == written_at
    assert edited.updated_at >= written_at


async def test_editing_with_blank_text_is_refused(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Emptying a note is a delete, and the two must stay distinguishable."""
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        note = await journal_service.add_entry(db, user_id, book_id, "something")
        with pytest.raises(InvalidData):
            await journal_service.edit_entry(db, user_id, book_id, note.id, "  ")


# --- Deletion and cascade ------------------------------------------------------


async def test_deleting_a_note_leaves_the_others(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """Removing one entry from the timeline is not removing the journal."""
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        first = await journal_service.add_entry(db, user_id, book_id, "one")
        await journal_service.add_entry(db, user_id, book_id, "two")

        await journal_service.delete_entry(db, user_id, book_id, first.id)
        notes = await journal_service.list_entries(db, user_id, book_id)

    assert [note.content for note in notes] == ["two"]


async def test_removing_the_book_takes_its_journal_with_it(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """The cascade, asserted rather than assumed.

    SQLite does not enforce `ON DELETE CASCADE` unless `PRAGMA
    foreign_keys` is on — which it is not here — so the notes are removed
    by the ORM cascade, which only works because the relationship is
    eagerly loaded. Orphaned notes would otherwise reappear under the
    next entry that happened to reuse the id.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        await journal_service.add_entry(db, user_id, book_id, "a note")
        await library_service.delete_entry(db, user_id, book_id)
        remaining = list(await db.scalars(select(JournalEntry)))

    assert remaining == []


async def test_a_journalled_book_survives_a_correction(
    db_session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    """A note is the reader saying something — the entry is no longer an artifact.

    `discard_scan_artifact` may only drop rows nobody deliberately
    touched, and dropping this one would take the writing with it.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        await library_service.record_scan(db, user_id, book_id)
        await journal_service.add_entry(db, user_id, book_id, "Actually I did read this.")
        discarded = await library_service.discard_scan_artifact(db, user_id, book_id)

    assert discarded is False


# --- Ownership -----------------------------------------------------------------


async def test_a_note_cannot_be_read_through_another_users_book(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One reader's journal is invisible to another, even for the same book.

    Both users have the same `book_id` — the `Book` row is shared by
    everyone who scans the cover. Only the library entry between them
    differs, which is exactly what the lookup goes through.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        alice = User(email="alice@example.com", hashed_password="x")
        bob = User(email="bob@example.com", hashed_password="x")
        db.add(alice)
        db.add(bob)
        await db.commit()

        await journal_service.add_entry(db, alice.id, book_id, "Alice's private thought.")
        await library_service.record_scan(db, bob.id, book_id)

        bobs_journal = await journal_service.list_entries(db, bob.id, book_id)

    assert bobs_journal == []


async def test_a_note_id_from_another_user_does_not_resolve(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The guessable-id attack, tried directly.

    Bob holds a real note id and a book he legitimately has in his own
    library. The only thing standing between him and Alice's writing is
    that the lookup filters on *his* library entry.
    """
    book_id = await _seed_book(db_session_factory)

    async with db_session_factory() as db:
        alice = User(email="alice@example.com", hashed_password="x")
        bob = User(email="bob@example.com", hashed_password="x")
        db.add(alice)
        db.add(bob)
        await db.commit()

        alices_note = await journal_service.add_entry(db, alice.id, book_id, "Private.")
        await library_service.record_scan(db, bob.id, book_id)

        with pytest.raises(ResourceNotFound):
            await journal_service.edit_entry(db, bob.id, book_id, alices_note.id, "Mine now.")

        with pytest.raises(ResourceNotFound):
            await journal_service.delete_entry(db, bob.id, book_id, alices_note.id)

        # And Alice still has exactly what she wrote.
        alices_journal = await journal_service.list_entries(db, alice.id, book_id)

    assert [note.content for note in alices_journal] == ["Private."]


# --- HTTP contract -------------------------------------------------------------


async def test_journal_round_trip_over_http(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Write, read, edit, delete — the sequence the book screen performs."""
    book_id = await _seed_book(db_session_factory)
    headers = await _register(client, "reader@example.com")

    created = await client.post(
        f"/books/{book_id}/journal", json={"content": "First impressions."}, headers=headers
    )
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]

    listed = await client.get(f"/books/{book_id}/journal", headers=headers)
    assert listed.status_code == 200
    assert [note["content"] for note in listed.json()] == ["First impressions."]

    edited = await client.patch(
        f"/books/{book_id}/journal/{note_id}",
        json={"content": "First impressions, revised."},
        headers=headers,
    )
    assert edited.status_code == 200
    assert edited.json()["content"] == "First impressions, revised."

    removed = await client.delete(f"/books/{book_id}/journal/{note_id}", headers=headers)
    assert removed.status_code == 204

    empty = await client.get(f"/books/{book_id}/journal", headers=headers)
    assert empty.json() == []


async def test_journal_note_count_rides_along_with_the_entry(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A list of books carries the count, not every word of every note."""
    book_id = await _seed_book(db_session_factory)
    headers = await _register(client, "reader@example.com")

    for text in ("one", "two"):
        await client.post(f"/books/{book_id}/journal", json={"content": text}, headers=headers)

    entry = await client.get(f"/books/{book_id}/library", headers=headers)
    assert entry.status_code == 200
    assert entry.json()["journal_count"] == 2

    stats = await client.get("/users/me/stats", headers=headers)
    assert stats.json()["journal_notes"] == 2


async def test_bob_gets_a_404_on_alices_note_over_http(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The ownership guard, through the actual endpoints."""
    book_id = await _seed_book(db_session_factory)
    alice = await _register(client, "alice@example.com")
    bob = await _register(client, "bob@example.com")

    created = await client.post(
        f"/books/{book_id}/journal", json={"content": "Alice's."}, headers=alice
    )
    note_id = created.json()["id"]

    # Bob puts the same book in his own library, so the only thing left
    # protecting the note is which entry it hangs off.
    await client.put(f"/books/{book_id}/library", json={"status": "reading"}, headers=bob)

    stolen = await client.patch(
        f"/books/{book_id}/journal/{note_id}", json={"content": "Bob's."}, headers=bob
    )
    assert stolen.status_code == 404

    intact = await client.get(f"/books/{book_id}/journal", headers=alice)
    assert [note["content"] for note in intact.json()] == ["Alice's."]


async def test_journal_requires_authentication(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Every journal route is behind the access token."""
    book_id = await _seed_book(db_session_factory)

    assert (await client.get(f"/books/{book_id}/journal")).status_code == 401
    assert (
        await client.post(f"/books/{book_id}/journal", json={"content": "x"})
    ).status_code == 401


async def test_a_blank_note_is_rejected_by_the_schema(
    client: AsyncClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The bound is validated at the edge, not only in the service."""
    book_id = await _seed_book(db_session_factory)
    headers = await _register(client, "reader@example.com")

    response = await client.post(f"/books/{book_id}/journal", json={"content": ""}, headers=headers)
    assert response.status_code == 422


async def test_journal_on_an_unknown_book_is_404(client: AsyncClient) -> None:
    """No cached book means nothing to write against."""
    headers = await _register(client, "reader@example.com")
    response = await client.post("/books/9999/journal", json={"content": "hello"}, headers=headers)
    assert response.status_code == 404

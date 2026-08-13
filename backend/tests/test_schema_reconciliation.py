"""Tests for additive schema reconciliation at startup.

These exist because of a bug that shipped: Module 5 added `summary_json`
and `summary_generated_at` to `books`, `create_all` did not alter the
already-existing table, and every book fetch on the developer's real
database died on "no such column" — reported by the nearest `except` as a
vague "fetching the book failed".

The whole test suite passed throughout, and could not have done otherwise:
every test builds a fresh in-memory database from the *current* metadata,
so a drift between the models and an *older* database is invisible to it
by construction. These tests deliberately create that older database.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.init_db import _add_missing_columns
from app.db.session import Base
from app.models import Book, Job, RefreshToken, TextSource, User  # noqa: F401

# Columns Module 5 added to an already-populated table — the exact case
# that broke.
ADDED_IN_MODULE_5 = ("summary_json", "summary_generated_at")


@pytest.fixture
async def file_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """An engine over a real file database.

    A file, not `:memory:`: the bug is about a database that outlives the
    code change, which an in-memory one cannot model.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drifted.db'}")
    try:
        yield engine
    finally:
        await engine.dispose()


async def _columns(engine: AsyncEngine, table: str) -> set[str]:
    """Returns the column names currently present on a table."""
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns(table)}
        )


async def _make_pre_module_5_database(engine: AsyncEngine) -> None:
    """Builds a database in the state Module 5 found it in.

    Creates the current schema, then drops the columns Module 5 added and
    inserts a row — so the table is both out of date *and* populated,
    which is what makes `ADD COLUMN` a real constraint rather than a
    formality.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for column in ADDED_IN_MODULE_5:
            await conn.execute(text(f"ALTER TABLE books DROP COLUMN {column}"))
        # `created_at`/`updated_at` default in Python, not in the database,
        # so a raw INSERT has to supply them.
        await conn.execute(
            text(
                "INSERT INTO books "
                "(normalized_key, title, metadata_found, created_at, updated_at) "
                "VALUES ('dune|frank herbert', 'Dune', 1, :now, :now)"
            ),
            {"now": datetime(2026, 8, 13, 12, 0, 0)},
        )


async def test_create_all_alone_does_not_fix_a_drifted_table(file_engine: AsyncEngine) -> None:
    """The premise: `create_all` will not add a column to an existing table.

    Pinning the behaviour that caused the bug, so the reconciliation below
    is never mistaken for redundant belt-and-braces.
    """
    await _make_pre_module_5_database(file_engine)

    async with file_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    columns = await _columns(file_engine, "books")
    assert not set(ADDED_IN_MODULE_5) & columns


async def test_reconciliation_adds_the_missing_columns(file_engine: AsyncEngine) -> None:
    """The fix: startup reconciliation brings a drifted table up to date."""
    await _make_pre_module_5_database(file_engine)

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    columns = await _columns(file_engine, "books")
    for column in ADDED_IN_MODULE_5:
        assert column in columns


async def test_reconciliation_preserves_existing_rows(file_engine: AsyncEngine) -> None:
    """Existing data survives, with the new columns null.

    `ADD COLUMN` must not be a disguised table rebuild: a cached book
    keeps its identity, and simply has no summary yet.
    """
    await _make_pre_module_5_database(file_engine)

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    async with file_engine.begin() as conn:
        row = (await conn.execute(text("SELECT title, summary_json FROM books WHERE id = 1"))).one()

    assert row.title == "Dune"
    assert row.summary_json is None


async def test_the_orm_can_query_a_reconciled_table(file_engine: AsyncEngine) -> None:
    """The end-to-end assertion: the query that actually failed now works.

    `select(Book)` names every mapped column, which is precisely why a
    single missing one broke every book fetch rather than only the summary
    feature.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    await _make_pre_module_5_database(file_engine)
    session_factory = async_sessionmaker(bind=file_engine, expire_on_commit=False)

    async with session_factory() as db:
        with pytest.raises(Exception, match="no such column"):
            await db.execute(select(Book).where(Book.normalized_key == "dune|frank herbert"))

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    async with session_factory() as db:
        book = (
            await db.execute(select(Book).where(Book.normalized_key == "dune|frank herbert"))
        ).scalar_one()

    assert book.title == "Dune"
    assert book.summary_json is None


async def test_reconciliation_is_idempotent(file_engine: AsyncEngine) -> None:
    """Running it twice changes nothing the second time."""
    await _make_pre_module_5_database(file_engine)

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)
    first = await _columns(file_engine, "books")

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    assert await _columns(file_engine, "books") == first


async def test_a_current_database_is_left_alone(file_engine: AsyncEngine) -> None:
    """A database already matching the models is untouched."""
    async with file_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    before = await _columns(file_engine, "books")

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    assert await _columns(file_engine, "books") == before


async def test_every_mapped_column_is_reachable_after_reconciliation(
    file_engine: AsyncEngine,
) -> None:
    """No mapped column on any table is missing once startup has run.

    The general form of the bug, so the next added column is covered
    without anyone remembering to extend this file.
    """
    await _make_pre_module_5_database(file_engine)

    async with file_engine.begin() as conn:
        await conn.run_sync(_add_missing_columns)

    for table in Base.metadata.sorted_tables:
        present = await _columns(file_engine, table.name)
        missing = {column.name for column in table.columns} - present
        assert not missing, f"{table.name} is still missing {missing}"

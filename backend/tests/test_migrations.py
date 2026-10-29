"""Tests that the Alembic migrations and the models describe the same schema.

This file is the safety net the old `init_db` column reconciliation never
had. The rest of the suite builds its database from `Base.metadata`, so a
model changed without a matching migration is invisible to it by
construction: tests pass, and every real database is one change behind.
Here the database is built *only* by running the migrations, then compared
against the models.

The tests are synchronous on purpose: `alembic.command.upgrade` runs an
async `env.py` through `asyncio.run`, which cannot be called from inside an
already-running event loop (i.e. from an async pytest test).

Each test gets a brand-new, empty PostgreSQL database of its own (created
on the test server and dropped afterwards). It must be separate from the
`glance_test` database the rest of the suite shares, because these tests
need an *unmigrated* database and the migrations create the `vector`
extension themselves - that is exactly what 0002 must prove it can do.
"""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Iterator
from typing import Any, TypeVar

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import SchemaOutOfDate
from app.db.init_db import alembic_config, init_db
from app.db.session import Base
from tests.conftest import _test_database_url

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    async def _wrap() -> T:
        return await awaitable

    return asyncio.run(_wrap())


async def _admin(statement: str) -> None:
    """Runs `CREATE/DROP DATABASE` (which cannot run inside a transaction)."""
    admin = create_async_engine(
        _test_database_url(), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await admin.dispose()


@pytest.fixture
def database_url() -> Iterator[str]:
    """The URL of a new empty database, dropped again after the test."""
    name = f"glance_migrations_{uuid.uuid4().hex[:12]}"
    _run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(_test_database_url()).set(database=name).render_as_string(
            hide_password=False
        )
    finally:
        _run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _diff(url: str) -> list[Any]:
    """Differences Alembic sees between the migrated database and the models."""

    def _compare(conn: Connection) -> list[Any]:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    async def _go() -> list[Any]:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(_compare)
        finally:
            await engine.dispose()

    return _run(_go())


def _tables(url: str) -> set[str]:
    async def _go() -> set[str]:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        finally:
            await engine.dispose()

    return _run(_go())


def test_migrations_match_models(database_url: str) -> None:
    """Upgrading an empty database to head yields exactly the models' schema.

    Fails when a model is changed (column, type, index, constraint) without
    a new migration: fix it with `alembic revision --autogenerate`.
    """
    command.upgrade(alembic_config(database_url), "head")

    assert _diff(database_url) == []


def test_upgrade_downgrade_round_trip(database_url: str) -> None:
    """Every migration can be undone, and redone, without error or leftovers."""
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    assert _tables(database_url) <= {"alembic_version"}

    command.upgrade(cfg, "head")
    assert _diff(database_url) == []


def test_startup_check_refuses_unmigrated_database(database_url: str) -> None:
    """A database with no `alembic_version` makes startup fail loudly."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        with pytest.raises(SchemaOutOfDate):
            _run(init_db(engine))
    finally:
        _run(engine.dispose())


def test_startup_check_accepts_migrated_database(database_url: str) -> None:
    """At head, the startup check passes and changes nothing."""
    command.upgrade(alembic_config(database_url), "head")
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        _run(init_db(engine))
    finally:
        _run(engine.dispose())


def test_startup_check_names_a_database_ahead_of_the_code(database_url: str) -> None:
    """A revision the scripts don't know (an older image rolled back onto a
    newer schema) is reported as "newer than this code", not as "run upgrade",
    which would only fail with "Can't locate revision"."""
    command.upgrade(alembic_config(database_url), "head")
    engine = create_async_engine(database_url, poolclass=NullPool)

    async def _stamp_future() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = '9999_future'"))

    try:
        _run(_stamp_future())
        with pytest.raises(SchemaOutOfDate, match="newer than this code"):
            _run(init_db(engine))
    finally:
        _run(engine.dispose())


def test_vector_tables_exist_and_have_no_approximate_index(database_url: str) -> None:
    """0002 creates the extension and tables, and adds no HNSW/IVFFlat index.

    pgvector filters *after* an approximate scan, so such an index would
    make a `book_id`-filtered chunk query silently return too few rows
    (see `app/models/vector.py`). This keeps the choice from being undone
    by an innocent-looking `CREATE INDEX` in a later migration.
    """
    command.upgrade(alembic_config(database_url), "head")

    async def _inspect() -> tuple[set[str], list[str]]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                extensions = {
                    row[0] for row in await conn.execute(text("SELECT extname FROM pg_extension"))
                }
                indexes = [
                    row[0]
                    for row in await conn.execute(
                        text(
                            "SELECT indexdef FROM pg_indexes "
                            "WHERE tablename IN ('book_chunks', 'book_profiles')"
                        )
                    )
                ]
                return extensions, indexes
        finally:
            await engine.dispose()

    extensions, indexes = _run(_inspect())

    assert "vector" in extensions
    assert indexes, "expected at least the primary-key and book_id indexes"
    assert not [index for index in indexes if re.search(r"hnsw|ivfflat", index, re.IGNORECASE)]

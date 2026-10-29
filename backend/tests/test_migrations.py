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

They run on a temporary SQLite file always, and additionally on the server
named by `TEST_DATABASE_URL` when it is set.
"""

import asyncio
import os
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import SchemaOutOfDate
from app.db.init_db import alembic_config, init_db
from app.db.session import Base

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    async def _wrap() -> T:
        return await awaitable

    return asyncio.run(_wrap())


@pytest.fixture(params=["sqlite", "postgres"])
def database_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """A URL for an empty database: a temp SQLite file, or a wiped Postgres schema."""
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")

    async def _wipe() -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    _run(_wipe())
    return url


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

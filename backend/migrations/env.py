"""Alembic environment: runs migrations against the database Glance is configured for.

The URL comes from `Settings.database_url` (never from `alembic.ini`, so no
credential sits in a committed file) unless the caller has already set the
`sqlalchemy.url` main option — which is how the tests point Alembic at a
temporary database without touching the environment.

The environment is async because the application's drivers are
(`asyncpg`); `run_sync` hands Alembic the synchronous
connection it needs.
"""

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool

from app.core.config import get_settings
from app.db.session import Base, build_engine, dispose_engine

# Required import: registers every model on `Base.metadata`, which is what
# autogenerate and the drift test compare the database against.
from app.models import (  # noqa: F401
    Book,
    BookChunk,
    BookProfileRow,
    Job,
    JournalEntry,
    LibraryEntry,
    RecommendationState,
    RefreshToken,
    TextSource,
    User,
)

config = context.config
target_metadata = Base.metadata

# Without this the `[logger_alembic]` section is dead and `alembic upgrade`
# prints nothing, so the migrate container's log can't say what it applied.
# Only when nothing configured logging yet: under pytest (or inside the app)
# the existing handlers must not be replaced.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    """Resolves the URL to migrate: an explicit override, else the application's setting."""
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(connection: Connection | None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        literal_binds=connection is None,
        # Notice a column whose type changed, not only added/removed ones.
        compare_type=True,
    )


def run_migrations_offline() -> None:
    """Emits the migration SQL to stdout without connecting to a database."""
    _configure(None, url=_database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connects with the async driver and runs the migrations on that connection."""
    # NullPool: a one-shot process has no use for a pool, and a pooled
    # connection would outlive the event loop `asyncio.run` is about to close.
    # `build_engine` is the application's own factory, so with DATABASE_AUTH=entra
    # the migrate job authenticates with its managed identity, like the api.
    engine = build_engine(_database_url(), get_settings(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync_migrations)
    finally:
        await dispose_engine(engine)


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

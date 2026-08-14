"""Table creation and additive schema reconciliation at application startup.

`Base.metadata.create_all` creates tables that don't exist yet. What it
does **not** do — and this is the trap it set once already — is alter a
table that does exist. Adding a column to a model therefore leaves every
existing database silently one column behind, and the failure surfaces far
from its cause: `SELECT` names the new column, SQLite answers "no such
column", and whichever `except` block is nearest reports something vague
like "fetching the book failed".

The tests never catch it, because they build a fresh in-memory database
from the current metadata on every run. Only a database that predates the
change can be wrong, and by definition there isn't one in the suite.

So `init_db` also reconciles **added columns**, which is the only schema
change this project has actually made between modules. Deliberately
nothing else: it will not drop, rename, retype, or reorder anything, and
it will not touch constraints or indexes. Those need real migrations, and
the moment one is required, this is the point to bring in Alembic rather
than to grow another special case here (see CLAUDE.md).

Every reconciliation is logged. A schema change that happened without
anyone deciding it should is worth seeing in the startup output.
"""

import structlog
from sqlalchemy import Connection, inspect, text
from sqlalchemy.schema import CreateColumn

from app.db.session import Base, engine

# Required import: registers the models on `Base.metadata` before
# `create_all`. Without it, their tables would not be created.
from app.models import (  # noqa: F401
    Book,
    Job,
    JournalEntry,
    LibraryEntry,
    RefreshToken,
    TextSource,
    User,
)

logger = structlog.get_logger(__name__)


async def init_db() -> None:
    """Creates missing tables, then adds any columns missing from existing ones."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn: Connection) -> None:
    """Adds columns present on the models but absent from the database.

    Runs against a sync connection (via `run_sync`), because SQLAlchemy's
    inspection API is synchronous.

    Args:
        conn: The synchronous connection to reconcile through.
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            # Just created by `create_all`, so it already matches.
            continue

        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue

            if not _is_safely_addable(column):
                # Rather than emit DDL SQLite will reject — or, worse,
                # accept in a form that silently differs from the model.
                logger.error(
                    "schema_column_needs_migration",
                    table=table.name,
                    column=column.name,
                    reason="NOT NULL without a server default cannot be added to a "
                    "populated table; this needs a real migration.",
                )
                continue

            ddl = CreateColumn(column).compile(bind=conn.engine)
            conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
            logger.warning(
                "schema_column_added",
                table=table.name,
                column=column.name,
                type=str(column.type),
            )


def _is_safely_addable(column: object) -> bool:
    """Decides whether a column can be added to a populated table as-is.

    `ALTER TABLE ... ADD COLUMN` has to give existing rows *some* value. A
    nullable column gets `NULL`; one with a server default gets that. A
    `NOT NULL` column with neither has no correct answer, and SQLite
    refuses it — correctly, since inventing one would be a data decision
    this module has no business making.

    Args:
        column: The SQLAlchemy `Column` being considered.

    Returns:
        `True` when the column can be added without inventing data.
    """
    nullable = getattr(column, "nullable", True)
    server_default = getattr(column, "server_default", None)
    return bool(nullable) or server_default is not None

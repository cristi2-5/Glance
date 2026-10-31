"""Startup check that the database schema is at the latest Alembic revision.

History, because the shape of this module changed on purpose. It used to
call `Base.metadata.create_all` and then reconcile *added columns*:
`create_all` never alters an existing table, so a model gaining a column
left every older database silently one column behind, and the failure
surfaced far away as "fetching the book failed". The reconciliation was a
stopgap that handled the one schema change the project had made so far and
refused everything else (rename, retype, drop, NOT NULL), with a note that
the day one of those was needed Alembic would replace it.

Alembic has replaced it. The schema is now changed only by numbered
migration scripts (`alembic upgrade head`), run as a separate step rather
than here: with several api replicas, each migrating at startup would race.
What remains at startup is a *check*: if the database is not at the head
revision the api refuses to start, loudly, instead of serving requests that
will fail obscurely.

The old reconciliation's safety net (catching a model changed without a
matching change to the database) moved to `tests/test_migrations.py`, which
migrates an empty database to head and asserts Alembic sees no difference
from the models. The old suite could never see this bug class, because it
always built its database from the current metadata.
"""

import structlog
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import BACKEND_DIR
from app.core.exceptions import SchemaOutOfDate
from app.db.session import engine

logger = structlog.get_logger(__name__)

ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def alembic_config(url: str | None = None) -> Config:
    """Builds the Alembic configuration for this project.

    Args:
        url: A database URL to migrate instead of `Settings.database_url`.
            `None` leaves the choice to `migrations/env.py`.

    Returns:
        A `Config` with the script location resolved from `BACKEND_DIR`, so
        it works whatever the process's working directory is.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    if url is not None:
        # configparser treats % as interpolation, which URL-encoded passwords contain.
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _current_revision(conn: Connection) -> str | None:
    """Reads the revision recorded in `alembic_version`, `None` if there is none."""
    return MigrationContext.configure(conn).get_current_revision()


async def init_db(db_engine: AsyncEngine | None = None) -> None:
    """Verifies the database is at the latest migration; never changes it.

    Args:
        db_engine: The engine to check. Defaults to the application's.

    Raises:
        SchemaOutOfDate: If the database has no `alembic_version` table,
            records a revision other than the scripts' head, or the scripts
            themselves have more than one head.
    """
    scripts = ScriptDirectory.from_config(alembic_config())
    try:
        head = scripts.get_current_head()
    except CommandError as exc:
        # Two branches each adding a migration on top of the same parent.
        # Not a database problem, so it must not read like one.
        logger.error("schema_multiple_heads", error=str(exc))
        raise SchemaOutOfDate(
            "Migrations have more than one head; merge them (`alembic merge heads`)."
        ) from exc

    try:
        async with (db_engine or engine).connect() as conn:
            current = await conn.run_sync(_current_revision)
    except Exception as exc:
        # Includes an Azure authentication failure while fetching the Entra
        # token (wrong AZURE_CLIENT_ID, identity not assigned, no Postgres
        # role): without this the cause is buried in a long traceback.
        logger.error(
            "database_connect_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
            hint="check DATABASE_URL, AZURE_CLIENT_ID and the Postgres role of the identity",
        )
        raise

    if current == head:
        logger.info("schema_up_to_date", revision=head)
        return

    # A revision the scripts don't know means the database is *ahead*: an
    # older image rolled back onto a newer schema. "Upgrade" would then fail
    # with "Can't locate revision", so say what actually happened.
    ahead = current is not None and _is_unknown(scripts, current)
    hint = (
        "the database is newer than this code; deploy the matching image"
        if ahead
        else "run `alembic upgrade head`"
    )
    logger.error(
        "schema_revision_mismatch",
        current_revision=current,
        head_revision=head,
        database_ahead=ahead,
        hint=hint,
    )
    raise SchemaOutOfDate(f"Database is at revision {current!r}, code expects {head!r}: {hint}.")


def _is_unknown(scripts: ScriptDirectory, revision: str) -> bool:
    """Whether `revision` is absent from the migration scripts."""
    try:
        return scripts.get_revision(revision) is None
    except CommandError:
        return True

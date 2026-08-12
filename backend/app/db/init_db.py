"""Table creation at application startup.

As long as the schema is simple, `Base.metadata.create_all` is sufficient.
When the schema starts to evolve with real migrations, switch to Alembic
(see CLAUDE.md).
"""

from app.db.session import Base, engine

# Required import: registers the models on `Base.metadata` before
# `create_all`. Without it, their tables would not be created.
from app.models import Book, Job, RefreshToken, TextSource, User  # noqa: F401


async def init_db() -> None:
    """Creates all tables defined on `Base.metadata`, if they don't already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

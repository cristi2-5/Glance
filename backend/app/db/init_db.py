"""Creare tabele la pornirea aplicației.

Cât timp schema e simplă, `Base.metadata.create_all` e suficient. Când
schema începe să evolueze cu migrații reale, se trece la Alembic (vezi
CLAUDE.md).
"""

from app.db.session import Base, engine

# Import obligatoriu: înregistrează modelele pe `Base.metadata` înainte de
# `create_all`. Fără el, tabelele lor nu ar fi create.
from app.models import Job, RefreshToken, User  # noqa: F401


async def init_db() -> None:
    """Creează toate tabelele definite pe `Base.metadata`, dacă nu există deja."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

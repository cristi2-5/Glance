"""Database session setup (SQLAlchemy 2.0 async)."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models in Glance."""


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provides a database session for a single request.

    Yields:
        An `AsyncSession` that closes automatically at the end of the request.
    """
    async with AsyncSessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dependency that exposes the current session factory.

    This indirection exists so that background tasks (which cannot receive
    a request session, already closed by the time they run) can open their
    own session from the same factory that tests override — otherwise a
    worker would always write to the production database, even in tests.

    Returns:
        The `AsyncSessionLocal` session factory.
    """
    return AsyncSessionLocal

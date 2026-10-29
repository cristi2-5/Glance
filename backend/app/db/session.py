"""Database session setup (SQLAlchemy 2.0 async)."""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()


def _engine_kwargs(url: str) -> dict[str, Any]:
    """Chooses pool options that suit the database the URL points at.

    SQLite is a file in our own process and needs nothing. PostgreSQL is a
    server across a network, and in Azure that network is hostile to idle
    connections: the platform closes them silently, and a stopped Flexible
    Server (B1ms) drops every one at once. `pool_pre_ping` tests a pooled
    connection before handing it out and replaces a dead one, instead of
    letting the first request after an idle spell fail. The pool is capped
    at 10 connections per process (5 + 5 overflow) because a B1ms allows
    only ~50 in total, and the worker and extra replicas of later steps
    each bring their own pool.

    Args:
        url: The SQLAlchemy database URL.

    Returns:
        Keyword arguments for `create_async_engine`.
    """
    if make_url(url).get_backend_name() == "postgresql":
        return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5}
    return {}


engine = create_async_engine(
    settings.database_url, echo=False, **_engine_kwargs(settings.database_url)
)

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

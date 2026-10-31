"""Database session setup (SQLAlchemy 2.0 async)."""

import ssl
from collections.abc import AsyncIterator
from typing import Any
from weakref import WeakKeyDictionary

from azure.core.credentials_async import AsyncTokenCredential
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core import identity
from app.core.config import Settings, get_settings

settings = get_settings()

# The credential behind each Entra-authenticated engine, so `dispose_engine`
# can close its HTTP session together with the engine.
_credentials: "WeakKeyDictionary[AsyncEngine, AsyncTokenCredential]" = WeakKeyDictionary()


def _engine_kwargs() -> dict[str, Any]:
    """Chooses the connection-pool options for the PostgreSQL server.

    The database is a server across a network, and in Azure that network is
    hostile to idle connections: the platform closes them silently, and a stopped Flexible
    Server (B1ms) drops every one at once. `pool_pre_ping` tests a pooled
    connection before handing it out and replaces a dead one, instead of
    letting the first request after an idle spell fail. The pool is capped
    at 10 connections per process (5 + 5 overflow) because a B1ms allows
    only ~50 in total, and the queue worker and extra replicas of later steps
    each bring their own pool.

    Returns:
        Keyword arguments for `create_async_engine`.
    """
    return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5}


def build_engine(url: str, app_settings: Settings, **engine_kwargs: Any) -> AsyncEngine:
    """Creates an async engine that authenticates the way the settings say.

    The one place engines are built, used by the application *and* by
    `migrations/env.py`, so the one-shot `migrate` job logs in exactly as the
    api does. With `database_auth="password"` this is a plain engine. With
    `"entra"` (or `app_env="production"`) the connection also verifies the
    server certificate. With `"entra"` the connection password is a callable that fetches a fresh
    Entra ID token from the managed identity for every new connection (see
    `app.core.identity`); the URL then carries only the user and the host.

    Args:
        url: The database URL (without a password in Entra mode).
        app_settings: Supplies `database_auth` and the identity to use.
        **engine_kwargs: Passed to `create_async_engine` (pool options, ...).

    Returns:
        The engine. Release it with `dispose_engine`, which also closes the
        credential of an Entra engine.
    """
    connect_args: dict[str, Any] = {}
    if app_settings.database_auth == "entra" or app_settings.app_env == "production":
        # asyncpg's default (`sslmode=prefer`) encrypts when the server agrees
        # but verifies nothing and can be downgraded to plaintext, so a
        # man-in-the-middle could read the Entra bearer token sent as the
        # password. A default context verifies the certificate chain and the
        # host name against the system CAs (present in python:slim). Passed as
        # an object rather than `?sslmode=verify-full`, which makes asyncpg
        # look for ~/.postgresql/root.crt.
        connect_args["ssl"] = ssl.create_default_context()

    if app_settings.database_auth != "entra":
        return create_async_engine(url, echo=False, connect_args=connect_args, **engine_kwargs)

    credential = identity.create_credential(app_settings)
    # asyncpg accepts a (sync or async) callable as `password` and calls it per
    # connection attempt. SQLAlchemy forwards `connect_args` to `asyncpg.connect`.
    engine = create_async_engine(
        url,
        echo=False,
        connect_args={**connect_args, "password": identity.postgres_token_provider(credential)},
        **engine_kwargs,
    )
    _credentials[engine] = credential
    return engine


async def dispose_engine(target: AsyncEngine) -> None:
    """Disposes an engine and closes the managed-identity credential it used, if any.

    Args:
        target: An engine from `build_engine`.
    """
    await target.dispose()
    credential = _credentials.pop(target, None)
    if credential is not None:
        await credential.close()


engine = build_engine(settings.database_url, settings, **_engine_kwargs())

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
    queue worker would always write to the production database, even in tests.

    Returns:
        The `AsyncSessionLocal` session factory.
    """
    return AsyncSessionLocal

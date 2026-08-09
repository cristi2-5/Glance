"""Fixtures pytest comune tuturor testelor backend-ului Glance."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.session import Base
from app.main import app
from app.models import RefreshToken, User  # noqa: F401  (înregistrează modelele pe Base.metadata)


@pytest.fixture
async def db_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Motor SQLite în memorie, izolat per test, cu toate tabelele create.

    Yields:
        Un `async_sessionmaker` legat de o bază de date curată, unică pentru
        testul curent.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.fixture
async def client(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """Client HTTP async legat de aplicație, cu baza de date suprascrisă pe una de test.

    Yields:
        Un `AsyncClient` httpx configurat cu transportul ASGI al aplicației.
    """

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()

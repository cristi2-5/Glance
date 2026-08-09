"""Configurarea sesiunii de bază de date (SQLAlchemy 2.0 async)."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    """Clasa de bază pentru toate modelele SQLAlchemy din Glance."""


async def get_db() -> AsyncIterator[AsyncSession]:
    """Furnizează o sesiune de bază de date pentru un singur request.

    Yields:
        O sesiune `AsyncSession` care se închide automat la finalul request-ului.
    """
    async with AsyncSessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dependency care expune fabrica de sesiuni curentă.

    Indirecția există ca task-urile de fundal (care nu pot primi o sesiune
    de request, deja închisă până rulează ele) să-și poată deschide propria
    sesiune din aceeași fabrică pe care testele o suprascriu — altfel un
    worker ar scrie mereu în baza de date de producție, chiar și în teste.

    Returns:
        Fabrica de sesiuni `AsyncSessionLocal`.
    """
    return AsyncSessionLocal

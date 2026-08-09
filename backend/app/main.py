"""Punctul de intrare al aplicației FastAPI Glance."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import auth, books, jobs, users
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.init_db import init_db

settings = get_settings()
configure_logging(debug=settings.debug)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Creează tabelele bazei de date la pornirea aplicației."""
    await init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
register_exception_handlers(app)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(books.router)
app.include_router(jobs.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Verifică dacă serviciul rulează.

    Returns:
        Un dicționar cu statusul aplicației și numele ei.
    """
    return {"status": "ok", "app": settings.app_name}

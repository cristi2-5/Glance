"""Entry point of the Glance FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, books, jobs, users
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.services.storage import close_storage

settings = get_settings()
configure_logging(debug=settings.debug)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Refuses to start on a database that is behind the migrations."""
    await init_db()
    yield
    # Close the Blob/Queue HTTP sessions (created lazily on first use).
    await close_storage()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Only needed for clients running in a browser (Expo Web, the test page in
# `/dev`). The native Expo Go app does not go through CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(books.router)
app.include_router(jobs.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Checks whether the service is running.

    Returns:
        A dictionary with the application's status and its name.
    """
    return {"status": "ok", "app": settings.app_name}

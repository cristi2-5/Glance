"""Punctul de intrare al aplicației FastAPI Glance."""

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(debug=settings.debug)

app = FastAPI(title=settings.app_name)
register_exception_handlers(app)


@app.get("/health")
async def health() -> dict[str, str]:
    """Verifică dacă serviciul rulează.

    Returns:
        Un dicționar cu statusul aplicației și numele ei.
    """
    return {"status": "ok", "app": settings.app_name}

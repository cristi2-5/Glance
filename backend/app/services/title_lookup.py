"""Minimal Google Books lookup used to validate OCR/vision guesses.

This is deliberately narrow and lives in the vision layer, not in
`app/services/sources/` — that package belongs to Module 4's full
`ContentSource` implementations (description, categories, ISBN, etc).
`GoogleBooksTitleLookup` reads only `volumeInfo.title`/`authors`, just
enough to score a candidate title against the cover's OCR text.

When Module 4 builds `services/sources/google_books.py`, its `ContentSource`
can satisfy this same `TitleLookup` Protocol and replace this
implementation in `VisionService` without changing vision's logic.
"""

from typing import Protocol

import httpx
import structlog
from pydantic import BaseModel

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

_GOOGLE_BOOKS_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"


class BookCandidate(BaseModel):
    """A single candidate title returned by a title lookup."""

    title: str
    authors: list[str]


class TitleLookup(Protocol):
    """Abstraction over a title-search backend, so it can be faked in tests."""

    async def search(self, query: str, limit: int = 5) -> list[BookCandidate]:
        """Searches for books matching a free-text query.

        Args:
            query: Free-text search terms (e.g. OCR-extracted lines).
            limit: The maximum number of candidates to return.

        Returns:
            Matching candidates, in the backend's relevance order. Empty on
            any failure — a lookup outage degrades confidence, it must not
            fail the caller.
        """
        ...


class GoogleBooksTitleLookup:
    """`TitleLookup` backed by the public Google Books volumes search."""

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.google_books_timeout_seconds

    async def search(self, query: str, limit: int = 5) -> list[BookCandidate]:
        """See `TitleLookup.search`. Uses no API key (public volumes only)."""
        if not query.strip():
            return []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    _GOOGLE_BOOKS_VOLUMES_URL,
                    params={"q": query, "maxResults": limit},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("google_books_lookup_failed", query=query, error=str(exc))
            return []

        candidates = []
        for item in payload.get("items", []):
            info = item.get("volumeInfo", {})
            title = info.get("title")
            if not title:
                continue
            candidates.append(BookCandidate(title=title, authors=info.get("authors", [])))
        return candidates


def build_title_lookup() -> GoogleBooksTitleLookup:
    """Factory for the production `TitleLookup` implementation.

    Returns:
        A `GoogleBooksTitleLookup` configured from application settings.
    """
    return GoogleBooksTitleLookup(get_settings())

"""Catalog lookups used to confirm and canonicalize OCR/vision guesses.

These are deliberately narrow and live in the vision layer, not in
`app/services/sources/` — that package belongs to Module 4's full
`ContentSource` implementations (description, categories, ISBN, etc).
The lookups here read only titles and authors, just enough to score a
candidate against the cover's OCR text.

**Availability is reported, not swallowed.** A lookup that returns no
matches ("this book isn't in the catalog" — common for Romanian editions)
means something very different from one that could not be reached (429,
timeout). The caller lowers confidence in the first case and must not
treat the second as evidence of anything, so `LookupOutcome` carries an
`available` flag alongside the candidates.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog
from pydantic import BaseModel

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

_GOOGLE_BOOKS_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"
_OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"


class BookCandidate(BaseModel):
    """A single candidate title returned by a catalog lookup."""

    title: str
    authors: list[str]


@dataclass(frozen=True)
class LookupOutcome:
    """The result of a catalog query.

    Attributes:
        candidates: The matches found, in the catalog's relevance order.
        available: `False` when no catalog could be reached (quota, timeout,
            network). An empty `candidates` list with `available=True` is a
            real answer: the catalog simply has no such book.
    """

    candidates: list[BookCandidate] = field(default_factory=list)
    available: bool = True

    @classmethod
    def unavailable(cls) -> "LookupOutcome":
        """Builds the outcome representing an unreachable catalog."""
        return cls(candidates=[], available=False)


class TitleLookup(Protocol):
    """Abstraction over a catalog search backend, so it can be faked in tests."""

    async def search(self, query: str, limit: int = 5) -> LookupOutcome:
        """Searches for books matching a free-text query.

        Args:
            query: Free-text search terms (e.g. OCR-extracted lines).
            limit: The maximum number of candidates to return.

        Returns:
            The matches found, plus whether the catalog was reachable at all.
        """
        ...


class GoogleBooksTitleLookup:
    """`TitleLookup` over the Google Books volumes search.

    Sends `Settings.google_books_api_key` when configured. Without a key the
    endpoint shares an anonymous per-project quota that is routinely
    exhausted, answering 429 to every request.
    """

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.google_books_timeout_seconds
        self._api_key = settings.google_books_api_key

    async def search(self, query: str, limit: int = 5) -> LookupOutcome:
        """See `TitleLookup.search`."""
        if not query.strip():
            return LookupOutcome()

        params: dict[str, Any] = {"q": query, "maxResults": limit}
        if self._api_key:
            params["key"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(_GOOGLE_BOOKS_VOLUMES_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "google_books_lookup_unavailable",
                query=query,
                has_api_key=bool(self._api_key),
                error=str(exc),
            )
            return LookupOutcome.unavailable()

        candidates = []
        for item in payload.get("items", []):
            info = item.get("volumeInfo", {})
            title = info.get("title")
            if not title:
                continue
            candidates.append(BookCandidate(title=title, authors=info.get("authors", [])))
        return LookupOutcome(candidates=candidates)


class OpenLibraryTitleLookup:
    """`TitleLookup` over Open Library's search (CC0, no API key, no quota).

    The keyless safety net for when Google Books is unconfigured or
    throttled. Its non-English coverage is thinner, so it confirms fewer
    Romanian editions — that is handled by the caller, which trusts good
    OCR rather than discarding it when no catalog can confirm.
    """

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.open_library_timeout_seconds

    async def search(self, query: str, limit: int = 5) -> LookupOutcome:
        """See `TitleLookup.search`."""
        if not query.strip():
            return LookupOutcome()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    _OPEN_LIBRARY_SEARCH_URL,
                    params={
                        "q": query,
                        "limit": limit,
                        "fields": "title,author_name",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("open_library_lookup_unavailable", query=query, error=str(exc))
            return LookupOutcome.unavailable()

        candidates = []
        for doc in payload.get("docs", []):
            title = doc.get("title")
            if not title:
                continue
            candidates.append(BookCandidate(title=title, authors=doc.get("author_name", [])))
        return LookupOutcome(candidates=candidates)


class ChainedTitleLookup:
    """Tries several catalogs in order, returning the first useful answer.

    Stops at the first backend that is reachable *and* returns matches.
    Reports `available=True` if any backend answered at all — an empty
    result from a reachable catalog is a real "not in the catalog", which
    the caller must be able to distinguish from a total outage.
    """

    def __init__(self, lookups: list[TitleLookup]) -> None:
        self._lookups = lookups

    async def search(self, query: str, limit: int = 5) -> LookupOutcome:
        """See `TitleLookup.search`."""
        any_available = False

        for lookup in self._lookups:
            outcome = await lookup.search(query, limit)
            if outcome.available:
                any_available = True
                if outcome.candidates:
                    return outcome

        return LookupOutcome(candidates=[], available=any_available)


def build_title_lookup() -> ChainedTitleLookup:
    """Factory for the production `TitleLookup` chain.

    Returns:
        Google Books first (better metadata, needs a key), Open Library as
        the keyless fallback.
    """
    settings = get_settings()
    return ChainedTitleLookup([GoogleBooksTitleLookup(settings), OpenLibraryTitleLookup(settings)])

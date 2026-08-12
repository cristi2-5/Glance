"""Google Books as a `ContentSource` — the primary metadata source.

Carries the richest catalog fields (description, categories, ISBN, cover
thumbnail, average rating) but, as established in CLAUDE.md, **no review
text at all**: the volumes API exposes `averageRating` and `ratingsCount`
without the prose behind them. The only passage it contributes to the RAG
corpus is the publisher's description.
"""

from typing import Any

import structlog
from rapidfuzz import fuzz

from app.core.config import Settings
from app.models.book import SourceKind, SourceName
from app.services.http_utils import get_json_with_retry
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult

logger = structlog.get_logger(__name__)

_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"

# Below this title similarity the top hit is treated as a different book.
# Google Books always returns *something* for a free-text query, so without
# a floor a misread cover silently yields confident metadata for the wrong
# title — worse than reporting nothing.
_MIN_TITLE_SIMILARITY = 70.0


class GoogleBooksSource:
    """`ContentSource` over the Google Books volumes API.

    Sends `Settings.google_books_api_key` when configured; without a key
    the endpoint shares an anonymous quota that is routinely exhausted.
    """

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.google_books_timeout_seconds
        self._api_key = settings.google_books_api_key
        self._max_retries = settings.catalog_max_retries
        self._max_chars = settings.source_max_passage_chars

    @property
    def name(self) -> SourceName:
        """See `ContentSource.name`."""
        return SourceName.GOOGLE_BOOKS

    async def fetch(self, title: str, author: str | None = None) -> SourceResult:
        """See `ContentSource.fetch`."""
        if not title.strip():
            return SourceResult.no_match(self.name)

        query = f'intitle:"{title}"'
        if author:
            query += f' inauthor:"{author}"'

        params: dict[str, Any] = {"q": query, "maxResults": 5}
        if self._api_key:
            params["key"] = self._api_key

        fetch = await get_json_with_retry(
            _VOLUMES_URL,
            params,
            timeout=self._timeout,
            max_retries=self._max_retries,
            source=self.name.value,
        )
        if not fetch.available:
            return SourceResult.unavailable(self.name)
        if not isinstance(fetch.payload, dict):
            return SourceResult.no_match(self.name)

        volume = self._best_volume(fetch.payload.get("items", []), title)
        if volume is None:
            logger.info("google_books_no_match", title=title, author=author)
            return SourceResult.no_match(self.name)

        return self._to_result(volume)

    def _best_volume(self, items: list[Any], title: str) -> dict[str, Any] | None:
        """Picks the returned volume whose title best matches the query.

        Args:
            items: The raw `items` array from the volumes response.
            title: The title being looked for.

        Returns:
            The best-matching volume's `volumeInfo`, or `None` if nothing
            cleared `_MIN_TITLE_SIMILARITY`.
        """
        best: dict[str, Any] | None = None
        best_score = _MIN_TITLE_SIMILARITY

        for item in items:
            if not isinstance(item, dict):
                continue
            info = item.get("volumeInfo")
            if not isinstance(info, dict) or not info.get("title"):
                continue

            candidate = str(info["title"])
            if info.get("subtitle"):
                candidate = f"{candidate}: {info['subtitle']}"

            score = fuzz.token_set_ratio(title.casefold(), candidate.casefold())
            if score >= best_score:
                best, best_score = info, score

        return best

    def _to_result(self, info: dict[str, Any]) -> SourceResult:
        """Maps a `volumeInfo` object onto a `SourceResult`.

        Args:
            info: The `volumeInfo` object of the chosen volume.

        Returns:
            The metadata and description passage the volume carries.
        """
        identifiers = {
            str(entry.get("type")): str(entry.get("identifier"))
            for entry in info.get("industryIdentifiers", [])
            if isinstance(entry, dict) and entry.get("identifier")
        }
        images = info.get("imageLinks") or {}
        authors = info.get("authors") or []
        description = info.get("description")

        metadata = BookMetadata(
            title=info.get("title"),
            author=", ".join(str(a) for a in authors) if authors else None,
            description=description,
            categories=[str(c) for c in (info.get("categories") or [])],
            cover_url=images.get("thumbnail") or images.get("smallThumbnail"),
            isbn_13=identifiers.get("ISBN_13"),
            isbn_10=identifiers.get("ISBN_10"),
            average_rating=info.get("averageRating"),
            ratings_count=info.get("ratingsCount"),
        )

        passages: list[SourcePassage] = []
        if description:
            passages.append(
                SourcePassage(
                    kind=SourceKind.DESCRIPTION,
                    content=str(description)[: self._max_chars],
                )
            )

        return SourceResult(
            source=self.name,
            metadata=metadata,
            passages=passages,
            url=info.get("infoLink") or info.get("canonicalVolumeLink"),
            license="Google Books API Terms of Service",
        )

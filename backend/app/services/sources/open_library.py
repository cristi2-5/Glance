"""Open Library as a `ContentSource` — the keyless CC0 fallback.

Contributes subjects (a broader, more granular taxonomy than Google's
`categories`) and sometimes a description Google Books lacks. Everything
it returns is CC0, so it carries no attribution obligation.

Two requests are needed: `search.json` resolves a title+author to a work
key, then `/works/{key}.json` carries the description and subjects that
the search index does not include.
"""

from typing import Any

import structlog
from rapidfuzz import fuzz

from app.core.config import Settings
from app.models.book import SourceKind, SourceName
from app.services.http_utils import get_json_with_retry
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult

logger = structlog.get_logger(__name__)

_SEARCH_URL = "https://openlibrary.org/search.json"
_WORKS_URL = "https://openlibrary.org/works/{key}.json"
_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

_MIN_TITLE_SIMILARITY = 70.0
_MAX_SUBJECTS = 20


class OpenLibrarySource:
    """`ContentSource` over Open Library's search and works endpoints."""

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.open_library_timeout_seconds
        self._max_retries = settings.catalog_max_retries
        self._max_chars = settings.source_max_passage_chars
        self._headers = {"User-Agent": settings.source_user_agent}

    @property
    def name(self) -> SourceName:
        """See `ContentSource.name`."""
        return SourceName.OPEN_LIBRARY

    async def fetch(self, title: str, author: str | None = None) -> SourceResult:
        """See `ContentSource.fetch`."""
        if not title.strip():
            return SourceResult.no_match(self.name)

        params: dict[str, Any] = {
            "title": title,
            "limit": 5,
            "fields": "key,title,author_name,cover_i,first_publish_year,"
            "ratings_average,ratings_count",
        }
        if author:
            params["author"] = author

        search = await get_json_with_retry(
            _SEARCH_URL,
            params,
            timeout=self._timeout,
            max_retries=self._max_retries,
            source=self.name.value,
            headers=self._headers,
        )
        if not search.available:
            return SourceResult.unavailable(self.name)
        if not isinstance(search.payload, dict):
            return SourceResult.no_match(self.name)

        doc = self._best_doc(search.payload.get("docs", []), title)
        if doc is None:
            logger.info("open_library_no_match", title=title, author=author)
            return SourceResult.no_match(self.name)

        work_key = str(doc.get("key", "")).removeprefix("/works/")
        work = await self._fetch_work(work_key) if work_key else None

        return self._to_result(doc, work, work_key)

    async def _fetch_work(self, work_key: str) -> dict[str, Any] | None:
        """Fetches the work record carrying description and subjects.

        A failure here is not fatal — the search hit alone still yields a
        title, author, cover and rating, so the result degrades rather
        than disappearing.

        Args:
            work_key: The work identifier, without the `/works/` prefix.

        Returns:
            The work record, or `None` if it could not be fetched.
        """
        fetch = await get_json_with_retry(
            _WORKS_URL.format(key=work_key),
            None,
            timeout=self._timeout,
            max_retries=self._max_retries,
            source=f"{self.name.value}_work",
            headers=self._headers,
        )
        return fetch.payload if isinstance(fetch.payload, dict) else None

    def _best_doc(self, docs: list[Any], title: str) -> dict[str, Any] | None:
        """Picks the search hit whose title best matches the query.

        Args:
            docs: The raw `docs` array from the search response.
            title: The title being looked for.

        Returns:
            The best-matching document, or `None` if none cleared the
            similarity floor.
        """
        best: dict[str, Any] | None = None
        best_score = _MIN_TITLE_SIMILARITY

        for doc in docs:
            if not isinstance(doc, dict) or not doc.get("title"):
                continue
            score = fuzz.token_set_ratio(title.casefold(), str(doc["title"]).casefold())
            if score >= best_score:
                best, best_score = doc, score

        return best

    def _to_result(
        self, doc: dict[str, Any], work: dict[str, Any] | None, work_key: str
    ) -> SourceResult:
        """Merges a search hit and its work record into a `SourceResult`.

        Args:
            doc: The chosen search document.
            work: The work record, when it could be fetched.
            work_key: The work identifier, used to build the citation URL.

        Returns:
            The metadata and passages Open Library carries for this book.
        """
        work = work or {}
        authors = doc.get("author_name") or []
        cover_id = doc.get("cover_i")

        description = _flatten_description(work.get("description"))
        subjects = [str(s) for s in (work.get("subjects") or [])][:_MAX_SUBJECTS]

        metadata = BookMetadata(
            title=doc.get("title"),
            author=", ".join(str(a) for a in authors) if authors else None,
            description=description,
            categories=subjects,
            cover_url=_COVER_URL.format(cover_id=cover_id) if cover_id else None,
            average_rating=doc.get("ratings_average"),
            ratings_count=doc.get("ratings_count"),
        )

        passages: list[SourcePassage] = []
        if description:
            passages.append(
                SourcePassage(kind=SourceKind.DESCRIPTION, content=description[: self._max_chars])
            )
        if subjects:
            passages.append(
                SourcePassage(
                    kind=SourceKind.SUBJECTS,
                    heading="Subjects",
                    content=", ".join(subjects),
                )
            )

        return SourceResult(
            source=self.name,
            metadata=metadata,
            passages=passages,
            url=f"https://openlibrary.org/works/{work_key}" if work_key else None,
            license="CC0",
        )


def _flatten_description(raw: Any) -> str | None:
    """Normalizes Open Library's two description shapes into plain text.

    The field is either a bare string or a `{"type": ..., "value": ...}`
    typed-text object, depending on the record's age.

    Args:
        raw: The `description` field as it came out of the JSON.

    Returns:
        The description text, or `None` if there wasn't one.
    """
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        value = raw.get("value")
        if isinstance(value, str):
            return value.strip() or None
    return None

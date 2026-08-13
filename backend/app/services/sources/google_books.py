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
from app.services.sources.matching import MIN_TITLE_SIMILARITY, title_similarity

logger = structlog.get_logger(__name__)

_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"


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

        return self._to_result(volume, author)

    def _best_volume(self, items: list[Any], title: str) -> dict[str, Any] | None:
        """Picks the returned volume whose title best matches the query.

        Ties are common and used to be resolved badly. A popular novel has
        several volumes whose titles all match perfectly — the novel, a
        "A Novel" reissue, a literary-criticism study of it — and the old
        `>=` comparison let the *last* of them win, silently discarding
        Google's own relevance ranking. "Seraphina" resolved to a criticism
        volume carrying no description at all, so the book cached with a
        cover and nothing to read.

        Ranking therefore goes: title similarity first, then whether the
        volume actually carries a description, then Google's ordering (a
        strict `>` keeps the earliest of equals).

        Args:
            items: The raw `items` array from the volumes response.
            title: The title being looked for.

        Returns:
            The best-matching volume's `volumeInfo`, or `None` if nothing
            cleared `MIN_TITLE_SIMILARITY`.
        """
        best: dict[str, Any] | None = None
        best_rank = (MIN_TITLE_SIMILARITY, -1)

        for item in items:
            if not isinstance(item, dict):
                continue
            info = item.get("volumeInfo")
            if not isinstance(info, dict) or not info.get("title"):
                continue

            candidate = str(info["title"])
            if info.get("subtitle"):
                candidate = f"{candidate}: {info['subtitle']}"

            rank = (title_similarity(title, candidate), 1 if info.get("description") else 0)
            if rank > best_rank:
                best, best_rank = info, rank

        return best

    def _to_result(self, info: dict[str, Any], queried_author: str | None) -> SourceResult:
        """Maps a `volumeInfo` object onto a `SourceResult`.

        Args:
            info: The `volumeInfo` object of the chosen volume.
            queried_author: The author read off the cover, used to pick the
                real author out of Google's contributor list.

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
            author=_pick_author([str(a) for a in authors], queried_author),
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


# How close a listed contributor must be to the name on the cover to be
# taken as the same person, allowing for "Verne, Jules" and initials.
_AUTHOR_MATCH_SIMILARITY = 80.0

# Cap on contributors kept when the cover gave us no name to match against.
_MAX_AUTHORS = 2


def _pick_author(authors: list[str], queried_author: str | None) -> str | None:
    """Chooses the author to display from Google's contributor list.

    `volumeInfo.authors` is a contributor list, not an author field:
    translators, illustrators and editors are in it, unlabelled and in no
    reliable order. Joining it wholesale produced, for a Romanian edition
    of Jules Verne, the author line

        Jules Verne, Anghel Ghițulescu, Simona Schileru, H. Meyer

    — one author, two translators and the original illustrator.

    The cover is the better authority here, and we already have what it
    said. When the scanned name appears in the list, that entry wins: it is
    the catalog's spelling of the person actually credited on the book, and
    everyone else in the list is by elimination not who the cover named.

    Args:
        authors: The contributor names, as Google returned them.
        queried_author: The author read off the cover, when vision or OCR
            produced one.

    Returns:
        The author to display, or `None` when the volume lists nobody.
    """
    if not authors:
        return None

    if queried_author:
        best = max(authors, key=lambda name: fuzz.token_sort_ratio(name, queried_author))
        if fuzz.token_sort_ratio(best, queried_author) >= _AUTHOR_MATCH_SIMILARITY:
            return best

    # Nothing to match against — a cover whose author line wasn't read, or
    # a genuinely different name. Keep the first few rather than the whole
    # list: co-authorship is real and worth showing, a cast of ten is not.
    return ", ".join(authors[:_MAX_AUTHORS])

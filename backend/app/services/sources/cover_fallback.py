"""Last-resort cover lookups, for books the catalogs matched without an image.

A missing cover is the most visible kind of gap: the client has a title, a
blurb and a rating, and still renders a grey rectangle where the book
should be. It is also the gap most often closable from a source that
already answered — the ISBN is sitting in the Google Books record, the
Wikipedia article is already open.

The chain, in order, stopping at the first hit:

1. **Google Books thumbnail** — not here; it arrives through the normal
   `ContentSource` merge, and this chain only runs when that produced
   nothing.
2. **Open Library Covers API, by ISBN** (`open_library.fetch_cover_by_isbn`).
   CC0. Reaches editions whose *title* Open Library's search index misses,
   which is the usual shape of the problem for Romanian editions. The
   title/author leg of that same API is already covered by
   `OpenLibrarySource.fetch`, whose `cover_i` is merged ahead of this.
3. **The Wikipedia article's lead image** (`wikipedia.fetch_article_image`).
   Used only because this project is private and not distributed — the
   image is usually a non-free cover scan held under fair use. See the
   "Cover images" decision in CLAUDE.md, and `wikipedia_cover_fallback`
   in `Settings` for the switch that turns it off.

Ordering is licence-first, not quality-first: the CC0 image is preferred
even though the Wikipedia one is often the better scan.

Nothing here is allowed to fail a fetch. Every leg returns `None` rather
than raising, and a book with no cover is a perfectly normal result.
"""

from typing import Protocol

import structlog

from app.core.config import Settings
from app.services.sources.open_library import fetch_cover_by_isbn
from app.services.sources.wikipedia import fetch_article_image

logger = structlog.get_logger(__name__)


class CoverFallback(Protocol):
    """Resolves a cover image for a book the content sources gave none for."""

    async def find_cover(
        self,
        isbn_13: str | None,
        isbn_10: str | None,
        wikipedia_article: str | None,
    ) -> str | None:
        """Finds a cover image URL from whatever identifiers are available.

        Args:
            isbn_13: The book's ISBN-13, when a catalog reported one.
            isbn_10: The book's ISBN-10, likewise.
            wikipedia_article: The resolved article title, when the
                Wikipedia source matched this book.

        Returns:
            A cover URL that has been confirmed to exist, or `None`.
            Implementations must not raise.
        """
        ...


class OfficialCoverFallback:
    """The production chain: Open Library by ISBN, then Wikipedia's lead image."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def find_cover(
        self,
        isbn_13: str | None,
        isbn_10: str | None,
        wikipedia_article: str | None,
    ) -> str | None:
        """See `CoverFallback.find_cover`.

        The legs run in sequence, not concurrently: each one is a request
        made on the user's behalf, and the common case is that the first
        succeeds. Firing all of them to throw most of the answers away
        would cost the sources more and gain nothing but a few hundred
        milliseconds on the rarest path.
        """
        for isbn in (isbn_13, isbn_10):
            if isbn:
                cover_url = await fetch_cover_by_isbn(isbn, self._settings)
                if cover_url:
                    return cover_url

        if wikipedia_article and self._settings.wikipedia_cover_fallback:
            return await fetch_article_image(wikipedia_article, self._settings)

        return None

"""Wikipedia as a `ContentSource` — the project's source of critical opinion.

This is the source that replaces the review scraping the project
deliberately does not do. Goodreads, LibraryThing and Amazon all
`Disallow` their review paths in robots.txt, so the *Reception* /
*Critical reception* section of a book's Wikipedia article is the
critical-opinion corpus instead: professional criticism, quoted and
cited, published under CC BY-SA. See the "Content sources" decision in
CLAUDE.md.

Its limitation is coverage, not licensing — only notable books have
articles, and Romanian editions rarely do. A book with no article is a
normal, non-fatal outcome: the fetcher keeps the Google Books metadata
and simply has less to retrieve over in Module 5.

Two requests: a search to resolve the article title, then one `extracts`
call for the whole article as plain text, which is cheaper than fetching
sections individually and lets us split headings locally.
"""

import re

import structlog
from rapidfuzz import fuzz

from app.core.config import Settings
from app.models.book import SourceKind, SourceName
from app.services.http_utils import get_json_with_retry
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult

logger = structlog.get_logger(__name__)

_API_URL = "https://{lang}.wikipedia.org/w/api.php"
_ARTICLE_URL = "https://{lang}.wikipedia.org/wiki/{title}"

_MIN_TITLE_SIMILARITY = 65.0

# `explaintext` renders headings as "== Section ==" / "=== Subsection ===".
_HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1$", re.MULTILINE)

# Keywords whose presence in a heading marks the section, matched
# **whole-word**. Wikipedia has no fixed vocabulary here, so these are the
# observed spellings rather than a closed set — anything unmatched is
# simply not ingested.
#
# Whole-word matching is not fussiness: on substring matching "Publication
# history" classifies as plot, because "story" is a substring of "history".
_RECEPTION_WORDS = frozenset(
    {
        "reception",
        "response",
        "responses",
        "review",
        "reviews",
        "critic",
        "critics",
        "critical",
        "criticism",
        "acclaim",
        "award",
        "awards",
        "legacy",
    }
)
_PLOT_WORDS = frozenset(
    {
        "plot",
        "synopsis",
        "summary",
        "story",
        "storyline",
        "premise",
        "overview",
    }
)

# Sections that are never prose worth retrieving.
_SKIP_HEADINGS = (
    "references",
    "external links",
    "see also",
    "further reading",
    "notes",
    "bibliography",
    "sources",
    "citations",
)


class WikipediaSource:
    """`ContentSource` over the Wikipedia action API.

    Sends the descriptive `User-Agent` Wikipedia's API etiquette requires;
    anonymous or generic agents are throttled or refused outright.
    """

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.wikipedia_timeout_seconds
        self._max_retries = settings.catalog_max_retries
        self._max_chars = settings.source_max_passage_chars
        self._lang = settings.wikipedia_language
        self._headers = {"User-Agent": settings.source_user_agent}
        self._api_url = _API_URL.format(lang=self._lang)

    @property
    def name(self) -> SourceName:
        """See `ContentSource.name`."""
        return SourceName.WIKIPEDIA

    async def fetch(self, title: str, author: str | None = None) -> SourceResult:
        """See `ContentSource.fetch`."""
        if not title.strip():
            return SourceResult.no_match(self.name)

        page_title, available = await self._find_article(title, author)
        if not available:
            return SourceResult.unavailable(self.name)
        if page_title is None:
            return SourceResult.no_match(self.name)

        extract = await self._fetch_extract(page_title)
        if extract is None:
            return SourceResult.no_match(self.name)

        passages = self._split_passages(extract)
        if not passages:
            logger.info("wikipedia_article_had_no_usable_sections", page=page_title)
            return SourceResult.no_match(self.name)

        return SourceResult(
            source=self.name,
            metadata=BookMetadata(),
            passages=passages,
            url=_ARTICLE_URL.format(lang=self._lang, title=page_title.replace(" ", "_")),
            license="CC BY-SA 4.0",
        )

    async def _find_article(self, title: str, author: str | None) -> tuple[str | None, bool]:
        """Resolves a book to the title of its Wikipedia article.

        Args:
            title: The book title.
            author: The author, added to the query to disambiguate.

        Returns:
            A `(article_title, available)` pair. `(None, True)` means
            Wikipedia answered and has no article about this book — the
            common case for untranslated or non-notable editions.
            `(None, False)` means the API could not be reached, which must
            not be cached as "no article".
        """
        query = f"{title} {author} novel" if author else f"{title} book"
        fetch = await get_json_with_retry(
            self._api_url,
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 5,
                "format": "json",
            },
            timeout=self._timeout,
            max_retries=self._max_retries,
            source=self.name.value,
            headers=self._headers,
        )
        if not fetch.available:
            return None, False
        if not isinstance(fetch.payload, dict):
            return None, True

        results = fetch.payload.get("query", {}).get("search", [])
        best: str | None = None
        best_score = _MIN_TITLE_SIMILARITY

        for entry in results:
            if not isinstance(entry, dict) or not entry.get("title"):
                continue
            candidate = str(entry["title"])
            # Strip the disambiguator — "Dune (novel)" should match "Dune".
            bare = re.sub(r"\s*\([^)]*\)\s*$", "", candidate)
            score = fuzz.token_set_ratio(title.casefold(), bare.casefold())
            if score >= best_score:
                best, best_score = candidate, score

        if best is None:
            logger.info("wikipedia_no_match", title=title, author=author)
        return best, True

    async def _fetch_extract(self, page_title: str) -> str | None:
        """Fetches an article's full plain-text extract.

        Args:
            page_title: The exact article title.

        Returns:
            The article as plain text, or `None` if it could not be read.
        """
        fetch = await get_json_with_retry(
            self._api_url,
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "redirects": 1,
                "titles": page_title,
                "format": "json",
            },
            timeout=self._timeout,
            max_retries=self._max_retries,
            source=f"{self.name.value}_extract",
            headers=self._headers,
        )
        if not isinstance(fetch.payload, dict):
            return None

        pages = fetch.payload.get("query", {}).get("pages", {})
        for page in pages.values():
            if isinstance(page, dict) and page.get("extract"):
                return str(page["extract"])
        return None

    def _split_passages(self, extract: str) -> list[SourcePassage]:
        """Splits an article extract into the passages worth retrieving.

        Keeps only reception and plot sections: the rest of an article
        (publication history, references, external links) is metadata
        noise that would dilute retrieval in Module 5.

        Args:
            extract: The article's plain-text extract.

        Returns:
            The reception and plot passages found, in article order.
        """
        passages: list[SourcePassage] = []
        matches = list(_HEADING_RE.finditer(extract))

        for index, match in enumerate(matches):
            heading = match.group(2).strip()
            normalized = heading.casefold()
            if normalized in _SKIP_HEADINGS:
                continue

            kind = _classify_heading(normalized)
            if kind is None:
                continue

            end = matches[index + 1].start() if index + 1 < len(matches) else len(extract)
            body = extract[match.end() : end].strip()
            if not body:
                continue

            passages.append(
                SourcePassage(kind=kind, heading=heading, content=body[: self._max_chars])
            )

        return passages


def _classify_heading(normalized: str) -> SourceKind | None:
    """Maps a section heading onto the passage kind it holds.

    Reception is checked first, so a combined heading such as "Reception
    and legacy" or "Critical reception" lands in the critical-opinion
    corpus rather than being split.

    Args:
        normalized: The heading, case-folded.

    Returns:
        The matching `SourceKind`, or `None` for a section we don't ingest.
    """
    words = set(re.findall(r"[a-z]+", normalized))
    if words & _RECEPTION_WORDS:
        return SourceKind.RECEPTION
    if words & _PLOT_WORDS:
        return SourceKind.PLOT
    return None

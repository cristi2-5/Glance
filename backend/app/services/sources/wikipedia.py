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

`fetch_article_image` is a third, separate request, made only as the last
leg of the cover-image fallback (see `cover_fallback.py`) — and only for
a private, non-distributed build, because a book article's lead image is
usually a non-free cover scan. See the "Cover images" decision in
CLAUDE.md.
"""

import enum
import re
import unicodedata
from urllib.parse import quote

import structlog
from rapidfuzz import fuzz

from app.core.config import Settings
from app.models.book import SourceKind, SourceName
from app.services.http_utils import get_json_with_retry
from app.services.sources.base import BookMetadata, SourcePassage, SourceResult
from app.services.sources.matching import title_similarity

logger = structlog.get_logger(__name__)

_API_URL = "https://{lang}.wikipedia.org/w/api.php"
_ARTICLE_URL = "https://{lang}.wikipedia.org/wiki/{title}"
_SUMMARY_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"

# Where an image is hosted is what tells us how it is licensed, and it is
# readable straight off the URL, with no extra API call:
#
#   .../wikipedia/commons/...  → Wikimedia Commons, freely licensed
#   .../wikipedia/en/...       → uploaded locally to one wiki, which is how
#                                non-free material is held under a
#                                fair-use exemption
#
# Book articles overwhelmingly fall in the second group: the lead image is
# the publisher's cover scan, kept as fair use. Both are used here, and the
# difference is logged rather than enforced — see `fetch_article_image`.
_COMMONS_MARKER = "/wikipedia/commons/"

# Deliberately below `matching.MIN_TITLE_SIMILARITY`: Wikipedia article
# titles are prose, not catalog records, so they drift further from the
# cover ("Baltagul" → "The Hatchet") than a catalog entry does. The cost of
# a loose match here is lower too — Wikipedia contributes passages, which
# are attributed and citable, not the cover and blurb shown as fact.
_MIN_TITLE_SIMILARITY = 65.0

# A trailing "(...)" on an article title — Wikipedia's disambiguator.
_DISAMBIGUATOR = re.compile(r"\s*\(([^)]*)\)\s*$")

# Disambiguators that mark an article as being about a *book*. Both
# languages, because the search now spans both.
#
# These matter more than they look. Stripping the disambiguator before
# scoring — which is needed so "Dune (novel)" matches "Dune" — also makes
# "Moarte pe Nil (film din 2022)" and "Câmp (river)" score 100 against the
# book being scanned. Ingesting either would fill a book's RAG corpus with
# an article about a film or a river, and the summary built from it would
# be fluent, cited, and about the wrong subject entirely.
_BOOK_WORDS = frozenset(
    {
        "roman",
        "novel",
        "novella",
        "nuvela",
        "carte",
        "book",
        "povestire",
        "poem",
        "fictiune",
        "fiction",
        "literatura",
        "literature",
        "trilogie",
        "trilogy",
        "serie",
        "series",
    }
)

# How close a disambiguator must be to the author's name to be read as
# one — "Michael Strogoff (Jules Verne)" is a book article.
_AUTHOR_DISAMBIGUATOR_SIMILARITY = 80.0

# `explaintext` renders headings as "== Section ==" / "=== Subsection ===".
_HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1$", re.MULTILINE)

# Keywords whose presence in a heading marks the section, matched
# **whole-word**. Wikipedia has no fixed vocabulary here, so these are the
# observed spellings rather than a closed set — anything unmatched is
# simply not ingested.
#
# Whole-word matching is not fussiness: on substring matching "Publication
# history" classifies as plot, because "story" is a substring of "history".
# Romanian spellings are listed alongside the English ones because the
# search now spans ro.wikipedia, and a Romanian article's headings are
# entirely different words. Without these, `ro` articles resolve correctly
# and then yield **zero** passages — which is how "Baltagul (roman)", a
# 32,000-character article with an "Aprecieri critice" section, produced
# no corpus at all.
#
# Words are matched after accent folding (see `_classify_heading`), so the
# entries here are unaccented: "Acțiune" tokenizes to "ac" + "iune" on a
# raw `[a-z]+` split, which matches nothing.
_RECEPTION_WORDS = frozenset(
    {
        # English
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
        # Romanian — "Aprecieri critice" is the standard heading for a
        # Romanian literary article's critical-reception section.
        "aprecieri",
        "critica",
        "critice",
        "critici",
        "receptare",
        "recenzii",
        "primire",
        "premii",
        "ecouri",
        "mostenire",
    }
)
_THEME_WORDS = frozenset(
    {
        # English
        "themes",
        "theme",
        "style",
        "symbolism",
        "analysis",
        "interpretation",
        # Romanian
        "teme",
        "tema",
        "tematica",
        "stil",
        "simboluri",
        "semnificatii",
        "interpretare",
        "interpretari",
        "analiza",
    }
)
_PLOT_WORDS = frozenset(
    {
        # English
        "plot",
        "synopsis",
        "summary",
        "story",
        "storyline",
        "premise",
        "overview",
        # Romanian
        "rezumat",
        "sinopsis",
        "subiect",
        "intriga",
        "poveste",
        "actiune",
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
        self._languages = list(settings.wikipedia_languages) or ["en"]
        self._headers = {"User-Agent": settings.source_user_agent}

    @property
    def name(self) -> SourceName:
        """See `ContentSource.name`."""
        return SourceName.WIKIPEDIA

    async def fetch(self, title: str, author: str | None = None) -> SourceResult:
        """See `ContentSource.fetch`.

        Searches each configured language edition in order and stops at the
        first confident match, rather than searching them all and ranking
        across editions. Scores are not comparable between languages — a
        Romanian article about a different book scores exactly as well as
        an English article about the right one — so "the first edition that
        recognises this title" is a better rule than "the highest number".
        It also means an English-language book pays a single request.
        """
        if not title.strip():
            return SourceResult.no_match(self.name)

        reachable = False
        for language in self._languages:
            page_title, available = await self._find_article(language, title, author)
            reachable = reachable or available
            if page_title is None:
                continue

            result = await self._build_result(language, page_title)
            if result is not None:
                return result

        if not reachable:
            # Every edition was unreachable — an outage, not an answer, and
            # it must not be cached as "this book has no article".
            return SourceResult.unavailable(self.name)

        logger.info("wikipedia_no_article", title=title, author=author, languages=self._languages)
        return SourceResult.no_match(self.name)

    async def _build_result(self, language: str, page_title: str) -> SourceResult | None:
        """Fetches an article and turns it into a result, if it has content.

        Args:
            language: The Wikipedia edition the article lives in.
            page_title: The exact article title.

        Returns:
            The result, or `None` when the article could not be read or
            held no section worth retrieving — in which case the caller
            carries on to the next language.
        """
        extract = await self._fetch_extract(language, page_title)
        if extract is None:
            return None

        passages = self._split_passages(extract)
        if not passages:
            logger.info(
                "wikipedia_article_had_no_usable_sections", page=page_title, language=language
            )
            return None

        logger.info(
            "wikipedia_matched",
            page=page_title,
            language=language,
            passages=len(passages),
            kinds=sorted({passage.kind.value for passage in passages}),
        )
        return SourceResult(
            source=self.name,
            metadata=BookMetadata(),
            passages=passages,
            url=_ARTICLE_URL.format(lang=language, title=page_title.replace(" ", "_")),
            license="CC BY-SA 4.0",
            # Carried so the cover fallback can go straight to this article
            # instead of paying for `_find_article` a second time. The
            # language prefix matters: the article may not be on the first
            # configured edition, and asking the wrong one for its lead
            # image gets a 404 or, worse, another book's cover.
            record_ref=f"{language}:{page_title}",
        )

    async def _find_article(
        self, language: str, title: str, author: str | None
    ) -> tuple[str | None, bool]:
        """Resolves a book to the title of its article in one language edition.

        Args:
            language: The Wikipedia edition to search.
            title: The book title.
            author: The author, added to the query to disambiguate.

        Returns:
            An `(article_title, available)` pair. `(None, True)` means this
            edition answered and has no article about this book.
            `(None, False)` means it could not be reached, which must not
            be cached as "no article".
        """
        # Just the title and author. The query used to append the English
        # word "novel" (or "book"), which is a disaster outside en:
        # Wikipedia's search ANDs its terms, so an English noun no Romanian
        # article contains filters out every result. "Căpitan la
        # cincisprezece ani Jules Verne novel" returned nothing on
        # ro.wikipedia; without the "novel", the exact article is the first
        # hit. Disambiguation is handled by `_classify_article` instead,
        # which does it in both languages and rejects films outright.
        query = f"{title} {author}".strip() if author else title
        fetch = await get_json_with_retry(
            _API_URL.format(lang=language),
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                # Wider than the matching needs, because entries are now
                # discarded rather than merely outranked: a film and a
                # disambiguation page can occupy the top slots while the
                # book article sits below them.
                "srlimit": 8,
                "format": "json",
            },
            timeout=self._timeout,
            max_retries=self._max_retries,
            source=f"{self.name.value}_{language}",
            headers=self._headers,
        )
        if not fetch.available:
            return None, False
        if not isinstance(fetch.payload, dict):
            return None, True

        results = fetch.payload.get("query", {}).get("search", [])
        best: str | None = None
        best_rank = (-1, float("-inf"))

        for entry in results:
            if not isinstance(entry, dict) or not entry.get("title"):
                continue
            candidate = str(entry["title"])

            match = _DISAMBIGUATOR.search(candidate)
            kind = _classify_article(match.group(1) if match else "", author)
            if kind is _ArticleKind.OTHER:
                logger.debug("wikipedia_candidate_rejected", candidate=candidate, language=language)
                continue

            # Strip the disambiguator so "Dune (novel)" matches "Dune".
            score = title_similarity(title, _DISAMBIGUATOR.sub("", candidate))
            if score < _MIN_TITLE_SIMILARITY:
                continue

            # A confirmed book article outranks an unmarked one even at a
            # lower similarity: "Baltagul (roman)" is the novel, whereas a
            # bare title of the same name could be anything.
            # Strict `>`: on equal ranks keep the earliest, which is
            # Wikipedia's own relevance ordering.
            rank = (1 if kind is _ArticleKind.BOOK else 0, score)
            if rank > best_rank:
                best, best_rank = candidate, rank

        return best, True

    async def _fetch_extract(self, language: str, page_title: str) -> str | None:
        """Fetches an article's full plain-text extract.

        Args:
            language: The Wikipedia edition the article lives in.
            page_title: The exact article title.

        Returns:
            The article as plain text, or `None` if it could not be read.
        """
        fetch = await get_json_with_retry(
            _API_URL.format(lang=language),
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
            source=f"{self.name.value}_extract_{language}",
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


class _ArticleKind(enum.Enum):
    """What a candidate article's disambiguator says it is about."""

    #: Explicitly a book — "(novel)", "(roman)", "(Jules Verne)".
    BOOK = "book"
    #: No disambiguator at all, so the title stands on its own.
    UNKNOWN = "unknown"
    #: Explicitly something else — a film, an album, a river.
    OTHER = "other"


def _classify_article(disambiguator: str, author: str | None) -> _ArticleKind:
    """Decides whether a disambiguated article can be about this book.

    A **whitelist**, not a blacklist, and deliberately so. Blacklisting the
    obvious wrong kinds (film, album, song) still let "Câmp (river)" match
    a book called *Câmpul* at a similarity of 80 — the space of things that
    are not books is not enumerable, and every miss attaches a foreign
    article to a book's RAG corpus.

    The asymmetry is the same one `matching.py` reasons about: a missing
    article costs a few passages and is already a normal outcome for the
    editions this app scans, while a wrong article produces a fluent,
    fully-cited summary of the wrong subject. So an article that announces
    itself as something specific must announce itself as a book.

    Args:
        disambiguator: The text inside the trailing parentheses, or `""`
            when the title has none.
        author: The author being searched for, since an author's name is
            itself a book disambiguator.

    Returns:
        The classification. `UNKNOWN` — an undisambiguated title — is
        accepted, but ranks below a confirmed book.
    """
    if not disambiguator.strip():
        return _ArticleKind.UNKNOWN

    folded = _fold_for_matching(disambiguator)
    if set(re.findall(r"[a-z]+", folded)) & _BOOK_WORDS:
        return _ArticleKind.BOOK

    if author and fuzz.token_sort_ratio(folded, _fold_for_matching(author)) >= (
        _AUTHOR_DISAMBIGUATOR_SIMILARITY
    ):
        return _ArticleKind.BOOK

    return _ArticleKind.OTHER


def _fold_for_matching(value: str) -> str:
    """Case-folds and strips accents, so "ficțiune" matches "fictiune".

    Args:
        value: The string to fold.

    Returns:
        The accent-free, case-folded form.
    """
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def split_article_ref(article_ref: str, default_language: str) -> tuple[str, str]:
    """Splits a `"lang:Article Title"` reference into its parts.

    Args:
        article_ref: The reference carried in `SourceResult.record_ref`.
        default_language: Language to assume when the reference carries no
            prefix.

    Returns:
        A `(language, article_title)` pair.
    """
    language, separator, page_title = article_ref.partition(":")
    # A bare title is still valid, and a title can legitimately contain a
    # colon ("Dune: Part Two"), so only a short alphabetic prefix counts as
    # a language code.
    if separator and language.isalpha() and len(language) <= 3:
        return language, page_title
    return default_language, article_ref


async def fetch_article_image(article_ref: str, settings: Settings) -> str | None:
    """Reads an article's lead image from the REST summary endpoint.

    The last leg of the cover-image fallback, and the only one whose
    licensing is not clean. A book article's lead image is usually the
    publisher's cover scan, uploaded locally under a fair-use exemption
    rather than to Commons, and fair use does not travel to a third-party
    app. This project is private and not distributed, so it is used
    anyway — the decision is recorded in CLAUDE.md, along with the fact
    that publishing the app would require revisiting it. The Commons /
    local split is logged on every hit so that revisit has evidence rather
    than guesswork.

    `originalimage` is preferred over `thumbnail`: both point at the same
    file, and a cover rendered on a phone screen is better served by the
    full-size version than by Wikipedia's 320 px preview.

    Args:
        article_ref: The resolved article, as `WikipediaSource` reported it
            in `SourceResult.record_ref` — `"lang:Article Title"`. A bare
            title is accepted too and assumed to be in the first configured
            language.
        settings: Application settings, for the languages, timeout, retry
            budget and the User-Agent the API's etiquette requires.

    Returns:
        The image URL, or `None` when the article has no lead image or the
        endpoint could not be read. Never raises.
    """
    default_language = (settings.wikipedia_languages or ["en"])[0]
    language, article_title = split_article_ref(article_ref, default_language)
    url = _SUMMARY_URL.format(
        lang=language,
        # The REST API takes the title as a single percent-encoded path
        # segment; a slash in a title ("And/Or") would otherwise split it.
        title=quote(article_title.replace(" ", "_"), safe=""),
    )
    fetch = await get_json_with_retry(
        url,
        None,
        timeout=settings.wikipedia_timeout_seconds,
        max_retries=settings.catalog_max_retries,
        source="wikipedia_summary",
        headers={"User-Agent": settings.source_user_agent},
    )
    if not isinstance(fetch.payload, dict):
        return None

    for key in ("originalimage", "thumbnail"):
        image = fetch.payload.get(key)
        if isinstance(image, dict) and isinstance(image.get("source"), str):
            image_url = str(image["source"])
            on_commons = _COMMONS_MARKER in image_url
            logger.info(
                "cover_fallback_hit",
                provider="wikipedia",
                article=article_title,
                field=key,
                language=language,
                on_commons=on_commons,
                licensing=(
                    "Wikimedia Commons (freely licensed)"
                    if on_commons
                    else "local wiki upload (likely non-free / fair use)"
                ),
            )
            return image_url

    logger.info("wikipedia_article_has_no_lead_image", article=article_title)
    return None


def _classify_heading(normalized: str) -> SourceKind | None:
    """Maps a section heading onto the passage kind it holds.

    Reception is checked first, so a combined heading such as "Reception
    and legacy" or "Critical reception" lands in the critical-opinion
    corpus rather than being split. Themes come before plot for the same
    reason: "Teme abordate în cadrul romanului" is analysis, not a summary
    of events.

    Sections that match nothing are simply not ingested, which is what
    keeps chapter lists, character lists, translation tables, publication
    history and adaptation lists out of the corpus without needing to be
    enumerated.

    Args:
        normalized: The heading, case-folded.

    Returns:
        The matching `SourceKind`, or `None` for a section we don't ingest.
    """
    words = set(re.findall(r"[a-z]+", _fold_for_matching(normalized)))
    if words & _RECEPTION_WORDS:
        return SourceKind.RECEPTION
    if words & _THEME_WORDS:
        return SourceKind.THEMES
    if words & _PLOT_WORDS:
        return SourceKind.PLOT
    return None

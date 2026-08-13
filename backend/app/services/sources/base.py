"""The `ContentSource` protocol and the value objects every source returns.

Each official source (Google Books, Open Library, Wikipedia) implements
`ContentSource`, so `BookDataFetcher` can orchestrate them uniformly and
tests can substitute fakes without touching the network.

Availability is modelled the same way as in `title_lookup.py`, and for
the same reason: "this source has nothing about the book" and "this
source could not be reached" are different facts. The first is worth
caching; the second must not be, or a transient 503 would poison the
cache with an empty entry for as long as the TTL lasts.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.models.book import SourceKind, SourceName


@dataclass(frozen=True)
class SourcePassage:
    """One passage of prose about a book, before it is persisted.

    Attributes:
        kind: What the passage is (description, subjects, plot, reception).
        content: The text itself.
        heading: The section heading it came from, when applicable.
    """

    kind: SourceKind
    content: str
    heading: str | None = None


@dataclass(frozen=True)
class BookMetadata:
    """Catalog facts about a book, as one source reports them.

    Every field is optional: sources disagree about which they carry, and
    `BookDataFetcher` merges them by taking the first non-`None` value in
    source-priority order.
    """

    title: str | None = None
    author: str | None = None
    description: str | None = None
    categories: list[str] = field(default_factory=list)
    cover_url: str | None = None
    isbn_13: str | None = None
    isbn_10: str | None = None
    average_rating: float | None = None
    ratings_count: int | None = None


@dataclass(frozen=True)
class SourceResult:
    """Everything one source could say about a book.

    Attributes:
        source: Which source produced this.
        metadata: The catalog fields it carries.
        passages: The prose it carries, for the RAG corpus.
        url: The canonical page for the book on this source, for citation.
        license: The licence the passages are published under.
        record_ref: The source's own identifier for the record this result
            came from, so a follow-up request can skip re-resolving it.
            Only Wikipedia sets it — with the article title, which the
            cover-image fallback needs and which cost a search request to
            find. The catalogs have no second endpoint to reach for.
        matched: `False` when the source was reached but has no such book.
        available: `False` when the source could not be reached at all
            (timeout, 5xx, quota). Callers must not cache this outcome.
    """

    source: SourceName
    metadata: BookMetadata = field(default_factory=BookMetadata)
    passages: list[SourcePassage] = field(default_factory=list)
    url: str | None = None
    license: str | None = None
    record_ref: str | None = None
    matched: bool = True
    available: bool = True

    @classmethod
    def unavailable(cls, source: SourceName) -> "SourceResult":
        """Builds the result representing an unreachable source.

        Args:
            source: The source that could not be reached.

        Returns:
            A `SourceResult` with `available=False` and no content.
        """
        return cls(source=source, matched=False, available=False)

    @classmethod
    def no_match(cls, source: SourceName) -> "SourceResult":
        """Builds the result representing a reachable source with no such book.

        Args:
            source: The source that answered, but had nothing.

        Returns:
            A `SourceResult` with `matched=False` but `available=True`.
        """
        return cls(source=source, matched=False, available=True)


class ContentSource(Protocol):
    """An official source of book metadata and prose.

    Implementations must never raise for an expected remote failure —
    a timeout, a 5xx, a quota refusal and a missing book are all reported
    through `SourceResult`, so one flaky source cannot fail a whole scan.
    """

    @property
    def name(self) -> SourceName:
        """The source's identifier, stored on every `TextSource` row it produces."""
        ...

    async def fetch(self, title: str, author: str | None = None) -> SourceResult:
        """Fetches everything this source knows about a book.

        Args:
            title: The book title, as recognized from the cover.
            author: The author, when the vision step identified one.

        Returns:
            The metadata and passages found, plus whether the source was
            reachable and whether it matched the book at all.
        """
        ...

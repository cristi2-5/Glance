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


@dataclass(frozen=True)
class DiscoveryQuery:
    """A request for books *like* something, rather than for one book.

    The two catalogs spell this differently — Google Books wants
    `subject:"..."` / `inauthor:"..."` inside its `q` string, Open Library
    wants `subject=` / `author=` parameters — so the intent is carried
    here and each source translates it. Exactly one field is set per
    query: "science fiction by Frank Herbert" would return the books the
    reader already has, not new ones.

    Attributes:
        subject: A genre or subject label to find books in.
        author: An author to find other books by.
    """

    subject: str | None = None
    author: str | None = None

    def label(self) -> str:
        """Returns a short description of this query, for logging."""
        if self.subject:
            return f"subject:{self.subject}"
        if self.author:
            return f"author:{self.author}"
        return "empty"


@dataclass(frozen=True)
class DiscoveryResult:
    """The books one source returned for one `DiscoveryQuery`.

    Candidates are plain `BookMetadata`: a discovered book carries exactly
    the catalog fields a looked-up one does, and reusing the type is what
    lets `Book` rows be built from either without a second mapping that
    could drift.

    Attributes:
        source: Which source produced these.
        candidates: The books found, in the source's own relevance order.
        available: `False` when the source could not be reached. As
            everywhere else in this package, that is not the same fact as
            "found nothing", and the caller must not record it as a
            completed discovery run.
    """

    source: SourceName
    candidates: list[BookMetadata] = field(default_factory=list)
    available: bool = True

    @classmethod
    def unavailable(cls, source: SourceName) -> "DiscoveryResult":
        """Builds the result representing an unreachable source.

        Args:
            source: The source that could not be reached.

        Returns:
            A `DiscoveryResult` with `available=False` and no candidates.
        """
        return cls(source=source, candidates=[], available=False)


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


class CandidateSource(Protocol):
    """An official source that can be asked for books *like* a description.

    Deliberately a separate protocol from `ContentSource`, even though both
    catalogs implement it on the same class. The two are different
    questions: `fetch` resolves one known title and merges everything about
    it, while `discover` asks an open question and expects many partial
    answers back. Folding them together would give `fetch` a plural return
    type it has no use for, and would let a discovery result reach the
    Module 4 merge, where "the first non-`None` value wins" assumes every
    result describes the *same book*.

    Implementations must never raise for a remote failure — see
    `DiscoveryResult.unavailable`.
    """

    @property
    def name(self) -> SourceName:
        """The source's identifier, used in logs and for dedup bookkeeping."""
        ...

    async def discover(self, query: DiscoveryQuery, limit: int) -> DiscoveryResult:
        """Finds books matching a subject or an author.

        Args:
            query: What to look for. Exactly one field is set.
            limit: Maximum candidates to return.

        Returns:
            The candidates found, plus whether the source was reachable.
        """
        ...

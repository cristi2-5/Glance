"""Official content sources for book metadata and prose.

Only the sources listed in CLAUDE.md's "Content sources" table live here:
Google Books, Open Library and Wikipedia, each behind the `ContentSource`
protocol. No scraping — every mainstream review site disallows its review
paths in robots.txt, so Wikipedia's *Reception* sections stand in as the
critical-opinion corpus.
"""

from app.services.sources.base import (
    BookMetadata,
    ContentSource,
    SourcePassage,
    SourceResult,
)
from app.services.sources.google_books import GoogleBooksSource
from app.services.sources.open_library import OpenLibrarySource
from app.services.sources.wikipedia import WikipediaSource

__all__ = [
    "BookMetadata",
    "ContentSource",
    "GoogleBooksSource",
    "OpenLibrarySource",
    "SourcePassage",
    "SourceResult",
    "WikipediaSource",
]

"""Pydantic schemas for books and their cached text sources."""

from pydantic import BaseModel, ConfigDict


class TextSourceRead(BaseModel):
    """One cached passage about a book, as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    source: str
    kind: str
    heading: str | None = None
    content: str
    url: str | None = None
    license: str | None = None


class BookRead(BaseModel):
    """A book's cached metadata, as returned to the client.

    `metadata_found=False` is the "metadata not found" state: no catalog
    matched the scanned cover, so `title` and `author` are the vision
    step's reading and every other field is empty. The client shows this
    rather than an error — see `data_fetcher.py`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: str | None = None
    description: str | None = None
    categories: list[str] = []
    cover_url: str | None = None
    isbn_13: str | None = None
    isbn_10: str | None = None
    average_rating: float | None = None
    ratings_count: int | None = None
    metadata_found: bool

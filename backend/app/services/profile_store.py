"""One vector per book, in the `book_profiles` ChromaDB collection.

**This module queries across books on purpose, and that is exactly what
`vector_store.py` forbids.** The two are not in tension; they answer
different questions and are kept apart so neither has to bend:

| | `book_chunks` (`vector_store.py`) | `book_profiles` (here) |
|---|---|---|
| unit | one passage of one book | one whole book |
| question | "what does *this book's* corpus say about X?" | "which books are like this?" |
| `book_id` | **mandatory filter**, enforced three ways | the answer, never a constraint |
| wrong answer | a cited summary of the wrong book | a worse suggestion |

Module 5's filter is a correctness invariant because a passage from
*Foundation* inside a summary of *Dune* is fluent, plausible, carries a
real citation, and is undetectable downstream. Nothing of the sort applies
here: a recommendation that surfaces the wrong neighbour is visibly a bad
recommendation, and the reader is the check. So rather than weakening that
filter, flagging around it, or granting it an exception, recommendations
get their own collection with its own rule. Two collections, two rules,
neither relaxed.

The practical consequence to remember: **never store a chunk here and
never store a profile there.** A profile document is built from the
catalog record (title, author, categories, blurb) and is not citable
material; the chunk ids Module 5 hands the client would not resolve to it.
The id prefix (`bp:`) makes a stray row obvious at a glance.

Like the chunk store, Chroma's client is synchronous, so every call goes
through `asyncio.to_thread`.
"""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import chromadb
import structlog
from chromadb.api.models.Collection import Collection

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

#: Separate from `vector_store.COLLECTION_NAME` — see the module docstring.
COLLECTION_NAME = "book_profiles"

#: Prefix on every id in this collection, so a row that wandered in from
#: the chunk store (or the reverse) is recognisable without a lookup.
_ID_PREFIX = "bp"


@dataclass(frozen=True)
class BookProfile:
    """One book's whole-record document, ready to embed.

    Attributes:
        book_id: The `Book` row this describes.
        document: The text the vector is built from. Must be produced by
            the *same* builder for every book — a candidate characterised
            by title and subjects and a library book characterised by a
            full blurb are not comparable if they were assembled
            differently. See `recommendation_service.build_profile_document`.
    """

    book_id: int
    document: str

    @property
    def id(self) -> str:
        """The Chroma id for this profile."""
        return f"{_ID_PREFIX}:{self.book_id}"


@dataclass(frozen=True)
class ScoredBook:
    """One neighbour returned by a profile similarity search.

    Attributes:
        book_id: The book found.
        score: Cosine **similarity**, clamped to `[0, 1]`. Chroma reports
            distance; this is converted here so callers never have to
            remember which direction is better — a score is a score, and a
            reversed comparison would silently recommend the least similar
            books with no error anywhere.
        vector: The book's stored embedding, returned alongside so the
            explanation ("because you liked X") can be computed without a
            second round trip per recommendation.
    """

    book_id: int
    score: float
    vector: list[float]


class ProfileStore(Protocol):
    """Abstraction over the whole-book vector store, so it can be faked."""

    async def upsert(self, profiles: list[BookProfile], embeddings: list[list[float]]) -> None:
        """Writes book profiles and their vectors, replacing any with the same ids."""
        ...

    async def vectors_for(self, book_ids: list[int]) -> dict[int, list[float]]:
        """Returns the stored vectors of the given books, skipping any missing."""
        ...

    async def query(
        self, embedding: list[float], n_results: int, exclude_book_ids: set[int]
    ) -> list[ScoredBook]:
        """Returns the books nearest a vector, excluding the given ids."""
        ...

    async def delete_book(self, book_id: int) -> None:
        """Removes a book's profile vector."""
        ...


class ChromaProfileStore:
    """`ProfileStore` backed by a persistent local ChromaDB collection."""

    def __init__(self, client: chromadb.api.ClientAPI) -> None:
        self._client = client
        self._collection: Collection | None = None

    def _get_collection(self) -> Collection:
        """Returns the profile collection, creating it on first use.

        Cosine distance is set explicitly, for the same reason as in the
        chunk store: Chroma defaults to squared L2, which would rank by
        vector magnitude as much as by meaning.
        """
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    async def upsert(self, profiles: list[BookProfile], embeddings: list[list[float]]) -> None:
        """Writes book profiles and their vectors.

        Args:
            profiles: The profiles to store.
            embeddings: One vector per profile, in the same order.

        Raises:
            ValueError: If the two lists have different lengths — that
                would pair each book with another's vector, and every
                recommendation downstream would be confidently wrong.
        """
        if len(profiles) != len(embeddings):
            raise ValueError(
                f"Got {len(profiles)} profiles but {len(embeddings)} embeddings; "
                "refusing to upsert."
            )
        if not profiles:
            return

        collection = self._get_collection()
        await asyncio.to_thread(
            collection.upsert,
            ids=[profile.id for profile in profiles],
            embeddings=embeddings,  # type: ignore[arg-type]
            documents=[profile.document for profile in profiles],
            metadatas=[{"book_id": profile.book_id} for profile in profiles],
        )
        logger.info("book_profiles_upserted", count=len(profiles))

    async def vectors_for(self, book_ids: list[int]) -> dict[int, list[float]]:
        """Returns the stored vectors of the given books.

        Books with no stored profile are simply absent from the result
        rather than raising: a book the catalogs described too thinly to
        characterise has no vector by design, and the caller reports how
        many books actually fed the profile.

        Args:
            book_ids: The books to look up.

        Returns:
            A mapping of book id to vector, for those that have one.
        """
        if not book_ids:
            return {}

        collection = self._get_collection()
        response = await asyncio.to_thread(
            collection.get,
            ids=[f"{_ID_PREFIX}:{book_id}" for book_id in book_ids],
            include=["embeddings", "metadatas"],  # type: ignore[list-item]
        )

        vectors: dict[int, list[float]] = {}
        metadatas = response.get("metadatas") or []
        embeddings = response.get("embeddings")
        # Chroma hands embeddings back as a numpy array, which is falsy for
        # an empty result and raises on a plain truthiness test otherwise —
        # so the length is checked explicitly rather than with `or []`.
        if embeddings is None or len(embeddings) == 0:
            return {}

        for index, metadata in enumerate(metadatas):
            if index >= len(embeddings):
                break
            book_id = metadata.get("book_id") if metadata else None
            if book_id is None:
                continue
            vectors[int(book_id)] = [float(value) for value in embeddings[index]]
        return vectors

    async def query(
        self, embedding: list[float], n_results: int, exclude_book_ids: set[int]
    ) -> list[ScoredBook]:
        """Returns the books nearest a vector, excluding the given ids.

        The exclusion is pushed into Chroma rather than applied afterwards
        so `n_results` means what it says: filtering a fetched page in
        Python would quietly return fewer books the more the reader has
        already read, which is the direction that matters least to notice
        and most to get wrong.

        Args:
            embedding: The query vector — normally the reader's profile.
            n_results: Maximum books to return.
            exclude_book_ids: Books that must not be returned, typically
                everything already in the reader's library.

        Returns:
            The nearest books, most similar first. Empty when the
            collection holds nothing but exclusions.
        """
        if n_results <= 0:
            return []

        collection = self._get_collection()
        # An empty `$nin` is not a valid Chroma filter, and there is
        # nothing to exclude anyway — an unfiltered query is the *correct*
        # query in this collection, unlike in `vector_store.py`.
        where: dict[str, Any] | None = (
            {"book_id": {"$nin": sorted(exclude_book_ids)}} if exclude_book_ids else None
        )

        response = await asyncio.to_thread(
            collection.query,
            query_embeddings=[embedding],  # type: ignore[arg-type]
            n_results=n_results,
            where=where,
            include=["metadatas", "distances", "embeddings"],  # type: ignore[list-item]
        )

        found = _parse_query_response(response)
        logger.info(
            "book_profiles_queried",
            requested=n_results,
            returned=len(found),
            excluded=len(exclude_book_ids),
        )
        return found

    async def delete_book(self, book_id: int) -> None:
        """Removes a book's profile vector.

        Args:
            book_id: The book whose profile to drop.
        """
        collection = self._get_collection()
        await asyncio.to_thread(collection.delete, ids=[f"{_ID_PREFIX}:{book_id}"])
        logger.info("book_profile_deleted", book_id=book_id)


def _parse_query_response(response: Any) -> list[ScoredBook]:
    """Converts Chroma's column-oriented query response into scored books.

    Args:
        response: The raw `collection.query` response.

    Returns:
        The parsed neighbours, in the order Chroma ranked them. Rows whose
        metadata carries no `book_id` are skipped — they cannot be joined
        back to a `Book` row, so there is nothing to recommend.
    """
    id_rows = response.get("ids") or []
    if not id_rows or not id_rows[0]:
        return []

    metadatas = (response.get("metadatas") or [[]])[0]
    distances = (response.get("distances") or [[]])[0]
    embedding_rows = response.get("embeddings")
    embeddings = embedding_rows[0] if embedding_rows is not None and len(embedding_rows) > 0 else []

    scored: list[ScoredBook] = []
    for index in range(len(id_rows[0])):
        metadata = metadatas[index] if index < len(metadatas) else None
        book_id = metadata.get("book_id") if metadata else None
        if book_id is None:
            continue

        distance = float(distances[index]) if index < len(distances) else 1.0
        vector = [float(value) for value in embeddings[index]] if index < len(embeddings) else []
        scored.append(
            ScoredBook(
                book_id=int(book_id),
                # Chroma's cosine distance is `1 - similarity`, so this
                # inverts it. Clamped because floating-point error puts a
                # perfect match a hair below zero, and a negative score
                # would sort and render as if it meant something.
                score=min(1.0, max(0.0, 1.0 - distance)),
                vector=vector,
            )
        )
    return scored


@lru_cache
def get_profile_store() -> ChromaProfileStore:
    """Returns the (cached) shared `ChromaProfileStore`.

    Shares the persistence directory with the chunk store — one Chroma
    client, two collections — so this opens the same on-disk index rather
    than a second one.

    Returns:
        A `ChromaProfileStore` over the configured persistence directory.
    """
    settings: Settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    return ChromaProfileStore(client)

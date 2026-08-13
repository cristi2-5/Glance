"""Persistent vector storage for book chunks, in local ChromaDB.

**The retrieval filter on `book_id` is a correctness invariant, not a
ranking preference.** A summary of *Dune* built partly from *Foundation*'s
reception passages would be fluent, plausible, and wrong — and, because
every claim would still carry a real citation, wrong in a way that looks
verified. Nothing downstream can detect that; the prompt certainly
cannot. So the constraint is enforced here, structurally, in three
independent ways:

1. **`book_id` is a required positional parameter of `query`.** There is
   no overload that omits it and no default, so a caller cannot forget it
   and cannot pass a filter dict of their own.
2. **The `where` clause is built inside this module**, from that
   parameter alone. Callers never supply raw filters, so a typo in a
   metadata key cannot reach Chroma from anywhere else.
3. **Every returned row is re-checked against the requested `book_id`**
   before it is handed back, and a mismatch raises.

The third guard exists because Chroma fails *silently* in exactly the
directions that matter: `where=None` or `where={}` returns the whole
collection unfiltered, and a misspelled metadata key matches nothing at
all — neither raises. A metadata-type mismatch is the same trap (an `int`
filter never matches a `str` value), which is why `Chunk.metadata` pins
`book_id` to `int` and this module compares it as one. If the filter ever
silently stops working, the guard turns a quiet corpus leak into a loud
failure.

Chroma's client is synchronous, so every call is dispatched with
`asyncio.to_thread` — the request path is async, and an HNSW query
holding the event loop would stall unrelated polling requests.
"""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import chromadb
import structlog
from chromadb.api.models.Collection import Collection

from app.core.config import Settings, get_settings
from app.services.chunking import Chunk

logger = structlog.get_logger(__name__)

# One collection for the whole corpus, partitioned by the `book_id`
# metadata filter rather than by collection-per-book. Chroma holds an HNSW
# index per collection, and thousands of tiny indexes would cost far more
# memory than one — which matters on a 7.4 GB laptop. The filter is what
# keeps books apart; see the module docstring for how it is enforced.
COLLECTION_NAME = "book_chunks"


class CorpusLeak(RuntimeError):
    """Raised when a retrieval returned a chunk belonging to another book.

    Not a `GlanceError`: this is an invariant violation, not a
    user-facing condition. It means the `book_id` filter silently stopped
    working, and the correct response is a 500 and a loud log — never a
    summary built on another book's passages.
    """


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk returned from a similarity search.

    Attributes:
        id: The chunk id, as produced by `chunking.Chunk`.
        book_id: The book it belongs to. Always equal to the queried book.
        content: The chunk text.
        source: Which official source produced it.
        kind: What the passage is — description, subjects, plot, reception.
        heading: The originating section heading, or `None`.
        url: A link back to the source page, or `None`.
        license: The licence the text is published under, or `None`.
        distance: Cosine distance from the query vector; lower is closer.
    """

    id: str
    book_id: int
    content: str
    source: str
    kind: str
    heading: str | None
    url: str | None
    license: str | None
    distance: float


class VectorStore(Protocol):
    """Abstraction over the chunk vector store, so it can be faked in tests."""

    async def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Writes chunks and their vectors, replacing any with the same ids."""
        ...

    async def query(
        self, book_id: int, embedding: list[float], n_results: int
    ) -> list[RetrievedChunk]:
        """Returns the nearest chunks *belonging to `book_id`*, never others."""
        ...

    async def delete_book(self, book_id: int) -> None:
        """Removes every chunk belonging to a book."""
        ...

    async def count_for_book(self, book_id: int) -> int:
        """Returns how many chunks are stored for a book."""
        ...


class ChromaVectorStore:
    """`VectorStore` backed by a persistent local ChromaDB collection."""

    def __init__(self, client: chromadb.api.ClientAPI) -> None:
        self._client = client
        self._collection: Collection | None = None

    def _get_collection(self) -> Collection:
        """Returns the chunk collection, creating it on first use.

        Cosine distance is set explicitly: Chroma defaults to squared L2,
        which is the wrong metric for `nomic-embed-text` output and would
        rank by vector magnitude as much as by meaning.
        """
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    async def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Writes chunks and their vectors, replacing any with the same ids.

        Args:
            chunks: The chunks to store.
            embeddings: One vector per chunk, in the same order.

        Raises:
            ValueError: If the two lists have different lengths — that
                would silently pair each chunk with another's vector.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Got {len(chunks)} chunks but {len(embeddings)} embeddings; refusing to upsert."
            )
        if not chunks:
            return

        collection = self._get_collection()
        await asyncio.to_thread(
            collection.upsert,
            ids=[chunk.id for chunk in chunks],
            embeddings=embeddings,  # type: ignore[arg-type]
            documents=[chunk.content for chunk in chunks],
            metadatas=[chunk.metadata() for chunk in chunks],
        )
        logger.info("chunks_upserted", book_id=chunks[0].book_id, count=len(chunks))

    async def query(
        self, book_id: int, embedding: list[float], n_results: int
    ) -> list[RetrievedChunk]:
        """Returns the nearest chunks belonging to `book_id`.

        `book_id` is required and the `where` clause is built here from it
        alone — see the module docstring for why the filter is not left to
        callers. The results are re-checked against it before returning.

        Args:
            book_id: The only book whose chunks may be returned.
            embedding: The query vector.
            n_results: Maximum number of chunks to return.

        Returns:
            The matching chunks, nearest first. Empty if the book has no
            chunks stored.

        Raises:
            CorpusLeak: If Chroma returned a chunk from another book,
                meaning the metadata filter silently stopped working.
        """
        if n_results <= 0:
            return []

        collection = self._get_collection()
        response = await asyncio.to_thread(
            collection.query,
            query_embeddings=[embedding],  # type: ignore[arg-type]
            n_results=n_results,
            where={"book_id": book_id},
            include=["documents", "metadatas", "distances"],  # type: ignore[list-item]
        )

        chunks = _parse_query_response(response)
        _assert_no_leak(book_id, chunks)

        logger.info("chunks_retrieved", book_id=book_id, count=len(chunks), requested=n_results)
        return chunks

    async def delete_book(self, book_id: int) -> None:
        """Removes every chunk belonging to a book.

        Called before re-ingesting a book whose passages were refreshed,
        so stale chunks from the previous fetch cannot be retrieved and
        cited alongside the new ones.

        Args:
            book_id: The book whose chunks to drop.
        """
        collection = self._get_collection()
        await asyncio.to_thread(collection.delete, where={"book_id": book_id})
        logger.info("chunks_deleted", book_id=book_id)

    async def count_for_book(self, book_id: int) -> int:
        """Returns how many chunks are stored for a book.

        Args:
            book_id: The book to count chunks for.

        Returns:
            The number of stored chunks; `0` if the book was never ingested.
        """
        collection = self._get_collection()
        response = await asyncio.to_thread(collection.get, where={"book_id": book_id}, include=[])
        return len(response.get("ids") or [])


def _parse_query_response(response: Any) -> list[RetrievedChunk]:
    """Converts Chroma's column-oriented query response into chunk objects.

    Chroma returns one list per queried vector, each holding parallel
    lists of ids, documents, metadatas and distances. We always query with
    a single vector, so we read row 0 throughout.

    Args:
        response: The raw `collection.query` response.

    Returns:
        The parsed chunks, in the order Chroma ranked them.
    """
    id_rows = response.get("ids") or []
    if not id_rows or not id_rows[0]:
        return []

    ids = id_rows[0]
    documents = (response.get("documents") or [[]])[0]
    metadatas = (response.get("metadatas") or [[]])[0]
    distances = (response.get("distances") or [[]])[0]

    chunks: list[RetrievedChunk] = []
    for index, chunk_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        chunks.append(
            RetrievedChunk(
                id=chunk_id,
                book_id=int(metadata.get("book_id", -1)),
                content=documents[index] if index < len(documents) else "",
                source=str(metadata.get("source", "")),
                kind=str(metadata.get("kind", "")),
                # Chroma cannot store `None`, so absent values were written
                # as empty strings; restore them here.
                heading=str(metadata.get("heading") or "") or None,
                url=str(metadata.get("url") or "") or None,
                license=str(metadata.get("license") or "") or None,
                distance=float(distances[index]) if index < len(distances) else 0.0,
            )
        )
    return chunks


def _assert_no_leak(book_id: int, chunks: list[RetrievedChunk]) -> None:
    """Verifies every retrieved chunk belongs to the requested book.

    The last line of defence behind the `where` filter. Chroma does not
    raise when a filter matches nothing or matches everything, so without
    this a broken filter would surface as a confidently-cited summary of
    the wrong book rather than as an error.

    Args:
        book_id: The book that was queried.
        chunks: The chunks Chroma returned.

    Raises:
        CorpusLeak: If any chunk belongs to a different book.
    """
    foreign = [chunk for chunk in chunks if chunk.book_id != book_id]
    if not foreign:
        return

    logger.error(
        "corpus_leak_detected",
        requested_book_id=book_id,
        foreign_chunk_ids=[chunk.id for chunk in foreign],
        foreign_book_ids=sorted({chunk.book_id for chunk in foreign}),
    )
    raise CorpusLeak(
        f"Retrieval for book {book_id} returned {len(foreign)} chunk(s) from another book. "
        "The book_id metadata filter is not working; refusing to build a summary."
    )


@lru_cache
def get_vector_store() -> ChromaVectorStore:
    """Returns the (cached) shared `ChromaVectorStore`.

    Cached because `chromadb.PersistentClient` opens the on-disk index;
    building one per request would reopen it every time.

    Returns:
        A `ChromaVectorStore` over the configured persistence directory.
    """
    settings: Settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    return ChromaVectorStore(client)

"""Tests for the Chroma vector store, and above all for the `book_id` filter.

**These run against a real ephemeral ChromaDB, not a fake.** A fake store
would only prove that our own filter-passing code calls itself correctly;
the risk being defended against is that *Chroma's* metadata filter does
not do what we assume — it returns everything for `where=None` and
nothing for a misspelled key, in both cases without raising. Only the
real client can falsify that assumption, so the isolation tests use one.

The store is ephemeral rather than persistent so nothing touches
`data/chroma`, and each test gets a clean collection.
"""

from collections.abc import Iterator

import chromadb
import pytest

from app.services.chunking import Chunk, chunk_text_sources
from app.services.vector_store import ChromaVectorStore, CorpusLeak, RetrievedChunk
from tests.fakes import HashingEmbeddingClient
from tests.fixtures.rag_corpus import (
    FOUNDATION_MARKERS,
    assign_fake_ids,
    build_dune,
    build_foundation,
)

DUNE_ID = 1
FOUNDATION_ID = 2


@pytest.fixture
def store() -> Iterator[ChromaVectorStore]:
    """A vector store over a clean, in-memory Chroma instance.

    `EphemeralClient` instances with identical settings share one
    in-memory system, so a fresh client is *not* a fresh database — the
    explicit `reset` in teardown is what actually isolates the tests, and
    `allow_reset` is what permits it.
    """
    client = chromadb.EphemeralClient(
        settings=chromadb.Settings(allow_reset=True, anonymized_telemetry=False)
    )
    client.reset()
    yield ChromaVectorStore(client)
    client.reset()


@pytest.fixture
def embedder() -> HashingEmbeddingClient:
    """The deterministic offline embedding client."""
    return HashingEmbeddingClient()


async def _ingest(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient, chunks: list[Chunk]
) -> None:
    """Embeds and stores a list of chunks."""
    vectors = await embedder.embed([chunk.content for chunk in chunks])
    await store.upsert(chunks, vectors)


def _dune_chunks() -> list[Chunk]:
    """The *Dune* fixture, chunked."""
    book = assign_fake_ids(build_dune(), book_id=DUNE_ID, first_source_id=1)
    return chunk_text_sources(DUNE_ID, list(book.text_sources))


def _foundation_chunks() -> list[Chunk]:
    """The *Foundation* fixture, chunked."""
    book = assign_fake_ids(build_foundation(), book_id=FOUNDATION_ID, first_source_id=100)
    return chunk_text_sources(FOUNDATION_ID, list(book.text_sources))


async def test_stored_chunks_are_retrievable(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """A stored corpus can be searched and comes back with its provenance."""
    await _ingest(store, embedder, _dune_chunks())

    query = (await embedder.embed(["the desert planet Arrakis and the spice melange"]))[0]
    found = await store.query(DUNE_ID, query, n_results=3)

    assert found
    assert all(isinstance(chunk, RetrievedChunk) for chunk in found)
    assert all(chunk.book_id == DUNE_ID for chunk in found)
    assert any("Arrakis" in chunk.content for chunk in found)
    assert all(chunk.url for chunk in found)


async def test_retrieval_never_crosses_books(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """**The hard constraint.** Two books stored; a query returns only one's.

    Both corpora are science fiction of the same era with similar critical
    vocabulary, so a semantic search over the unfiltered collection would
    genuinely rank *Foundation*'s reception passage against a *Dune*
    reception query. That is the point: the filter, not the ranking, is
    what keeps them apart.
    """
    await _ingest(store, embedder, _dune_chunks())
    await _ingest(store, embedder, _foundation_chunks())

    # A query written to match both books' reception passages.
    query = (await embedder.embed(["critics praised the novel; it won a Hugo Award"]))[0]

    # Ask for more results than one book has chunks, so an unfiltered
    # search would certainly spill over into the other book.
    found = await store.query(DUNE_ID, query, n_results=20)

    assert found, "the filter must not have emptied the result"
    assert {chunk.book_id for chunk in found} == {DUNE_ID}
    combined = " ".join(chunk.content for chunk in found)
    for marker in FOUNDATION_MARKERS:
        assert marker not in combined, f"{marker!r} leaked from the other book"


async def test_each_book_retrieves_its_own_corpus(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """The filter is not accidentally pinned to one book — both directions work."""
    await _ingest(store, embedder, _dune_chunks())
    await _ingest(store, embedder, _foundation_chunks())

    query = (await embedder.embed(["what is this book about"]))[0]

    dune = await store.query(DUNE_ID, query, n_results=20)
    foundation = await store.query(FOUNDATION_ID, query, n_results=20)

    assert {chunk.book_id for chunk in dune} == {DUNE_ID}
    assert {chunk.book_id for chunk in foundation} == {FOUNDATION_ID}
    assert not {chunk.id for chunk in dune} & {chunk.id for chunk in foundation}


async def test_query_for_an_unknown_book_returns_nothing(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """A book with no chunks returns empty, rather than the whole collection.

    The failure this guards against is `where` being ignored: with a
    populated collection and a filter that does nothing, this returns
    every chunk in the store.
    """
    await _ingest(store, embedder, _dune_chunks())

    query = (await embedder.embed(["anything at all"]))[0]
    found = await store.query(999, query, n_results=20)

    assert found == []


async def test_leak_guard_raises_when_the_filter_fails(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """If Chroma ever returns a foreign chunk, the store must raise, not return it.

    Simulated by monkeypatching the parse step, because a working Chroma
    cannot produce this — which is precisely why the guard needs its own
    test. A silent leak is worse than an outage: the summary would still
    be fluent, still carry real citations, and still be about the wrong
    book.
    """
    await _ingest(store, embedder, _dune_chunks())
    query = (await embedder.embed(["anything"]))[0]

    import app.services.vector_store as vector_store_module

    original = vector_store_module._parse_query_response

    def _leaky(response: object) -> list[RetrievedChunk]:
        chunks = original(response)
        chunks.append(
            RetrievedChunk(
                id="b2:s100:0",
                book_id=FOUNDATION_ID,
                content="Hari Seldon foresees the collapse of the Galactic Empire.",
                source="wikipedia",
                kind="plot",
                heading="Plot",
                url=None,
                license=None,
                distance=0.1,
            )
        )
        return chunks

    vector_store_module._parse_query_response = _leaky  # type: ignore[assignment]
    try:
        with pytest.raises(CorpusLeak) as excinfo:
            await store.query(DUNE_ID, query, n_results=5)
    finally:
        vector_store_module._parse_query_response = original  # type: ignore[assignment]

    assert "another book" in str(excinfo.value)


async def test_delete_book_removes_only_that_book(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """Deleting one book's chunks leaves the other's intact."""
    await _ingest(store, embedder, _dune_chunks())
    await _ingest(store, embedder, _foundation_chunks())

    await store.delete_book(DUNE_ID)

    assert await store.count_for_book(DUNE_ID) == 0
    assert await store.count_for_book(FOUNDATION_ID) > 0


async def test_upsert_replaces_rather_than_duplicates(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """Re-ingesting the same corpus must not double it.

    Chunk ids are deterministic, so a second ingest of unchanged passages
    overwrites in place — otherwise every TTL refresh would grow the
    collection and retrieval would return the same passage twice.
    """
    chunks = _dune_chunks()
    await _ingest(store, embedder, chunks)
    first = await store.count_for_book(DUNE_ID)

    await _ingest(store, embedder, chunks)

    assert await store.count_for_book(DUNE_ID) == first


async def test_upsert_rejects_mismatched_lengths(store: ChromaVectorStore) -> None:
    """A chunk/vector length mismatch is refused, not silently misaligned."""
    chunks = _dune_chunks()

    with pytest.raises(ValueError, match="refusing to upsert"):
        await store.upsert(chunks, [[0.1, 0.2]])


async def test_empty_inputs_are_no_ops(
    store: ChromaVectorStore, embedder: HashingEmbeddingClient
) -> None:
    """Empty upserts and non-positive result counts do nothing, quietly."""
    await store.upsert([], [])

    await _ingest(store, embedder, _dune_chunks())
    query = (await embedder.embed(["anything"]))[0]

    assert await store.query(DUNE_ID, query, n_results=0) == []

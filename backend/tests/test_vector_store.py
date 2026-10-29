"""Tests for the pgvector chunk store, and above all for the `book_id` filter.

**These run against the real PostgreSQL + pgvector, not a fake.** A fake
store would only prove that our own filter-passing code calls itself; the
risks being defended against live in the database - a `WHERE` that is
missing, a foreign key that is not enforced, and (the subtle one) an ANN
index that applies the filter *after* its approximate scan and so returns
too few rows for a small book. Only the real server can falsify those
assumptions, so every test here uses it (see `conftest.db_session_factory`).

The fixture corpus deliberately holds a plausible near-miss (*Foundation*
beside *Dune*: same genre, same era, same shape of award-winning
reception), because a filter is only worth testing against a corpus that
would genuinely rank against it.
"""

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

import app.services.vector_store as vector_store_module
from app.models.book import Book, SourceKind, SourceName, TextSource
from app.models.vector import EMBEDDING_DIMENSIONS
from app.services.chunking import Chunk, chunk_text_sources
from app.services.vector_store import CorpusLeak, PgVectorStore, RetrievedChunk
from tests.fakes import HashingEmbeddingClient
from tests.fixtures.rag_corpus import FOUNDATION_MARKERS, build_dune, build_foundation

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def store() -> PgVectorStore:
    """The (stateless) chunk store; the database is `db_session_factory`'s."""
    return PgVectorStore()


@pytest.fixture
def embedder() -> HashingEmbeddingClient:
    """The deterministic offline embedding client."""
    return HashingEmbeddingClient()


async def _persist(db: AsyncSession, book: Book) -> list[Chunk]:
    """Saves a fixture book with its passages and returns its chunks.

    The chunk table has foreign keys to `books` and `text_sources`, so a
    chunk can only be stored for rows that exist - the ids the chunks carry
    are the ones the database assigned.
    """
    db.add(book)
    await db.commit()
    return chunk_text_sources(book.id, list(book.text_sources))


async def _ingest(
    db: AsyncSession, store: PgVectorStore, embedder: HashingEmbeddingClient, chunks: list[Chunk]
) -> None:
    """Embeds and stores a list of chunks."""
    vectors = await embedder.embed([chunk.content for chunk in chunks])
    await store.upsert(db, chunks, vectors)
    await db.commit()


async def _both_books(
    db: AsyncSession, store: PgVectorStore, embedder: HashingEmbeddingClient
) -> tuple[int, int]:
    """Stores *Dune* and *Foundation*; returns their ids."""
    dune_chunks = await _persist(db, build_dune())
    foundation_chunks = await _persist(db, build_foundation())
    await _ingest(db, store, embedder, dune_chunks)
    await _ingest(db, store, embedder, foundation_chunks)
    return dune_chunks[0].book_id, foundation_chunks[0].book_id


async def test_stored_chunks_are_retrievable(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """A stored corpus can be searched and comes back with its provenance."""
    async with db_session_factory() as db:
        chunks = await _persist(db, build_dune())
        dune_id = chunks[0].book_id
        await _ingest(db, store, embedder, chunks)

        query = (await embedder.embed(["the desert planet Arrakis and the spice melange"]))[0]
        found = await store.query(db, dune_id, query, n_results=3)

    assert found
    assert all(isinstance(chunk, RetrievedChunk) for chunk in found)
    assert all(chunk.book_id == dune_id for chunk in found)
    assert any("Arrakis" in chunk.content for chunk in found)
    assert all(chunk.url for chunk in found)
    assert [chunk.distance for chunk in found] == sorted(chunk.distance for chunk in found)


async def test_retrieval_never_crosses_books(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """**The hard constraint.** Two books stored; a query returns only one's.

    Both corpora are science fiction of the same era with similar critical
    vocabulary, so a semantic search over the whole table would genuinely
    rank *Foundation*'s reception passage against a *Dune* reception
    query. That is the point: the filter, not the ranking, is what keeps
    them apart.
    """
    async with db_session_factory() as db:
        dune_id, _ = await _both_books(db, store, embedder)

        # A query written to match both books' reception passages.
        query = (await embedder.embed(["critics praised the novel; it won a Hugo Award"]))[0]

        # Ask for more results than one book has chunks, so an unfiltered
        # search would certainly spill over into the other book.
        found = await store.query(db, dune_id, query, n_results=20)

    assert found, "the filter must not have emptied the result"
    assert {chunk.book_id for chunk in found} == {dune_id}
    combined = " ".join(chunk.content for chunk in found)
    for marker in FOUNDATION_MARKERS:
        assert marker not in combined, f"{marker!r} leaked from the other book"


async def test_each_book_retrieves_its_own_corpus(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """The filter is not accidentally pinned to one book - both directions work."""
    async with db_session_factory() as db:
        dune_id, foundation_id = await _both_books(db, store, embedder)
        query = (await embedder.embed(["what is this book about"]))[0]

        dune = await store.query(db, dune_id, query, n_results=20)
        foundation = await store.query(db, foundation_id, query, n_results=20)

    assert {chunk.book_id for chunk in dune} == {dune_id}
    assert {chunk.book_id for chunk in foundation} == {foundation_id}
    assert not {chunk.id for chunk in dune} & {chunk.id for chunk in foundation}


async def test_query_for_an_unknown_book_returns_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """A book with no chunks returns empty, rather than the whole table.

    The failure this guards against is the `WHERE` being lost: with a
    populated table and a filter that does nothing, this returns every
    chunk in the store.
    """
    async with db_session_factory() as db:
        await _both_books(db, store, embedder)

        query = (await embedder.embed(["anything at all"]))[0]
        found = await store.query(db, 999, query, n_results=20)

    assert found == []


async def test_a_small_book_among_many_far_away_ones_still_returns_all_its_chunks(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
) -> None:
    """**Guards against an ANN index being added to `book_chunks`.**

    pgvector applies a `WHERE` clause *after* an HNSW/IVFFlat scan: the
    index yields its nearest ~40 vectors across *all* books and only then
    filters them. Here one book of three chunks sits far from the query,
    surrounded by thirty books whose chunks all sit right next to it - so
    with such an index the filtered query would see none of the target's
    chunks among the 40 candidates and silently return fewer rows (or
    nothing), which would read as "this book has no passages".

    The exact scan that exists today returns all three. If this test ever
    fails, someone added an approximate index: remove it, or add
    `hnsw.iterative_scan` and prove the filtered results are complete.
    """
    near = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
    far = [0.0, 1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2)

    async with db_session_factory() as db:
        chunks: list[Chunk] = []
        vectors: list[list[float]] = []
        target_id = 0
        for number in range(31):
            book = Book(
                normalized_key=f"book-{number}|someone",
                title=f"Book {number}",
                metadata_found=True,
            )
            book.text_sources = [
                TextSource(
                    source=SourceName.WIKIPEDIA.value,
                    kind=SourceKind.PLOT.value,
                    content="text",
                )
            ]
            db.add(book)
            await db.flush()
            source_id = book.text_sources[0].id

            is_target = number == 15
            target_id = book.id if is_target else target_id
            count = 3 if is_target else 5
            for index in range(count):
                chunks.append(
                    Chunk(
                        id=f"b{book.id}:s{source_id}:{index}",
                        book_id=book.id,
                        text_source_id=source_id,
                        source=SourceName.WIKIPEDIA.value,
                        kind=SourceKind.PLOT.value,
                        heading=None,
                        url=None,
                        license=None,
                        content=f"chunk {index} of book {number}",
                    )
                )
                vectors.append(far if is_target else near)
        await store.upsert(db, chunks, vectors)
        await db.commit()

        found = await store.query(db, target_id, near, n_results=10)

    assert len(found) == 3, "the filter must see all of a small book's chunks"
    assert {chunk.book_id for chunk in found} == {target_id}


async def test_leak_guard_raises_when_the_filter_fails(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the filter ever stops working, the store must raise, not return the leak.

    Simulated by making the module's `select` produce statements that
    silently ignore `.where(...)` - exactly the refactor accident the
    guard exists for: the query still succeeds and now ranks every book's
    chunks. A silent leak is worse than an outage: the summary would still
    be fluent, still carry real citations, and still be about the wrong
    book.
    """

    class _IgnoresWhere(Select):  # type: ignore[type-arg]
        inherit_cache = False

        def where(self, *criteria: object) -> "_IgnoresWhere":
            return self

    async with db_session_factory() as db:
        dune_id, _ = await _both_books(db, store, embedder)
        query = (await embedder.embed(["anything"]))[0]

        monkeypatch.setattr(vector_store_module, "select", lambda *cols: _IgnoresWhere(*cols))
        with pytest.raises(CorpusLeak) as excinfo:
            await store.query(db, dune_id, query, n_results=50)

    assert "another book" in str(excinfo.value)


async def test_delete_book_removes_only_that_book(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """Deleting one book's chunks leaves the other's intact."""
    async with db_session_factory() as db:
        dune_id, foundation_id = await _both_books(db, store, embedder)

        await store.delete_book(db, dune_id)

        assert await store.count_for_book(db, dune_id) == 0
        assert await store.count_for_book(db, foundation_id) > 0


async def test_upsert_replaces_rather_than_duplicates(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """Re-ingesting the same corpus must not double it.

    Chunk ids are deterministic, so a second ingest of unchanged passages
    overwrites in place - otherwise every refresh would grow the table and
    retrieval would return the same passage twice.
    """
    async with db_session_factory() as db:
        chunks = await _persist(db, build_dune())
        book_id = chunks[0].book_id
        await _ingest(db, store, embedder, chunks)
        first = await store.count_for_book(db, book_id)

        await _ingest(db, store, embedder, chunks)

        assert await store.count_for_book(db, book_id) == first == len(chunks)


async def test_upsert_overwrites_the_content_and_vector_of_an_existing_chunk(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """`ON CONFLICT DO UPDATE` really updates, not just ignores the new row."""
    async with db_session_factory() as db:
        chunks = await _persist(db, build_dune())
        book_id = chunks[0].book_id
        target = chunks[0]
        await _ingest(db, store, embedder, [target])

        target.content = "Completely different words about sardines and lemons."
        await _ingest(db, store, embedder, [target])

        query = (await embedder.embed([target.content]))[0]
        found = await store.query(db, book_id, query, n_results=1)

    assert found[0].id == target.id
    assert found[0].content == target.content
    assert found[0].distance == pytest.approx(0.0, abs=1e-4)


async def test_upsert_rejects_mismatched_lengths(
    db_session_factory: async_sessionmaker[AsyncSession], store: PgVectorStore
) -> None:
    """A chunk/vector length mismatch is refused, not silently misaligned."""
    async with db_session_factory() as db:
        chunks = await _persist(db, build_dune())

        with pytest.raises(ValueError, match="refusing to upsert"):
            await store.upsert(db, chunks, [[0.1, 0.2]])


async def test_empty_inputs_are_no_ops(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """Empty upserts and non-positive result counts do nothing, quietly."""
    async with db_session_factory() as db:
        await store.upsert(db, [], [])

        chunks = await _persist(db, build_dune())
        await _ingest(db, store, embedder, chunks)
        query = (await embedder.embed(["anything"]))[0]

        assert await store.query(db, chunks[0].book_id, query, n_results=0) == []


# ---------------------------------------------------------------------------
# What the database itself enforces: foreign keys and cascades
# ---------------------------------------------------------------------------


async def test_a_chunk_for_a_book_that_does_not_exist_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """The foreign key makes a chunk of a missing book impossible.

    This is the structural fix for the id-reuse hazard (a new book
    inheriting the chunks of an old one that had the same id): there is no
    way to hold a chunk whose book is gone, so ids need not be trusted.
    """
    orphan = Chunk(
        id="b4242:s4242:0",
        book_id=4242,
        text_source_id=4242,
        source=SourceName.WIKIPEDIA.value,
        kind=SourceKind.PLOT.value,
        heading=None,
        url=None,
        license=None,
        content="A passage about nothing.",
    )
    vectors = await embedder.embed([orphan.content])

    async with db_session_factory() as db:
        with pytest.raises(IntegrityError):
            await store.upsert(db, [orphan], vectors)


async def test_deleting_a_book_deletes_its_chunks(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """`ON DELETE CASCADE` from `books`: no chunk outlives its book."""
    async with db_session_factory() as db:
        dune_id, foundation_id = await _both_books(db, store, embedder)

        dune = await db.get(Book, dune_id)
        assert dune is not None
        await db.delete(dune)
        await db.commit()

        assert await store.count_for_book(db, dune_id) == 0
        assert await store.count_for_book(db, foundation_id) > 0


async def test_replacing_a_books_sources_deletes_the_chunks_cut_from_them(
    db_session_factory: async_sessionmaker[AsyncSession],
    store: PgVectorStore,
    embedder: HashingEmbeddingClient,
) -> None:
    """`ON DELETE CASCADE` from `text_sources`: a refresh takes the old chunks with it.

    Module 4 replaces a book's `TextSource` rows wholesale (the
    `delete-orphan` relationship deletes the old ones). Chunks cut from
    those rows must not survive them.
    """
    async with db_session_factory() as db:
        dune_id, foundation_id = await _both_books(db, store, embedder)

        dune = await db.get(Book, dune_id)
        assert dune is not None
        dune.text_sources = [
            TextSource(
                source=SourceName.GOOGLE_BOOKS.value,
                kind=SourceKind.DESCRIPTION.value,
                content="A freshly fetched description.",
            )
        ]
        await db.commit()

        assert await store.count_for_book(db, dune_id) == 0
        assert await store.count_for_book(db, foundation_id) > 0


# ---------------------------------------------------------------------------
# Guard 4: nothing else may query the chunk table
# ---------------------------------------------------------------------------


def test_only_the_vector_store_and_the_models_reference_the_chunk_model() -> None:
    """The fourth guard on the `book_id` invariant.

    The filter lives in `vector_store.py`. If any other module could import
    `BookChunk`, it could write its own query beside the guarded one - and
    that one would have no mandatory `book_id`, no leak check. So the model
    is referenced only by the store and by the models package (migrations
    live outside `app/`). A new reference elsewhere fails here, which is
    the prompt to route it through `PgVectorStore` instead.
    """
    allowed = {APP_DIR / "services" / "vector_store.py"}
    allowed_dirs = {APP_DIR / "models"}
    pattern = re.compile(r"\bBookChunk\b")

    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if path not in allowed
        and not any(directory in path.parents for directory in allowed_dirs)
        and pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], f"BookChunk referenced outside the vector store: {offenders}"


def test_no_module_reaches_the_chunk_table_by_name() -> None:
    """Guard 4, second half: the table name is as dangerous as the model.

    `text("SELECT ... FROM book_chunks")`, `table("book_chunks")` or
    `Base.metadata.tables["book_chunks"]` bypass the model, so the check
    above cannot see them. Any string constant naming the table outside the
    store and its model fails here. Docstrings are skipped: they mention
    the table legitimately, and cannot execute.
    """
    allowed = {APP_DIR / "services" / "vector_store.py", APP_DIR / "models" / "vector.py"}

    def _docstrings(tree: ast.AST) -> set[int]:
        found: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                ):
                    found.add(id(body[0].value))
        return found

    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "book_chunks" in node.value
                and id(node) not in docstrings
            ):
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")

    assert offenders == [], f"book_chunks named outside the vector store: {offenders}"

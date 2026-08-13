"""Tests for retrieval-augmented summary generation.

The centrepiece is `test_every_sentence_in_the_summary_traces_to_a_chunk`,
which is Module 5's definition of done: the whole pipeline runs over a
fixture corpus with known content, and every sentence of the result is
checked back to a real chunk of the right book.

These run the *real* pipeline — real chunking, real ChromaDB, real
retrieval and filtering — with only two things faked: the embedding model
(`HashingEmbeddingClient`, deterministic and offline) and the summary
model (`ScriptedSummaryClient`, which answers from the prompt it is
given). Faking the model is what makes the assertions possible at all: a
real LLM's output changes between runs, so "every sentence is traceable"
could only ever be spot-checked, never asserted. Here the scripted
responder plays both an honest model and a hallucinating one, and the
suite pins what the pipeline does with each.
"""

import json
import re
from collections.abc import Iterator
from datetime import datetime, timedelta

import chromadb
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.book import Book
from app.schemas.summary import SummaryClaim
from app.services.chunking import chunk_text_sources
from app.services.rag_service import RagService, build_prompt, verify_claims
from app.services.vector_store import ChromaVectorStore, RetrievedChunk
from tests.fakes import HashingEmbeddingClient, ScriptedSummaryClient
from tests.fixtures.rag_corpus import (
    DUNE_MARKERS,
    FOUNDATION_MARKERS,
    assign_fake_ids,
    build_dune,
    build_foundation,
)

# Matches one passage block in the prompt: "[id] (source — label)\ncontent".
_PASSAGE_BLOCK = re.compile(r"\[([^\]]+)\] \([^)]*\)\n(.+?)(?=\n\n\[|\n\nASPECTS)", re.DOTALL)


@pytest.fixture
def chroma_client() -> Iterator[chromadb.api.ClientAPI]:
    """A clean in-memory Chroma instance per test."""
    client = chromadb.EphemeralClient(
        settings=chromadb.Settings(allow_reset=True, anonymized_telemetry=False)
    )
    client.reset()
    yield client
    client.reset()


def parse_prompt_passages(prompt: str) -> dict[str, str]:
    """Extracts the `{chunk_id: content}` map the prompt presented.

    Args:
        prompt: The synthesis prompt.

    Returns:
        The passages, keyed by the id the model is expected to cite.
    """
    return {match.group(1): match.group(2).strip() for match in _PASSAGE_BLOCK.finditer(prompt)}


def grounded_responder(prompt: str) -> str:
    """A well-behaved model: every claim is lifted from a cited passage.

    Each claim's text is the first sentence of the chunk it cites, so the
    traceability assertions can be exact — a claim is expected to appear
    verbatim inside its source chunk, which no amount of fluent
    paraphrasing could let us check.
    """
    passages = parse_prompt_passages(prompt)
    claims = []
    for chunk_id, content in list(passages.items())[:4]:
        first_sentence = content.split(". ")[0].strip()
        if not first_sentence.endswith("."):
            first_sentence += "."
        claims.append({"text": first_sentence, "chunk_ids": [chunk_id]})
    return json.dumps({"claims": claims, "uncovered": []})


async def _make_service(
    chroma_client: chromadb.api.ClientAPI,
    responder: object,
) -> tuple[RagService, HashingEmbeddingClient, ScriptedSummaryClient]:
    """Builds a `RagService` over real Chroma with fake model backends."""
    embedder = HashingEmbeddingClient()
    ai_client = ScriptedSummaryClient(responder)  # type: ignore[arg-type]
    service = RagService(
        embeddings=embedder,
        vector_store=ChromaVectorStore(chroma_client),
        ai_client=ai_client,
        settings=get_settings(),
    )
    return service, embedder, ai_client


async def _persist(session_factory: async_sessionmaker[AsyncSession], *books: Book) -> list[int]:
    """Saves fixture books and returns their assigned ids."""
    async with session_factory() as db:
        for book in books:
            db.add(book)
        await db.commit()
        return [book.id for book in books]


# ---------------------------------------------------------------------------
# Definition of done: statement-by-statement traceability
# ---------------------------------------------------------------------------


async def test_every_sentence_in_the_summary_traces_to_a_chunk(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """**Module 5's definition of done.**

    Runs the full pipeline over the fixture corpus and verifies, sentence
    by sentence, that the generated summary is traceable:

    1. Every claim cites at least one chunk.
    2. Every cited id resolves to a chunk that really exists.
    3. Every cited chunk belongs to *this* book.
    4. Every claim's text is actually present in a chunk it cites.
    5. The summary prose contains no sentence that is not a claim — so
       there is no unattributed text hiding in the joined output.
    """
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)
        corpus = chunk_text_sources(book.id, list(book.text_sources))

    assert summary.available
    assert summary.claims

    chunks_by_id = {chunk.id: chunk for chunk in corpus}

    for claim in summary.claims:
        # 1. Every claim carries a citation.
        assert claim.chunk_ids, f"claim without a citation: {claim.text!r}"

        for chunk_id in claim.chunk_ids:
            # 2. The cited chunk exists.
            assert chunk_id in chunks_by_id, f"claim cites unknown chunk {chunk_id!r}"
            # 3. It belongs to this book.
            assert chunks_by_id[chunk_id].book_id == book_id

        # 4. The claim's text is present in one of the chunks it cites.
        supporting = [chunks_by_id[chunk_id].content for chunk_id in claim.chunk_ids]
        assert any(
            claim.text.rstrip(".") in content for content in supporting
        ), f"claim not found in its cited passages: {claim.text!r}"

    # 5. The prose is exactly the claims, so no sentence escapes citation.
    assert summary.text == " ".join(claim.text for claim in summary.claims)

    # Every citation offered to the client resolves to a real excerpt.
    cited_ids = {chunk_id for claim in summary.claims for chunk_id in claim.chunk_ids}
    assert {review.id for review in summary.reviews} == cited_ids
    for review in summary.reviews:
        assert review.excerpt == chunks_by_id[review.id].content


async def test_summary_never_contains_another_books_content(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """With two books ingested, a summary of one cites only that one.

    The end-to-end counterpart to the store-level isolation test: the
    constraint has to survive the whole pipeline, not just the query call.
    """
    dune = build_dune()
    foundation = build_foundation()
    dune_id, foundation_id = await _persist(db_session_factory, dune, foundation)

    service, _, _ = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        other = await db.get(Book, foundation_id)
        assert other is not None
        await service.ingest(other)

        book = await db.get(Book, dune_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert summary.available
    haystack = f"{summary.text} " + " ".join(review.excerpt for review in summary.reviews)
    for marker in FOUNDATION_MARKERS:
        assert marker not in haystack, f"{marker!r} leaked into Dune's summary"

    assert all(
        chunk_id.startswith(f"b{dune_id}:")
        for review in summary.reviews
        for chunk_id in [review.id]
    )


async def test_marker_facts_are_attributed_to_the_right_passage(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A claim mentioning a known marker cites the passage that contains it.

    This is the "known content" half of the fixture corpus earning its
    keep: each marker appears in exactly one passage, so a correct
    citation is checkable rather than merely plausible.
    """
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)
        corpus = {chunk.id: chunk for chunk in chunk_text_sources(book.id, list(book.text_sources))}

    checked = 0
    for claim in summary.claims:
        for marker in DUNE_MARKERS:
            if marker not in claim.text:
                continue
            checked += 1
            assert any(
                marker in corpus[chunk_id].content for chunk_id in claim.chunk_ids
            ), f"claim mentions {marker!r} but no cited passage contains it: {claim.text!r}"

    assert checked > 0, "the fixture summary should mention at least one marker"


# ---------------------------------------------------------------------------
# Anti-hallucination: verification drops what the prompt only asked for
# ---------------------------------------------------------------------------


async def test_claims_citing_invented_chunks_are_dropped(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A model that invents a citation gets that claim removed, not shown.

    The prompt asks for grounded citations; this asserts what happens when
    the model ignores it, which is the case that actually matters.
    """

    def hallucinating(prompt: str) -> str:
        real_id = next(iter(parse_prompt_passages(prompt)))
        return json.dumps(
            {
                "claims": [
                    {"text": "A real, cited statement.", "chunk_ids": [real_id]},
                    {
                        "text": "Herbert wrote the novel in a beach hut over six years.",
                        "chunk_ids": ["b1:s999:0"],
                    },
                    {"text": "It sold twelve million copies.", "chunk_ids": []},
                ],
                "uncovered": [],
            }
        )

    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, hallucinating)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    texts = [claim.text for claim in summary.claims]
    assert texts == ["A real, cited statement."]
    assert "beach hut" not in summary.text
    assert "twelve million" not in summary.text


async def test_partially_invalid_citations_keep_only_the_real_ones(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A claim citing one real and one invented id keeps only the real one.

    Every id reaching the client must resolve, or the citation cannot be
    made tappable.
    """

    def mixed(prompt: str) -> str:
        real_id = next(iter(parse_prompt_passages(prompt)))
        return json.dumps(
            {
                "claims": [{"text": "A grounded statement.", "chunk_ids": [real_id, "b1:s404:0"]}],
                "uncovered": [],
            }
        )

    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, mixed)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert len(summary.claims) == 1
    assert "b1:s404:0" not in summary.claims[0].chunk_ids
    assert len(summary.claims[0].chunk_ids) == 1
    assert {review.id for review in summary.reviews} == set(summary.claims[0].chunk_ids)


async def test_summary_unavailable_when_no_claim_survives(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """If nothing verifies, the summary is reported unavailable, not faked.

    The client then falls back to the publisher's blurb — an honest gap
    rather than an ungrounded summary.
    """

    def all_invented(prompt: str) -> str:
        return json.dumps(
            {
                "claims": [{"text": "Entirely invented.", "chunk_ids": ["nope:0"]}],
                "uncovered": [],
            }
        )

    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, all_invented)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert summary.available is False
    assert summary.text == ""
    assert summary.claims == []


async def test_uncovered_aspects_are_reported(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """Gaps the model reports are carried through to the client."""

    def with_gap(prompt: str) -> str:
        real_id = next(iter(parse_prompt_passages(prompt)))
        return json.dumps(
            {
                "claims": [{"text": "A grounded statement.", "chunk_ids": [real_id]}],
                "uncovered": ["reception"],
            }
        )

    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, with_gap)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert summary.uncovered == ["reception"]


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "I'm afraid I can't help with that.",
        "{ this is not json",
        '{"claims": "not a list"}',
        '{"wrong_key": []}',
    ],
)
async def test_unusable_model_replies_yield_no_summary(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
    reply: str,
) -> None:
    """A malformed reply is an unavailable summary, never a crash."""
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, lambda _prompt: reply)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert summary.available is False


async def test_json_wrapped_in_prose_is_still_parsed(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A reply padded with a code fence or preamble still parses."""

    def fenced(prompt: str) -> str:
        real_id = next(iter(parse_prompt_passages(prompt)))
        payload = json.dumps(
            {"claims": [{"text": "A grounded statement.", "chunk_ids": [real_id]}]}
        )
        return f"Here you go:\n```json\n{payload}\n```"

    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, fenced)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert summary.available
    assert summary.claims[0].text == "A grounded statement."


# ---------------------------------------------------------------------------
# The prompt itself
# ---------------------------------------------------------------------------


def test_prompt_carries_the_real_chunk_ids_and_the_grounding_rules() -> None:
    """The prompt must present citable ids and forbid outside knowledge."""
    book = assign_fake_ids(build_dune(), book_id=3)
    chunks = [
        RetrievedChunk(
            id=chunk.id,
            book_id=chunk.book_id,
            content=chunk.content,
            source=chunk.source,
            kind=chunk.kind,
            heading=chunk.heading,
            url=chunk.url,
            license=chunk.license,
            distance=0.1,
        )
        for chunk in chunk_text_sources(3, list(book.text_sources))
    ]

    prompt = build_prompt(book, chunks)

    for chunk in chunks:
        assert f"[{chunk.id}]" in prompt
        assert chunk.content in prompt

    lowered = prompt.lower()
    assert "only" in lowered
    assert "never invent an id" in lowered
    assert "uncovered" in lowered
    assert "do not use anything you know about this book" in lowered
    assert book.title in prompt


def test_prompt_passages_are_recoverable() -> None:
    """The prompt's passage block parses back to the chunks it presented.

    Guards the test helper as much as the prompt: if the format drifts,
    `grounded_responder` would silently start citing nothing and the
    traceability test would pass vacuously.
    """
    book = assign_fake_ids(build_dune(), book_id=3)
    chunks = [
        RetrievedChunk(
            id=chunk.id,
            book_id=chunk.book_id,
            content=chunk.content,
            source=chunk.source,
            kind=chunk.kind,
            heading=chunk.heading,
            url=chunk.url,
            license=chunk.license,
            distance=0.1,
        )
        for chunk in chunk_text_sources(3, list(book.text_sources))
    ]

    parsed = parse_prompt_passages(build_prompt(book, chunks))

    assert set(parsed) == {chunk.id for chunk in chunks}
    for chunk in chunks:
        assert parsed[chunk.id] == chunk.content


# ---------------------------------------------------------------------------
# `verify_claims` in isolation
# ---------------------------------------------------------------------------


def _chunk(chunk_id: str, book_id: int = 1) -> RetrievedChunk:
    """A minimal retrieved chunk, for verifier tests."""
    return RetrievedChunk(
        id=chunk_id,
        book_id=book_id,
        content="Some passage text.",
        source="wikipedia",
        kind="reception",
        heading="Reception",
        url=None,
        license=None,
        distance=0.0,
    )


def test_verify_drops_uncited_and_unknown_claims() -> None:
    """Only claims citing a retrieved chunk survive verification."""
    chunks = [_chunk("b1:s1:0"), _chunk("b1:s2:0")]
    claims = [
        SummaryClaim.model_construct(text="Cited.", chunk_ids=["b1:s1:0"]),
        SummaryClaim.model_construct(text="Uncited.", chunk_ids=[]),
        SummaryClaim.model_construct(text="Invented.", chunk_ids=["b9:s9:9"]),
        SummaryClaim.model_construct(text="   ", chunk_ids=["b1:s2:0"]),
    ]

    verified = verify_claims(claims, chunks)

    assert [claim.text for claim in verified] == ["Cited."]


def test_verify_preserves_order_and_strips_whitespace() -> None:
    """Surviving claims keep reading order, with text tidied."""
    chunks = [_chunk("b1:s1:0"), _chunk("b1:s2:0")]
    claims = [
        SummaryClaim.model_construct(text="  First.  ", chunk_ids=["b1:s2:0"]),
        SummaryClaim.model_construct(text="Second.", chunk_ids=["b1:s1:0"]),
    ]

    verified = verify_claims(claims, chunks)

    assert [claim.text for claim in verified] == ["First.", "Second."]


# ---------------------------------------------------------------------------
# Ingestion and caching
# ---------------------------------------------------------------------------


async def test_ingest_is_idempotent(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """Re-ingesting replaces a book's chunks instead of duplicating them."""
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, _ = await _make_service(chroma_client, grounded_responder)
    store = ChromaVectorStore(chroma_client)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        first = await service.ingest(book)
        second = await service.ingest(book)

    assert first == second
    assert await store.count_for_book(book_id) == first


async def test_book_without_passages_gets_no_summary(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A book with an empty corpus reports unavailable without calling the model.

    Routine for Romanian editions: the catalogs hold a bare record and
    Wikipedia has no article. The model is never invoked, because there
    would be nothing for it to ground an answer in.
    """
    bare = Book(
        normalized_key="baltagul|mihail sadoveanu",
        title="Baltagul",
        author="Mihail Sadoveanu",
        metadata_found=False,
    )
    (book_id,) = await _persist(db_session_factory, bare)
    service, _, ai_client = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert summary.available is False
    assert ai_client.calls == []


async def test_summary_is_cached_and_reused(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A second request serves the stored summary without calling the model."""
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, ai_client = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        first = await service.summarize(db, book)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        second = await service.summarize(db, book)

    assert len(ai_client.calls) == 1
    assert second.available
    assert second.text == first.text
    assert [claim.text for claim in second.claims] == [claim.text for claim in first.claims]


async def test_summary_is_regenerated_when_the_corpus_is_refreshed(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A summary older than its sources is rebuilt, not served.

    Chunk ids are regenerated on re-ingest, so a summary written before a
    refresh may cite chunks that no longer exist — its citations would be
    untappable and its content might describe passages that are gone.
    """
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, ai_client = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        await service.summarize(db, book)

    # Module 4 re-fetches the sources: `sources_fetched_at` moves past
    # `summary_generated_at`.
    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        book.sources_fetched_at = datetime.utcnow() + timedelta(minutes=5)
        await db.commit()

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        summary = await service.summarize(db, book)

    assert len(ai_client.calls) == 2
    assert summary.available


async def test_unavailable_summaries_are_not_cached(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """A failed generation is retried next time rather than pinned.

    Caching "no summary" would deny the book one until its sources next
    expire, which is up to 30 days for a book that has metadata.
    """
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, ai_client = await _make_service(chroma_client, lambda _prompt: "not usable output")

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        await service.summarize(db, book)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        second = await service.summarize(db, book)
        assert book.summary_json is None

    assert second.available is False
    assert len(ai_client.calls) == 2


async def test_generation_uses_the_configured_llm_in_json_mode(
    db_session_factory: async_sessionmaker[AsyncSession],
    chroma_client: chromadb.api.ClientAPI,
) -> None:
    """The summary call goes to `Settings.llm_model`, never a hardcoded name.

    The provider switch is config-driven — see the "Architecture pivot"
    decision. A hardcoded model here would break the Ollama fallback
    silently.
    """
    dune = build_dune()
    (book_id,) = await _persist(db_session_factory, dune)
    service, _, ai_client = await _make_service(chroma_client, grounded_responder)

    async with db_session_factory() as db:
        book = await db.get(Book, book_id)
        assert book is not None
        await service.summarize(db, book)

    call = ai_client.calls[0]
    assert call.model == get_settings().llm_model
    assert call.format == "json"
    assert call.options is not None
    assert call.options["temperature"] == 0.0

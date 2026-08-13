"""Retrieval-augmented summary generation.

The pipeline, end to end:

1. **Ingest** — a book's cached `TextSource` passages are chunked
   (`chunking.py`), embedded locally with `nomic-embed-text`
   (`embeddings.py`), and written to ChromaDB (`vector_store.py`).
   Ingestion is idempotent: the book's existing chunks are dropped first,
   so a corpus refreshed after the Module 4 TTL expired cannot leave
   stale chunks behind to be cited alongside the new ones.
2. **Retrieve** — one similarity search per aspect we want the summary to
   cover, each **filtered on `book_id`**, unioned and deduplicated. The
   filter is mandatory and enforced structurally in the vector store; see
   that module's docstring for why it is not left to the prompt.
3. **Synthesize** — the retrieved chunks go to the summary model
   (`openai/gpt-oss-120b` on Groq by default, Llama 3.2 locally when
   `ai_provider="ollama"`) in JSON mode, with a prompt that forbids
   outside knowledge and requires a citation per statement.
4. **Verify** — every returned claim is checked against the chunks that
   were actually retrieved. A claim citing nothing, or citing an id that
   was not in the context, is dropped rather than shown.

Step 4 is the part that makes the guarantee real. A prompt asking a model
not to hallucinate is a request, not a constraint — models comply most of
the time and the failures are exactly the confident, plausible ones a
reader cannot spot. Verifying citations against the retrieved set turns
"we asked it to cite" into "it cited something that exists", which is
checkable, and which the Module 5 test suite checks statement by
statement.

What verification does *not* claim: that a claim's text is faithful to
the chunk it cites. That is a semantic judgement, and asserting it would
need a second model whose own errors we could not check either. The
honest boundary is that every statement is traceable to a real passage of
this book, and the client shows that passage so a reader can judge for
themselves.
"""

import json
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalServiceUnavailable
from app.models.book import Book
from app.schemas.summary import BookSummary, SourceReview, SummaryClaim
from app.services.chunking import chunk_text_sources
from app.services.embeddings import EmbeddingClient, get_embedding_client
from app.services.groq_client import get_active_ai_client
from app.services.ollama_client import OllamaClient
from app.services.vector_store import RetrievedChunk, VectorStore, get_vector_store

logger = structlog.get_logger(__name__)

# The aspects a summary should cover, each retrieved for separately.
#
# One query for "tell me about this book" retrieves whatever the corpus
# holds most of — for a book with a long Wikipedia plot section and two
# lines of reception, that is plot, every time, and the critical opinion
# never surfaces. Querying per aspect and unioning gives each a chance at
# the context window. The labels are also what the model is told to report
# as `uncovered` when the passages do not support one.
RETRIEVAL_ASPECTS: tuple[tuple[str, str], ...] = (
    ("premise", "What is this book about? Its plot, premise, characters and setting."),
    ("themes", "The book's themes, ideas, genre and writing style."),
    ("reception", "How critics and reviewers received the book; praise, criticism, awards."),
)

# Readable labels for a passage that has no section heading of its own.
_KIND_LABELS: dict[str, str] = {
    "description": "Publisher description",
    "subjects": "Subjects",
    "plot": "Plot summary",
    "themes": "Themes and style",
    "reception": "Critical reception",
}


class RagService:
    """Builds cited summaries from a book's cached passages.

    Every collaborator is injected so the whole pipeline can run against
    fakes in tests — no network, no Ollama, no Groq.
    """

    def __init__(
        self,
        embeddings: EmbeddingClient,
        vector_store: VectorStore,
        ai_client: OllamaClient,
        settings: Settings,
    ) -> None:
        self._embeddings = embeddings
        self._store = vector_store
        self._ai = ai_client
        self._settings = settings

    async def ingest(self, book: Book) -> int:
        """Chunks, embeds and stores a book's passages.

        Idempotent: any chunks already stored for the book are deleted
        first, so re-ingesting after a source refresh replaces the corpus
        instead of duplicating it.

        Args:
            book: The book whose `text_sources` to ingest.

        Returns:
            The number of chunks stored. `0` when the book has no
            passages, which is a normal outcome for an edition the
            catalogs cover poorly.
        """
        chunks = chunk_text_sources(
            book.id,
            list(book.text_sources),
            target_tokens=self._settings.rag_chunk_target_tokens,
            overlap_tokens=self._settings.rag_chunk_overlap_tokens,
        )

        # Delete unconditionally, including when there is nothing to write:
        # a book whose sources were refreshed down to nothing must not keep
        # serving the previous fetch's chunks.
        await self._store.delete_book(book.id)
        if not chunks:
            logger.info("ingest_empty_corpus", book_id=book.id)
            return 0

        vectors = await self._embeddings.embed([chunk.content for chunk in chunks])
        await self._store.upsert(chunks, vectors)
        logger.info("book_ingested", book_id=book.id, chunks=len(chunks))
        return len(chunks)

    async def summarize(self, db: AsyncSession, book: Book) -> BookSummary:
        """Returns the book's cited summary, generating it if needed.

        A stored summary is reused unless the book's passages have been
        re-fetched since it was written, in which case it describes a
        corpus that no longer exists and is regenerated.

        Args:
            db: The session to persist the generated summary through.
            book: The book to summarize, with `text_sources` loaded.

        Returns:
            The summary. `available=False` when the book has no passages
            to retrieve over, or when no generated claim survived
            verification — the client falls back to the publisher's blurb.

        Raises:
            ExternalServiceUnavailable: If the embedding backend or the
                summary model could not be reached. Propagated rather than
                swallowed so the client can retry, instead of caching an
                outage as "this book has no summary".
        """
        cached = _load_cached_summary(book)
        if cached is not None:
            logger.info("summary_cache_hit", book_id=book.id)
            return cached

        if not book.text_sources:
            logger.info("summary_no_corpus", book_id=book.id)
            return BookSummary(book_id=book.id, available=False)

        # Re-ingest when the store has nothing for this book. Covers the
        # first summary, and a corpus refreshed by Module 4 since the last
        # one — `_load_cached_summary` has already rejected the stale
        # summary by that point, so arriving here means a rebuild is due.
        if await self._store.count_for_book(book.id) == 0:
            await self.ingest(book)

        chunks = await self._retrieve(book.id)
        if not chunks:
            logger.info("summary_no_chunks_retrieved", book_id=book.id)
            return BookSummary(book_id=book.id, available=False)

        summary = await self._synthesize(book, chunks)
        await _store_summary(db, book, summary)
        return summary

    async def _retrieve(self, book_id: int) -> list[RetrievedChunk]:
        """Retrieves context chunks for every aspect, deduplicated.

        Each aspect is embedded and searched separately, and the results
        are unioned in aspect order — so the premise gets first call on the
        context budget, then themes, then reception, rather than one
        aspect crowding the others out.

        Args:
            book_id: The only book whose chunks may be retrieved. Passed
                straight to the store's mandatory filter.

        Returns:
            The deduplicated chunks, capped at
            `Settings.rag_max_context_chunks`.
        """
        queries = [query for _, query in RETRIEVAL_ASPECTS]
        vectors = await self._embeddings.embed(queries)

        seen: dict[str, RetrievedChunk] = {}
        for vector in vectors:
            found = await self._store.query(book_id, vector, self._settings.rag_retrieval_top_k)
            for chunk in found:
                # First hit wins: a chunk retrieved for several aspects
                # keeps the rank of the aspect that wanted it most.
                if chunk.id not in seen:
                    seen[chunk.id] = chunk

        chunks = list(seen.values())[: self._settings.rag_max_context_chunks]
        logger.info("retrieval_complete", book_id=book_id, chunks=len(chunks))
        return chunks

    async def _synthesize(self, book: Book, chunks: list[RetrievedChunk]) -> BookSummary:
        """Generates and verifies the summary from the retrieved chunks.

        Args:
            book: The book being summarized.
            chunks: The retrieved context.

        Returns:
            The verified summary. `available=False` when no claim survived
            verification.

        Raises:
            ExternalServiceUnavailable: If the summary model is unreachable.
        """
        prompt = build_prompt(book, chunks)
        raw = await self._ai.generate(
            model=self._settings.llm_model,
            prompt=prompt,
            format="json",
            options={
                "num_predict": self._settings.rag_max_output_tokens,
                # Deterministic: this is an extraction-and-grounding task,
                # and sampling variety here shows up as claims drifting
                # away from the passages that are supposed to support them.
                "temperature": 0.0,
            },
        )

        parsed = _parse_response(raw)
        if parsed is None:
            logger.warning("summary_unparsable", book_id=book.id, raw_preview=raw[:200])
            return BookSummary(book_id=book.id, available=False)

        claims, uncovered = parsed
        verified = verify_claims(claims, chunks)

        if not verified:
            logger.warning(
                "summary_no_verified_claims",
                book_id=book.id,
                generated_claims=len(claims),
            )
            return BookSummary(book_id=book.id, available=False)

        reviews = _cited_reviews(verified, chunks)
        logger.info(
            "summary_generated",
            book_id=book.id,
            claims=len(verified),
            dropped=len(claims) - len(verified),
            cited_chunks=len(reviews),
        )

        return BookSummary(
            book_id=book.id,
            available=True,
            text=" ".join(claim.text for claim in verified),
            claims=verified,
            reviews=reviews,
            uncovered=uncovered,
            model=self._settings.llm_model,
            generated_at=datetime.utcnow().isoformat(),
        )


def build_prompt(book: Book, chunks: list[RetrievedChunk]) -> str:
    """Builds the anti-hallucination synthesis prompt.

    Three things in here do real work, beyond politeness:

    - **The passages are numbered with the ids the model must cite**, so a
      citation is a token it has seen rather than one it invents. Invented
      ids still happen, and verification catches them — but making the
      right answer easy reduces how often that has to fire.
    - **Outside knowledge is forbidden explicitly**, because these are
      famous books. A model asked to summarize *Dune* from three thin
      passages will happily supply the rest from memory, and the result is
      indistinguishable from grounded text until you check it.
    - **Gaps have somewhere to go.** Without `uncovered`, a model told to
      cover reception it has no passages for will invent reception. Given
      a place to report the gap, it reports it.

    Args:
        book: The book being summarized, for title/author context.
        chunks: The retrieved passages, in context order.

    Returns:
        The prompt string.
    """
    passages = "\n\n".join(
        f"[{chunk.id}] ({chunk.source} — {_passage_label(chunk)})\n{chunk.content}"
        for chunk in chunks
    )
    aspect_list = "\n".join(f"- {name}: {query}" for name, query in RETRIEVAL_ASPECTS)
    author = f" by {book.author}" if book.author else ""

    return f"""You are writing a short, factual summary of the book "{book.title}"{author}.

You may use ONLY the numbered passages below. They are the entirety of \
what you know about this book.

PASSAGES
--------
{passages}

ASPECTS to cover, if and only if the passages support them:
{aspect_list}

RULES
-----
1. Every sentence you write must be supported by at least one passage, and \
must cite the id(s) of the passage(s) supporting it.
2. Cite only ids that appear above, exactly as written (for example \
"{chunks[0].id}"). Never invent an id.
3. Do NOT use anything you know about this book from outside these \
passages. You may well recognize this title — ignore what you recall. If \
it is not in the passages above, it does not go in the summary.
4. If the passages do not cover one of the aspects, put that aspect's name \
in "uncovered" and write nothing about it. Do not guess, and do not pad \
with generalities that would be true of any book.
5. Write between 3 and 6 sentences in total, each a single complete \
statement. Plain descriptive prose, no marketing language.
6. Do not mention the passages, the ids, or these instructions in the \
sentence text itself.

Reply with JSON in exactly this shape, and nothing else:
{{"claims": [{{"text": "<one sentence>", "chunk_ids": ["<id>", ...]}}], \
"uncovered": ["<aspect name>", ...]}}"""


def verify_claims(claims: list[SummaryClaim], chunks: list[RetrievedChunk]) -> list[SummaryClaim]:
    """Drops every claim not traceable to a retrieved chunk.

    This is the enforcement behind the prompt's citation rule. A claim
    survives only if it cites at least one id that was actually in the
    context — so an invented citation, a citation to a chunk from another
    book, and a claim with no citation at all are all removed rather than
    displayed.

    Citations that do not resolve are stripped from surviving claims too,
    so a claim citing one real and one invented id keeps only the real
    one. That leaves every id in the output resolvable by the client,
    which is what makes the citations tappable.

    Args:
        claims: The claims as returned by the model.
        chunks: The chunks that were actually retrieved and sent as context.

    Returns:
        The claims that are fully traceable, in their original order.
    """
    known = {chunk.id for chunk in chunks}
    verified: list[SummaryClaim] = []

    for claim in claims:
        text = claim.text.strip()
        if not text:
            continue

        resolved = [chunk_id for chunk_id in claim.chunk_ids if chunk_id in known]
        if not resolved:
            logger.warning(
                "claim_dropped_unsupported",
                claim_preview=text[:120],
                cited=claim.chunk_ids,
            )
            continue

        if len(resolved) != len(claim.chunk_ids):
            logger.warning(
                "claim_citations_partially_invalid",
                claim_preview=text[:120],
                cited=claim.chunk_ids,
                resolved=resolved,
            )

        verified.append(SummaryClaim(text=text, chunk_ids=resolved))

    return verified


def _parse_response(raw: str) -> tuple[list[SummaryClaim], list[str]] | None:
    """Parses the model's JSON reply into claims and uncovered aspects.

    Tolerant of the usual JSON-mode noise — a code fence, or prose either
    side of the object — but not of a reply with no object at all.

    Args:
        raw: The model's raw response text.

    Returns:
        The claims and uncovered aspect names, or `None` if the reply
        could not be parsed as the expected shape.
    """
    payload = _extract_json_object(raw)
    if payload is None:
        return None

    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return None

    claims: list[SummaryClaim] = []
    for entry in raw_claims:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        chunk_ids = entry.get("chunk_ids")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(chunk_ids, list):
            continue
        # Built directly rather than through the model so an empty
        # citation list survives to `verify_claims`, which logs why it was
        # dropped. `SummaryClaim` itself forbids an empty list.
        resolved_ids = [str(chunk_id) for chunk_id in chunk_ids if isinstance(chunk_id, str)]
        claims.append(SummaryClaim.model_construct(text=text, chunk_ids=resolved_ids))

    uncovered_raw = payload.get("uncovered")
    uncovered = (
        [str(item) for item in uncovered_raw if isinstance(item, str)]
        if isinstance(uncovered_raw, list)
        else []
    )

    return claims, uncovered


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    """Pulls the first JSON object out of a model reply.

    Args:
        raw: The raw response text.

    Returns:
        The decoded object, or `None` if there is no parsable one.
    """
    text = raw.strip()
    if not text:
        return None

    try:
        decoded = json.loads(text)
        return decoded if isinstance(decoded, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        decoded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _cited_reviews(claims: list[SummaryClaim], chunks: list[RetrievedChunk]) -> list[SourceReview]:
    """Builds the citation list, in first-citation order.

    Only chunks a surviving claim actually points at are included:
    retrieved-but-uncited chunks are context the model chose not to use,
    and listing them as sources would imply the summary rests on them.

    Args:
        claims: The verified claims.
        chunks: The retrieved chunks, to look citations up in.

    Returns:
        One `SourceReview` per cited chunk, ordered by first citation.
    """
    by_id = {chunk.id: chunk for chunk in chunks}
    reviews: list[SourceReview] = []
    seen: set[str] = set()

    for claim in claims:
        for chunk_id in claim.chunk_ids:
            if chunk_id in seen:
                continue
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            seen.add(chunk_id)
            reviews.append(
                SourceReview(
                    id=chunk.id,
                    source=chunk.source,
                    source_title=_passage_label(chunk),
                    excerpt=chunk.content,
                    url=chunk.url,
                    license=chunk.license,
                )
            )

    return reviews


def _passage_label(chunk: RetrievedChunk) -> str:
    """Returns a readable label for a passage.

    Args:
        chunk: The chunk to label.

    Returns:
        Its section heading where the source had one, otherwise a label
        for the kind of passage it is.
    """
    if chunk.heading:
        return chunk.heading
    return _KIND_LABELS.get(chunk.kind, chunk.kind.replace("_", " ").capitalize())


def _load_cached_summary(book: Book) -> BookSummary | None:
    """Returns the stored summary if it still describes the current corpus.

    A summary is invalidated by its book's passages being re-fetched: the
    chunk ids it cites are regenerated on ingest, so citations from before
    a refresh may point at chunks that no longer exist. Comparing the two
    timestamps catches that without needing an explicit invalidation call
    anywhere in Module 4's write path.

    Args:
        book: The book whose stored summary to check.

    Returns:
        The cached summary, or `None` if there is none or it is stale.
    """
    if not book.summary_json or book.summary_generated_at is None:
        return None

    if book.sources_fetched_at is not None and book.summary_generated_at < book.sources_fetched_at:
        logger.info("summary_stale", book_id=book.id)
        return None

    try:
        return BookSummary.model_validate(book.summary_json)
    except ValueError:
        # A payload written by an older build of this schema. Regenerating
        # is cheap and always correct; failing the request would not be.
        logger.warning("summary_payload_invalid", book_id=book.id)
        return None


async def _store_summary(db: AsyncSession, book: Book, summary: BookSummary) -> None:
    """Persists a generated summary on its book.

    Only a usable summary is stored. An `available=False` result is left
    unwritten on purpose: it usually means the model was having a bad
    moment or the corpus was momentarily thin, and caching that would deny
    the book a summary until its sources next expire.

    Args:
        db: The session to write through.
        book: The book to store the summary on.
        summary: The generated summary.
    """
    if not summary.available:
        return

    book.summary_json = summary.model_dump()
    book.summary_generated_at = datetime.utcnow()
    await db.commit()


def build_rag_service() -> RagService:
    """Factory for the production `RagService`.

    Returns:
        A service embedding locally through Ollama, storing vectors in the
        local Chroma directory, and generating summaries through whichever
        backend `Settings.ai_provider` selects.
    """
    settings = get_settings()
    return RagService(
        embeddings=get_embedding_client(),
        vector_store=get_vector_store(),
        ai_client=get_active_ai_client(),
        settings=settings,
    )


__all__ = [
    "ExternalServiceUnavailable",
    "RagService",
    "build_prompt",
    "build_rag_service",
    "verify_claims",
]

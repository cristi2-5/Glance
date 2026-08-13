"""Splitting cached passages into overlapping chunks for retrieval.

The corpus is the `TextSource` rows gathered in Module 4 — publisher
descriptions, subject lists, plot summaries and, above all, Wikipedia
*Reception* sections. (There are no scraped reviews and never will be: no
mainstream review site permits it. See the "Content sources" decision in
CLAUDE.md.)

Two properties matter for what Module 5 does with these chunks:

1. **Chunks are the citation unit.** A generated claim points at chunk
   ids, and the client renders each cited chunk as an excerpt with a link
   back to its source. So a chunk has to be self-contained enough to read
   on its own, and it has to carry its provenance (`source`, `url`,
   `license`) rather than just its text.
2. **Chunk ids must be stable and book-scoped.** `book_id` is baked into
   the id (`b{book_id}:s{source_id}:{index}`) as well as into the
   metadata, so a chunk can be traced to its book from the id alone —
   which is what makes the cross-book leak assertion in `vector_store.py`
   possible without a second lookup.

Sizing is deliberately approximate. Counting real tokens would mean
adding a tokenizer dependency for the sake of a boundary that does not
need to be exact — the retrieval quality difference between a 480-token
and a 520-token chunk is nil. `_TOKENS_PER_WORD` converts the configured
token budget into a word budget instead; the ratio is the usual English
figure and is a touch conservative, so chunks land at or under target
rather than over.
"""

import re

import structlog

from app.models.book import TextSource

logger = structlog.get_logger(__name__)

# Words-to-tokens ratio for English prose. Subword tokenizers split longer
# and inflected words, so one word averages slightly more than one token.
# Erring high keeps chunks at or under the configured token budget.
_TOKENS_PER_WORD = 1.3

# Sentence boundary: a terminator optionally followed by a closing quote
# or bracket, then whitespace. Written as two fixed-width lookbehind
# alternatives because Python's `re` rejects a variable-width one.
# Deliberately simple — a missed boundary costs a slightly uneven chunk,
# not a broken one, and this corpus is edited encyclopaedic prose.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?][\"')\]])\s+|(?<=[.!?])\s+")


class Chunk:
    """One retrievable passage of text, with the provenance to cite it.

    Attributes:
        id: Stable identifier, `b{book_id}:s{source_id}:{index}`. Encodes
            the book, so a chunk is traceable without a second lookup.
        book_id: The book this chunk belongs to. The mandatory retrieval
            filter keys on this — see `vector_store.py`.
        text_source_id: The `TextSource` row the chunk was cut from.
        source: Which official source produced it (`SourceName` value).
        kind: What the passage is (`SourceKind` value) — description,
            subjects, plot, or reception.
        heading: The section heading it came from, when the source has
            sections (Wikipedia); `None` otherwise.
        url: A link back to the originating page, for citation.
        license: The licence the text is published under.
        content: The chunk text itself.
    """

    __slots__ = (
        "id",
        "book_id",
        "text_source_id",
        "source",
        "kind",
        "heading",
        "url",
        "license",
        "content",
    )

    def __init__(
        self,
        *,
        id: str,
        book_id: int,
        text_source_id: int,
        source: str,
        kind: str,
        heading: str | None,
        url: str | None,
        license: str | None,
        content: str,
    ) -> None:
        self.id = id
        self.book_id = book_id
        self.text_source_id = text_source_id
        self.source = source
        self.kind = kind
        self.heading = heading
        self.url = url
        self.license = license
        self.content = content

    def metadata(self) -> dict[str, str | int]:
        """Builds the Chroma metadata payload for this chunk.

        `book_id` is stored as an `int`, matching the type used in the
        retrieval filter — Chroma compares metadata values by exact type,
        so a chunk written with a string `book_id` would be invisible to
        an `int` filter (and vice versa), silently and with no error.

        Returns:
            The metadata dict. Chroma rejects `None` values, so optional
            fields collapse to empty strings and are restored on read.
        """
        return {
            "book_id": self.book_id,
            "text_source_id": self.text_source_id,
            "source": self.source,
            "kind": self.kind,
            "heading": self.heading or "",
            "url": self.url or "",
            "license": self.license or "",
        }

    def __repr__(self) -> str:
        return f"Chunk(id={self.id!r}, book_id={self.book_id}, kind={self.kind!r})"


def chunk_text_sources(
    book_id: int,
    sources: list[TextSource],
    target_tokens: int = 500,
    overlap_tokens: int = 50,
) -> list[Chunk]:
    """Splits a book's cached passages into overlapping chunks.

    Each `TextSource` is chunked independently. Passages from different
    sources are never merged into one chunk: a chunk is a citation unit,
    and one that straddled a Wikipedia reception paragraph and a Google
    Books blurb could not be attributed to either.

    Args:
        book_id: The book these passages belong to. Baked into every
            chunk id and every metadata payload.
        sources: The `TextSource` rows to chunk.
        target_tokens: Approximate token budget per chunk.
        overlap_tokens: Approximate token overlap between neighbours, so a
            statement spanning a boundary survives in at least one chunk
            whole.

    Returns:
        The chunks, in source order then document order. Empty when
        `sources` is empty or holds nothing but whitespace.

    Raises:
        ValueError: If `target_tokens` is not positive, or `overlap_tokens`
            is negative or not smaller than `target_tokens` (which would
            make the window advance by zero and never terminate).
    """
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive.")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must not be negative.")
    if overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be smaller than target_tokens.")

    target_words = max(1, int(target_tokens / _TOKENS_PER_WORD))
    overlap_words = int(overlap_tokens / _TOKENS_PER_WORD)

    chunks: list[Chunk] = []
    for source in sources:
        pieces = _split_passage(source.content, target_words, overlap_words)
        for index, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    id=f"b{book_id}:s{source.id}:{index}",
                    book_id=book_id,
                    text_source_id=source.id,
                    source=source.source,
                    kind=source.kind,
                    heading=source.heading,
                    url=source.url,
                    license=source.license,
                    content=piece,
                )
            )

    logger.info(
        "chunked_book",
        book_id=book_id,
        passages=len(sources),
        chunks=len(chunks),
        target_tokens=target_tokens,
    )
    return chunks


def _split_passage(content: str, target_words: int, overlap_words: int) -> list[str]:
    """Splits one passage into overlapping, sentence-aligned windows.

    Sentences are kept whole wherever possible, because a chunk cut
    mid-sentence reads badly as a cited excerpt on the client. A single
    sentence longer than the whole budget is the one exception: it is
    emitted on its own, over budget, rather than being cut into fragments
    that cite nothing intelligible.

    Args:
        content: The passage text.
        target_words: Word budget per chunk.
        overlap_words: Words of overlap between consecutive chunks.

    Returns:
        The chunk texts, in document order.
    """
    text = content.strip()
    if not text:
        return []

    sentences = [piece.strip() for piece in _SENTENCE_BOUNDARY.split(text) if piece.strip()]
    if not sentences:
        return []

    # Word counts are computed once per sentence and reused: the window
    # walk revisits sentences on every overlap, and re-splitting them each
    # time would make this quadratic on long Reception sections.
    lengths = [len(sentence.split()) for sentence in sentences]

    chunks: list[str] = []
    start = 0
    while start < len(sentences):
        end = start
        budget = 0
        while end < len(sentences) and budget + lengths[end] <= target_words:
            budget += lengths[end]
            end += 1

        # A single sentence over the whole budget: take it alone rather
        # than emitting an empty chunk and looping forever.
        if end == start:
            end = start + 1
            budget = lengths[start]

        chunks.append(" ".join(sentences[start:end]))

        if end >= len(sentences):
            break

        start = _overlap_start(lengths, start, end, overlap_words)

    return chunks


def _overlap_start(lengths: list[int], start: int, end: int, overlap_words: int) -> int:
    """Finds where the next window begins, given the overlap budget.

    Walks back from `end` while the trailing sentences fit in the overlap
    budget. The result is always strictly greater than `start`, so the
    window advances even when one sentence fills the entire budget —
    without that floor, a long sentence would make the loop spin forever.

    Args:
        lengths: Word count per sentence.
        start: Index the current window began at.
        end: Index one past the current window's last sentence.
        overlap_words: Word budget for the overlap.

    Returns:
        The starting index of the next window.
    """
    if overlap_words <= 0:
        return end

    next_start = end
    carried = 0
    while next_start > start + 1 and carried + lengths[next_start - 1] <= overlap_words:
        next_start -= 1
        carried += lengths[next_start]

    return next_start

"""Tests for splitting cached passages into retrievable chunks."""

import pytest

from app.models.book import SourceKind, SourceName, TextSource
from app.services.chunking import chunk_text_sources
from tests.fixtures.rag_corpus import assign_fake_ids, build_dune


def _passage(content: str, source_id: int = 1) -> TextSource:
    """Builds a standalone `TextSource` with the given content."""
    passage = TextSource(
        source=SourceName.WIKIPEDIA.value,
        kind=SourceKind.RECEPTION.value,
        heading="Reception",
        content=content,
        url="https://wikipedia.example.test/Book",
        license="CC BY-SA 4.0",
    )
    passage.id = source_id
    return passage


def test_chunk_ids_are_unique_and_carry_the_book() -> None:
    """Chunk ids must be unique and encode their book.

    The id is what a generated claim cites, and what the leak guard in
    `vector_store.py` checks — a duplicate id would make two different
    passages indistinguishable as citations.
    """
    book = assign_fake_ids(build_dune(), book_id=7)

    chunks = chunk_text_sources(7, list(book.text_sources))

    ids = [chunk.id for chunk in chunks]
    assert len(ids) == len(set(ids))
    assert all(chunk.id.startswith("b7:") for chunk in chunks)
    assert all(chunk.book_id == 7 for chunk in chunks)


def test_chunks_carry_their_provenance() -> None:
    """Every chunk keeps the source, url and licence needed to cite it."""
    book = assign_fake_ids(build_dune(), book_id=1)

    chunks = chunk_text_sources(1, list(book.text_sources))
    reception = [chunk for chunk in chunks if chunk.kind == SourceKind.RECEPTION.value]

    assert reception, "the fixture has a reception passage"
    for chunk in reception:
        assert chunk.source == SourceName.WIKIPEDIA.value
        assert chunk.url == "https://wikipedia.example.test/Dune_(novel)"
        assert chunk.license == "CC BY-SA 4.0"
        assert chunk.heading == "Reception"


def test_passages_from_different_sources_are_never_merged() -> None:
    """A chunk belongs to exactly one passage.

    A chunk straddling two sources could not be attributed to either, and
    attribution is the whole point of the citation.
    """
    book = assign_fake_ids(build_dune(), book_id=1)

    chunks = chunk_text_sources(1, list(book.text_sources), target_tokens=5000)

    # Even with a budget far larger than the whole corpus, each passage
    # produced its own chunk rather than being combined.
    assert len(chunks) == len(book.text_sources)
    assert len({chunk.text_source_id for chunk in chunks}) == len(book.text_sources)


def test_long_passage_is_split_with_overlap() -> None:
    """A passage over budget splits, and neighbours share content.

    The overlap is what keeps a statement spanning a boundary retrievable
    whole, instead of surviving only as two halves that each cite badly.
    """
    sentences = [f"Sentence number {index} says something distinct." for index in range(60)]
    passage = _passage(" ".join(sentences))

    chunks = chunk_text_sources(1, [passage], target_tokens=60, overlap_tokens=20)

    assert len(chunks) > 1
    first_words = set(chunks[0].content.split())
    second_words = set(chunks[1].content.split())
    assert first_words & second_words, "consecutive chunks must overlap"


def test_chunks_respect_the_token_budget() -> None:
    """Chunks stay near the configured budget rather than growing unbounded."""
    sentences = [f"Sentence number {index} says something distinct." for index in range(60)]
    passage = _passage(" ".join(sentences))

    chunks = chunk_text_sources(1, [passage], target_tokens=100, overlap_tokens=10)

    # The budget is approximate (words converted to tokens), so this
    # asserts the order of magnitude, not an exact cut.
    for chunk in chunks:
        assert len(chunk.content.split()) <= 100


def test_sentences_are_kept_whole() -> None:
    """Chunks end on sentence boundaries, so an excerpt reads on its own."""
    passage = _passage(" ".join(f"This is sentence {index}." for index in range(40)))

    chunks = chunk_text_sources(1, [passage], target_tokens=40, overlap_tokens=5)

    for chunk in chunks:
        assert chunk.content.endswith("."), chunk.content


def test_sentence_longer_than_the_budget_is_emitted_alone() -> None:
    """An over-long sentence is kept whole rather than cut into fragments.

    Also the regression guard for the window never advancing: a naive
    implementation loops forever here.
    """
    long_sentence = " ".join(["word"] * 400) + "."
    passage = _passage(long_sentence)

    chunks = chunk_text_sources(1, [passage], target_tokens=50, overlap_tokens=10)

    assert len(chunks) == 1
    assert chunks[0].content == long_sentence


def test_empty_and_whitespace_passages_produce_nothing() -> None:
    """Blank passages are dropped rather than stored as empty chunks."""
    assert chunk_text_sources(1, [_passage("")]) == []
    assert chunk_text_sources(1, [_passage("   \n  ")]) == []
    assert chunk_text_sources(1, []) == []


def test_metadata_pins_book_id_as_an_integer() -> None:
    """`book_id` must be an int in Chroma metadata.

    Chroma matches metadata by exact type, so a chunk written with a
    string `book_id` would be invisible to the int filter — silently, with
    no error and no results.
    """
    book = assign_fake_ids(build_dune(), book_id=42)

    chunk = chunk_text_sources(42, list(book.text_sources))[0]

    assert chunk.metadata()["book_id"] == 42
    assert isinstance(chunk.metadata()["book_id"], int)


def test_metadata_has_no_none_values() -> None:
    """Chroma rejects `None` metadata; optional fields collapse to "" instead."""
    passage = _passage("A sentence.")
    passage.heading = None
    passage.url = None
    passage.license = None

    chunk = chunk_text_sources(1, [passage])[0]

    assert all(value is not None for value in chunk.metadata().values())


@pytest.mark.parametrize(
    ("target", "overlap"),
    [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 200)],
)
def test_invalid_budgets_are_rejected(target: int, overlap: int) -> None:
    """Budgets that would not terminate are refused up front."""
    with pytest.raises(ValueError):
        chunk_text_sources(1, [_passage("A sentence.")], target, overlap)

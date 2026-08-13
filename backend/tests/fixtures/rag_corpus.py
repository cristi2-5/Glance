"""A fixture corpus with known content, for the Module 5 traceability tests.

The point of this file is that **every fact in it is checkable**. Each
passage carries distinctive marker phrases that appear nowhere else in
the suite, so a test can assert that a generated statement came from a
specific passage rather than from the model's own knowledge of the book.

Two books are defined, not one. *Dune* is the book under test; *Foundation*
exists purely so the retrieval filter has something to leak, and its
passages are written to be superficially similar — same genre, same
vocabulary, same shape of critical praise — because a filter is only
worth testing against a plausible near-miss. `FOUNDATION_MARKERS` lists
the phrases that must never appear in a summary of *Dune*.

The text is written for the test, not copied from Wikipedia: it needs to
be stable across article edits, and it needs marker phrases chosen for
distinctiveness rather than accuracy of prose.
"""

from app.models.book import Book, SourceKind, SourceName, TextSource

# Phrases that appear only in Dune's passages. A summary claiming any of
# these must be traceable to the passage that contains it.
DUNE_MARKERS: dict[str, str] = {
    "melange": "plot",
    "Arrakis": "plot",
    "Bene Gesserit": "plot",
    "Hugo": "reception",
    "Nebula": "reception",
    "ecological": "reception",
}

# Phrases that appear only in Foundation's passages. None of these may
# ever surface in a summary of Dune — if one does, the book_id filter
# leaked and the summary is describing the wrong book.
FOUNDATION_MARKERS: tuple[str, ...] = (
    "psychohistory",
    "Hari Seldon",
    "Trantor",
    "Galactic Empire",
    "Encyclopedia Galactica",
)


def build_dune() -> Book:
    """Builds the *Dune* fixture book with four known passages.

    Returns:
        An unsaved `Book` with `text_sources` populated. Ids are assigned
        by the caller's session on flush; tests that need ids without a
        database use `assign_fake_ids`.
    """
    book = Book(
        normalized_key="dune|frank herbert",
        title="Dune",
        author="Frank Herbert",
        description="A desert planet, a rare spice, and the empire that depends on it.",
        metadata_found=True,
    )
    book.text_sources = [
        TextSource(
            source=SourceName.GOOGLE_BOOKS.value,
            kind=SourceKind.DESCRIPTION.value,
            heading=None,
            content=(
                "Dune is a science fiction novel by Frank Herbert, first published in 1965. "
                "It is set on the desert planet Arrakis, the only source of the spice melange."
            ),
            url="https://books.example.test/dune",
            license="Google Books ToS",
        ),
        TextSource(
            source=SourceName.WIKIPEDIA.value,
            kind=SourceKind.PLOT.value,
            heading="Plot",
            content=(
                "House Atreides is granted control of Arrakis, displacing their rivals. "
                "Paul Atreides, trained in part by the Bene Gesserit sisterhood, flees into "
                "the deep desert after his family is betrayed. There he is taken in by the "
                "Fremen, the planet's native people, who have adapted to life without open "
                "water. Paul learns to ride the sandworms that produce melange, and becomes "
                "the leader the Fremen have long awaited."
            ),
            url="https://wikipedia.example.test/Dune_(novel)",
            license="CC BY-SA 4.0",
        ),
        TextSource(
            source=SourceName.WIKIPEDIA.value,
            kind=SourceKind.RECEPTION.value,
            heading="Reception",
            content=(
                "Dune won the inaugural Nebula Award for Best Novel in 1965 and shared the "
                "Hugo Award in 1966. Critics praised the depth of its ecological world-building, "
                "which was unusual for the genre at the time. Reviewers were more divided on its "
                "pacing, with several noting that the novel's middle section moves slowly. "
                "It is now widely described as one of the most influential works of science "
                "fiction ever written."
            ),
            url="https://wikipedia.example.test/Dune_(novel)",
            license="CC BY-SA 4.0",
        ),
        TextSource(
            source=SourceName.OPEN_LIBRARY.value,
            kind=SourceKind.SUBJECTS.value,
            heading=None,
            content="Science fiction, Desert ecology, Political intrigue, Coming of age.",
            url="https://openlibrary.example.test/works/dune",
            license="CC0",
        ),
    ]
    return book


def build_foundation() -> Book:
    """Builds the *Foundation* fixture book — the corpus that must not leak.

    Deliberately similar to *Dune*: same genre, same era, same shape of
    award-winning reception. A `book_id` filter that quietly stopped
    working would return these chunks for a *Dune* query and the summary
    would still read plausibly, which is exactly the failure the tests
    exist to catch.

    Returns:
        An unsaved `Book` with `text_sources` populated.
    """
    book = Book(
        normalized_key="foundation|isaac asimov",
        title="Foundation",
        author="Isaac Asimov",
        description="A mathematician predicts the fall of a galactic civilisation.",
        metadata_found=True,
    )
    book.text_sources = [
        TextSource(
            source=SourceName.GOOGLE_BOOKS.value,
            kind=SourceKind.DESCRIPTION.value,
            heading=None,
            content=(
                "Foundation is a science fiction novel by Isaac Asimov, published in 1951. "
                "It follows the mathematician Hari Seldon and the science of psychohistory."
            ),
            url="https://books.example.test/foundation",
            license="Google Books ToS",
        ),
        TextSource(
            source=SourceName.WIKIPEDIA.value,
            kind=SourceKind.PLOT.value,
            heading="Plot",
            content=(
                "Hari Seldon foresees the collapse of the Galactic Empire and the thirty "
                "thousand years of barbarism that will follow. From Trantor, the imperial "
                "capital, he establishes the Foundation at the galaxy's edge, ostensibly to "
                "compile the Encyclopedia Galactica. Its real purpose is to shorten the coming "
                "dark age to a single millennium."
            ),
            url="https://wikipedia.example.test/Foundation_(novel)",
            license="CC BY-SA 4.0",
        ),
        TextSource(
            source=SourceName.WIKIPEDIA.value,
            kind=SourceKind.RECEPTION.value,
            heading="Reception",
            content=(
                "The Foundation series received a one-off Hugo Award for Best All-Time Series "
                "in 1966. Critics admired the scale of its central conceit, psychohistory, "
                "while noting that its characters are thinly drawn by design."
            ),
            url="https://wikipedia.example.test/Foundation_(novel)",
            license="CC BY-SA 4.0",
        ),
    ]
    return book


def assign_fake_ids(book: Book, book_id: int, first_source_id: int = 1) -> Book:
    """Assigns deterministic ids to a fixture book and its passages.

    For tests that exercise chunking or synthesis without a database, and
    therefore never get ids from a flush.

    Args:
        book: The fixture book to number.
        book_id: The id to give the book.
        first_source_id: The id of its first passage; later ones increment.

    Returns:
        The same book, with ids assigned.
    """
    book.id = book_id
    for offset, source in enumerate(book.text_sources):
        source.id = first_source_id + offset
        source.book_id = book_id
    return book

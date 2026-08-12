"""Tests for the shared catalog title matcher.

The regression these guard is concrete: `token_set_ratio` scored any
candidate *containing* the query at 100, so Open Library returned
*Heretics of Dune* as the match for "Dune" and its cover, description and
subjects were merged into Dune's cache entry. Attaching another book's
content is worse than reporting none — it is confidently wrong, and it
poisons the corpus Module 5 retrieves from.
"""

import pytest

from app.services.sources.matching import MIN_TITLE_SIMILARITY, title_similarity


def _matches(query: str, candidate: str) -> bool:
    """Whether a candidate clears the shared similarity floor."""
    return title_similarity(query, candidate) >= MIN_TITLE_SIMILARITY


@pytest.mark.parametrize(
    "candidate",
    [
        "Dune",
        "dune",
        "DUNE",
        "Dune: A Novel",
        "Dune (Movie Tie-In)",
        "Dune [Illustrated Edition]",
    ],
)
def test_accepts_the_same_book_however_the_catalog_decorates_it(candidate: str) -> None:
    assert _matches("Dune", candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        "Dune Messiah",
        "Children of Dune",
        "Heretics of Dune",
        "God Emperor of Dune",
        "The Dune Encyclopedia",
    ],
)
def test_rejects_other_books_whose_title_contains_the_query(candidate: str) -> None:
    # The whole point: these all scored 100 under `token_set_ratio`.
    assert not _matches("Dune", candidate)


def test_rejects_a_longer_title_that_merely_starts_with_the_query() -> None:
    # "It" is the pathological case — a two-letter title is a substring of
    # a great many others.
    assert not _matches("It", "It Ends with Us")
    assert _matches("It", "It: A Novel")


def test_accepts_a_leading_article_the_cover_omits() -> None:
    # Romanian covers routinely drop the article the catalog records.
    assert _matches("Calatorie spre centrul pamantului", "O Calatorie Spre Centrul Pamantului")


def test_accepts_a_comma_subtitle() -> None:
    assert _matches("The Hobbit", "The Hobbit, or There and Back Again")


def test_rejects_a_different_book_in_the_same_series() -> None:
    assert not _matches(
        "Harry Potter and the Chamber of Secrets",
        "Harry Potter and the Goblet of Fire",
    )


def test_empty_titles_never_match() -> None:
    assert title_similarity("", "Dune") == 0.0
    assert title_similarity("Dune", "") == 0.0
    assert title_similarity("   ", "Dune") == 0.0

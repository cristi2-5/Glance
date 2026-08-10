"""Tests for the catalog lookups. All HTTP is mocked via respx — no real network calls."""

import httpx
import respx

from app.core.config import Settings, get_settings
from app.services.title_lookup import (
    BookCandidate,
    ChainedTitleLookup,
    GoogleBooksTitleLookup,
    LookupOutcome,
    OpenLibraryTitleLookup,
)
from tests.fakes import FakeTitleLookup

_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"
_OPEN_LIBRARY_URL = "https://openlibrary.org/search.json"


# --- Google Books -----------------------------------------------------------


async def test_google_books_parses_title_and_authors() -> None:
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {"volumeInfo": {"title": "Dune", "authors": ["Frank Herbert"]}},
                        {"volumeInfo": {"title": "No Authors Here"}},
                        {"volumeInfo": {}},  # missing title — must be skipped
                    ]
                },
            )
        )
        outcome = await lookup.search("dune frank herbert")

    assert outcome.available is True
    assert len(outcome.candidates) == 2
    assert outcome.candidates[0] == BookCandidate(title="Dune", authors=["Frank Herbert"])
    assert outcome.candidates[1].authors == []


async def test_google_books_sends_api_key_when_configured() -> None:
    settings = Settings(google_books_api_key="test-key-123")
    lookup = GoogleBooksTitleLookup(settings)

    with respx.mock:
        route = respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json={"items": []}))
        await lookup.search("dune")

    assert route.calls.last.request.url.params["key"] == "test-key-123"


async def test_google_books_omits_key_when_not_configured() -> None:
    lookup = GoogleBooksTitleLookup(Settings(google_books_api_key=None))

    with respx.mock:
        route = respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json={"items": []}))
        await lookup.search("dune")

    assert "key" not in route.calls.last.request.url.params


async def test_google_books_quota_exhausted_reports_unavailable() -> None:
    # The exact production failure: 429 must be reported as "could not ask",
    # not as "this book does not exist".
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(429))
        outcome = await lookup.search("anything")

    assert outcome.available is False
    assert outcome.candidates == []


async def test_google_books_connection_error_reports_unavailable() -> None:
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(side_effect=httpx.ConnectError("refused"))
        outcome = await lookup.search("anything")

    assert outcome.available is False


async def test_empty_query_short_circuits_without_a_request() -> None:
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        route = respx.get(_VOLUMES_URL)
        outcome = await lookup.search("   ")

    assert outcome == LookupOutcome()
    assert route.call_count == 0


# --- Open Library -----------------------------------------------------------


async def test_open_library_parses_docs() -> None:
    lookup = OpenLibraryTitleLookup(get_settings())

    with respx.mock:
        respx.get(_OPEN_LIBRARY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "docs": [
                        {
                            "title": "Around the World in Eighty Days",
                            "author_name": ["Jules Verne"],
                        },
                        {"author_name": ["Nobody"]},  # missing title — skipped
                    ]
                },
            )
        )
        outcome = await lookup.search("around the world")

    assert outcome.available is True
    assert len(outcome.candidates) == 1
    assert outcome.candidates[0].title == "Around the World in Eighty Days"


async def test_open_library_no_matches_is_available_but_empty() -> None:
    # Open Library genuinely has no Romanian edition of many titles. That is
    # an answer, not an outage.
    lookup = OpenLibraryTitleLookup(get_settings())

    with respx.mock:
        respx.get(_OPEN_LIBRARY_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        outcome = await lookup.search("Ocolul Pamantului in optzeci de zile")

    assert outcome.available is True
    assert outcome.candidates == []


# --- The chain --------------------------------------------------------------


async def test_chain_falls_through_to_the_second_catalog_when_the_first_is_down() -> None:
    down = FakeTitleLookup(available=False)
    up = FakeTitleLookup([BookCandidate(title="Dune", authors=["Frank Herbert"])])
    chain = ChainedTitleLookup([down, up])

    outcome = await chain.search("dune")

    assert outcome.available is True
    assert outcome.candidates[0].title == "Dune"
    assert down.queries == ["dune"] and up.queries == ["dune"]


async def test_chain_stops_at_the_first_catalog_that_has_matches() -> None:
    first = FakeTitleLookup([BookCandidate(title="Dune", authors=[])])
    second = FakeTitleLookup([BookCandidate(title="Never Reached", authors=[])])
    chain = ChainedTitleLookup([first, second])

    outcome = await chain.search("dune")

    assert outcome.candidates[0].title == "Dune"
    assert second.queries == [], "the second catalog must not be queried needlessly"


async def test_chain_reports_unavailable_only_when_every_catalog_is_down() -> None:
    chain = ChainedTitleLookup([FakeTitleLookup(available=False), FakeTitleLookup(available=False)])

    outcome = await chain.search("anything")

    assert outcome.available is False


async def test_chain_reports_available_when_a_catalog_answered_with_no_matches() -> None:
    chain = ChainedTitleLookup([FakeTitleLookup(available=False), FakeTitleLookup([])])

    outcome = await chain.search("obscure romanian edition")

    assert outcome.available is True
    assert outcome.candidates == []

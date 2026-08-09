"""Tests for `GoogleBooksTitleLookup`. All HTTP is mocked via respx — no real network calls."""

import httpx
import respx

from app.core.config import get_settings
from app.services.title_lookup import GoogleBooksTitleLookup

_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"


async def test_search_parses_title_and_authors() -> None:
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
        results = await lookup.search("dune frank herbert")

    assert len(results) == 2
    assert results[0].title == "Dune"
    assert results[0].authors == ["Frank Herbert"]
    assert results[1].title == "No Authors Here"
    assert results[1].authors == []


async def test_search_degrades_to_empty_list_on_http_error() -> None:
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(500))
        results = await lookup.search("anything")

    assert results == []


async def test_search_degrades_to_empty_list_on_connection_error() -> None:
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(side_effect=httpx.ConnectError("refused"))
        results = await lookup.search("anything")

    assert results == []


async def test_empty_query_short_circuits_without_a_request() -> None:
    lookup = GoogleBooksTitleLookup(get_settings())

    with respx.mock:
        route = respx.get(_VOLUMES_URL)
        results = await lookup.search("   ")

    assert results == []
    assert route.call_count == 0

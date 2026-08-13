"""Tests for the cover-image fallback chain and its two legs.

All HTTP is mocked via respx — the suite makes no real network calls.
"""

import httpx
import respx

from app.core.config import Settings, get_settings
from app.services.sources.cover_fallback import OfficialCoverFallback
from app.services.sources.open_library import fetch_cover_by_isbn
from app.services.sources.wikipedia import fetch_article_image

_ISBN_13 = "9780441013593"
_ISBN_10 = "0441013597"
_COVER_URL = f"https://covers.openlibrary.org/b/isbn/{_ISBN_13}-L.jpg"
_COVER_URL_10 = f"https://covers.openlibrary.org/b/isbn/{_ISBN_10}-L.jpg"
_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/Dune_(novel)"

_COMMONS_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/d/d5/Dune.jpg"
_LOCAL_IMAGE = "https://upload.wikimedia.org/wikipedia/en/7/7a/Dune-Frank_Herbert.jpg"


def _no_retry() -> Settings:
    """Settings with retries off, so a failure test doesn't sleep through backoff."""
    return Settings(catalog_max_retries=0)


# --- Open Library Covers API, by ISBN ---------------------------------------


async def test_open_library_cover_is_returned_when_the_image_exists() -> None:
    with respx.mock:
        route = respx.head(_COVER_URL).mock(return_value=httpx.Response(200))
        cover = await fetch_cover_by_isbn(_ISBN_13, get_settings())

    assert cover == f"{_COVER_URL}?default=false"
    assert route.called


async def test_open_library_cover_probe_asks_for_no_placeholder() -> None:
    # Without `default=false` the API answers 200 and a blank grey image for
    # every ISBN it has nothing for, which would make the probe useless and
    # cache a placeholder as if it were the book's cover.
    with respx.mock:
        route = respx.head(_COVER_URL).mock(return_value=httpx.Response(200))
        await fetch_cover_by_isbn(_ISBN_13, get_settings())

    assert route.calls[0].request.url.params["default"] == "false"


async def test_open_library_cover_absent_yields_none() -> None:
    with respx.mock:
        respx.head(_COVER_URL).mock(return_value=httpx.Response(404))
        cover = await fetch_cover_by_isbn(_ISBN_13, get_settings())

    assert cover is None


async def test_open_library_cover_follows_the_cdn_redirect() -> None:
    with respx.mock:
        respx.head(_COVER_URL).mock(
            return_value=httpx.Response(302, headers={"Location": "https://cdn.test/dune.jpg"})
        )
        respx.head("https://cdn.test/dune.jpg").mock(return_value=httpx.Response(200))
        cover = await fetch_cover_by_isbn(_ISBN_13, get_settings())

    assert cover == f"{_COVER_URL}?default=false", "the stable URL is stored, not the CDN one"


async def test_open_library_cover_timeout_yields_none_rather_than_raising() -> None:
    with respx.mock:
        respx.head(_COVER_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
        cover = await fetch_cover_by_isbn(_ISBN_13, _no_retry())

    assert cover is None


# --- Wikipedia lead image ---------------------------------------------------


def _summary(**overrides: object) -> dict[str, object]:
    """A REST summary payload carrying a Commons-hosted lead image."""
    payload: dict[str, object] = {
        "title": "Dune (novel)",
        "originalimage": {"source": _COMMONS_IMAGE, "width": 800},
    }
    payload.update(overrides)
    return payload


async def test_wikipedia_lead_image_is_returned() -> None:
    with respx.mock:
        respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=_summary()))
        image = await fetch_article_image("Dune (novel)", get_settings())

    assert image == _COMMONS_IMAGE


async def test_wikipedia_non_free_local_upload_is_still_used() -> None:
    # The common case for book articles: the cover scan is held locally
    # under fair use rather than on Commons. This build is private, so the
    # image is used and the licensing is logged, not enforced.
    payload = _summary(originalimage={"source": _LOCAL_IMAGE})
    with respx.mock:
        respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=payload))
        image = await fetch_article_image("Dune (novel)", get_settings())

    assert image == _LOCAL_IMAGE


async def test_wikipedia_falls_back_to_the_thumbnail() -> None:
    payload = {"title": "Dune (novel)", "thumbnail": {"source": _COMMONS_IMAGE}}
    with respx.mock:
        respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=payload))
        image = await fetch_article_image("Dune (novel)", get_settings())

    assert image == _COMMONS_IMAGE


async def test_wikipedia_prefers_the_original_over_the_thumbnail() -> None:
    payload = _summary(thumbnail={"source": "https://upload.wikimedia.org/thumb/320px-Dune.jpg"})
    with respx.mock:
        respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=payload))
        image = await fetch_article_image("Dune (novel)", get_settings())

    assert image == _COMMONS_IMAGE, "a phone screen is better served by the full-size file"


async def test_wikipedia_article_without_a_lead_image_yields_none() -> None:
    with respx.mock:
        respx.get(_SUMMARY_URL).mock(
            return_value=httpx.Response(200, json={"title": "Dune (novel)"})
        )
        image = await fetch_article_image("Dune (novel)", get_settings())

    assert image is None


async def test_wikipedia_article_title_is_percent_encoded_into_the_path() -> None:
    # A slash in a title would otherwise split the path segment and request
    # a different (or non-existent) article.
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/And%2FOr"
    with respx.mock:
        route = respx.get(url).mock(return_value=httpx.Response(200, json={}))
        await fetch_article_image("And/Or", get_settings())

    assert route.called


async def test_wikipedia_summary_failure_yields_none() -> None:
    with respx.mock:
        respx.get(_SUMMARY_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        image = await fetch_article_image("Dune (novel)", _no_retry())

    assert image is None


# --- The chain --------------------------------------------------------------


async def test_chain_prefers_the_freely_licensed_open_library_cover() -> None:
    # Ordering is licence-first: the CC0 image wins even though the
    # Wikipedia scan is often the better one.
    with respx.mock:
        respx.head(_COVER_URL).mock(return_value=httpx.Response(200))
        summary = respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=_summary()))
        cover = await OfficialCoverFallback(get_settings()).find_cover(
            isbn_13=_ISBN_13, isbn_10=None, wikipedia_article="Dune (novel)"
        )

    assert cover == f"{_COVER_URL}?default=false"
    assert not summary.called, "Wikipedia is only reached once Open Library has failed"


async def test_chain_falls_through_to_wikipedia() -> None:
    with respx.mock:
        respx.head(_COVER_URL).mock(return_value=httpx.Response(404))
        respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=_summary()))
        cover = await OfficialCoverFallback(get_settings()).find_cover(
            isbn_13=_ISBN_13, isbn_10=None, wikipedia_article="Dune (novel)"
        )

    assert cover == _COMMONS_IMAGE


async def test_chain_tries_isbn_10_when_isbn_13_has_no_cover() -> None:
    with respx.mock:
        respx.head(_COVER_URL).mock(return_value=httpx.Response(404))
        respx.head(_COVER_URL_10).mock(return_value=httpx.Response(200))
        cover = await OfficialCoverFallback(get_settings()).find_cover(
            isbn_13=_ISBN_13, isbn_10=_ISBN_10, wikipedia_article=None
        )

    assert cover == f"{_COVER_URL_10}?default=false"


async def test_chain_with_nothing_to_go_on_makes_no_requests() -> None:
    with respx.mock:
        cover = await OfficialCoverFallback(get_settings()).find_cover(
            isbn_13=None, isbn_10=None, wikipedia_article=None
        )

    assert cover is None
    assert not respx.calls, "no identifiers means nothing to ask about"


async def test_wikipedia_leg_is_skipped_when_the_setting_is_off() -> None:
    # The switch that has to be flipped if this app is ever published: the
    # freely-licensed legs keep working, the fair-use one stops.
    settings = Settings(wikipedia_cover_fallback=False)
    with respx.mock:
        summary = respx.get(_SUMMARY_URL).mock(return_value=httpx.Response(200, json=_summary()))
        cover = await OfficialCoverFallback(settings).find_cover(
            isbn_13=None, isbn_10=None, wikipedia_article="Dune (novel)"
        )

    assert cover is None
    assert not summary.called

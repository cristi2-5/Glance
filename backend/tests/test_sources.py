"""Tests for the three official content sources.

All HTTP is mocked via respx — the suite makes no real network calls.
"""

import httpx
import respx

from app.core.config import Settings, get_settings
from app.models.book import SourceKind
from app.services.sources.google_books import GoogleBooksSource
from app.services.sources.open_library import OpenLibrarySource
from app.services.sources.wikipedia import WikipediaSource

_VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"
_OL_SEARCH_URL = "https://openlibrary.org/search.json"
_WIKI_API_URL = "https://en.wikipedia.org/w/api.php"


# --- Google Books -----------------------------------------------------------


def _volume(**overrides: object) -> dict[str, object]:
    """Builds a Google Books `volumeInfo` payload with sensible defaults."""
    info: dict[str, object] = {
        "title": "Dune",
        "authors": ["Frank Herbert"],
        "description": "A desert planet and its spice.",
        "categories": ["Fiction / Science Fiction"],
        "imageLinks": {"thumbnail": "https://books.test/dune.jpg"},
        "industryIdentifiers": [
            {"type": "ISBN_13", "identifier": "9780441013593"},
            {"type": "ISBN_10", "identifier": "0441013597"},
        ],
        "averageRating": 4.5,
        "ratingsCount": 1200,
        "infoLink": "https://books.test/dune",
    }
    info.update(overrides)
    return {"items": [{"volumeInfo": info}]}


# --- Picking the author out of Google's contributor list ---------------------
#
# `volumeInfo.authors` is a *contributor* list: translators, illustrators
# and editors sit in it unlabelled. A Romanian edition of Jules Verne
# returned four names, and the app displayed all of them as the author.


async def test_google_books_keeps_the_author_named_on_the_cover() -> None:
    source = GoogleBooksSource(get_settings())
    payload = _volume(
        title="Căpitan la cincisprezece ani",
        authors=["Jules Verne", "Anghel Ghițulescu", "Simona Schileru", "H. Meyer"],
    )

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Căpitan la cincisprezece ani", "Jules Verne")

    assert result.metadata.author == "Jules Verne"


async def test_google_books_uses_the_catalog_spelling_of_the_author() -> None:
    # The cover gives us who; the catalog gives us how it is spelled.
    source = GoogleBooksSource(get_settings())
    payload = _volume(authors=["Frank Herbert", "Some Translator"])

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Dune", "frank  herbert")

    assert result.metadata.author == "Frank Herbert"


async def test_google_books_caps_the_list_when_the_cover_gave_no_author() -> None:
    # Nothing to match against, so co-authorship is preserved but a cast of
    # contributors is not.
    source = GoogleBooksSource(get_settings())
    payload = _volume(authors=["A One", "B Two", "C Three", "D Four"])

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Dune", None)

    assert result.metadata.author == "A One, B Two"


async def test_google_books_keeps_genuine_co_authors() -> None:
    source = GoogleBooksSource(get_settings())
    payload = _volume(title="Good Omens", authors=["Terry Pratchett", "Neil Gaiman"])

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Good Omens", None)

    assert result.metadata.author == "Terry Pratchett, Neil Gaiman"


async def test_google_books_volume_without_authors_reports_none() -> None:
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=_volume(authors=[])))
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.metadata.author is None


async def test_google_books_maps_every_metadata_field() -> None:
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=_volume()))
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.matched is True
    assert result.available is True
    assert result.metadata.title == "Dune"
    assert result.metadata.author == "Frank Herbert"
    assert result.metadata.isbn_13 == "9780441013593"
    assert result.metadata.isbn_10 == "0441013597"
    assert result.metadata.cover_url == "https://books.test/dune.jpg"
    assert result.metadata.average_rating == 4.5
    assert result.metadata.ratings_count == 1200
    assert [p.kind for p in result.passages] == [SourceKind.DESCRIPTION]


async def test_google_books_rejects_a_volume_whose_title_does_not_match() -> None:
    # Google Books answers *something* for any free-text query. Without a
    # similarity floor a misread cover silently yields the wrong book's
    # metadata, which is worse than reporting nothing.
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(
            return_value=httpx.Response(200, json=_volume(title="Cooking with Yoghurt"))
        )
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.matched is False
    assert result.available is True


async def test_google_books_volume_without_description_yields_no_passage() -> None:
    source = GoogleBooksSource(get_settings())
    payload = _volume()
    del payload["items"][0]["volumeInfo"]["description"]  # type: ignore[index]

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Dune")

    assert result.matched is True
    assert result.passages == []


async def test_google_books_timeout_reports_unavailable_not_missing() -> None:
    # The distinction the cache depends on: a timeout must never be stored
    # as "this book does not exist".
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(side_effect=httpx.ReadTimeout("too slow"))
        result = await source.fetch("Dune")

    assert result.available is False
    assert result.matched is False


async def test_google_books_empty_result_set_is_a_real_no_match() -> None:
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json={"items": []}))
        result = await source.fetch("Some Untranslated Romanian Edition")

    assert result.available is True
    assert result.matched is False


async def test_google_books_sends_the_api_key_when_configured() -> None:
    source = GoogleBooksSource(Settings(google_books_api_key="k-123"))

    with respx.mock:
        route = respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json={"items": []}))
        await source.fetch("Dune")

    assert route.calls.last.request.url.params["key"] == "k-123"


# --- Open Library -----------------------------------------------------------


async def test_open_library_merges_search_hit_and_work_record() -> None:
    source = OpenLibrarySource(get_settings())

    with respx.mock:
        respx.get(_OL_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "docs": [
                        {
                            "key": "/works/OL893415W",
                            "title": "Dune",
                            "author_name": ["Frank Herbert"],
                            "cover_i": 8567891,
                            "ratings_average": 4.2,
                            "ratings_count": 900,
                        }
                    ]
                },
            )
        )
        respx.get("https://openlibrary.org/works/OL893415W.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "description": {"type": "/type/text", "value": "Arrakis, the desert planet."},
                    "subjects": ["Science fiction", "Ecology"],
                },
            )
        )
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.matched is True
    assert result.metadata.description == "Arrakis, the desert planet."
    assert result.metadata.categories == ["Science fiction", "Ecology"]
    assert result.metadata.cover_url is not None and "8567891" in result.metadata.cover_url
    assert {p.kind for p in result.passages} == {SourceKind.DESCRIPTION, SourceKind.SUBJECTS}
    assert result.license == "CC0"


async def test_open_library_accepts_a_bare_string_description() -> None:
    # Older records store `description` as a plain string rather than a
    # typed-text object; both shapes must flatten to the same thing.
    source = OpenLibrarySource(get_settings())

    with respx.mock:
        respx.get(_OL_SEARCH_URL).mock(
            return_value=httpx.Response(
                200, json={"docs": [{"key": "/works/OL1W", "title": "Dune"}]}
            )
        )
        respx.get("https://openlibrary.org/works/OL1W.json").mock(
            return_value=httpx.Response(200, json={"description": "Plain string form."})
        )
        result = await source.fetch("Dune")

    assert result.metadata.description == "Plain string form."


async def test_open_library_survives_a_failing_work_record() -> None:
    # The search hit alone still carries title, author, cover and rating —
    # losing the work record should degrade the result, not void it.
    source = OpenLibrarySource(get_settings())

    with respx.mock:
        respx.get(_OL_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json={"docs": [{"key": "/works/OL1W", "title": "Dune", "cover_i": 42}]},
            )
        )
        respx.get("https://openlibrary.org/works/OL1W.json").mock(return_value=httpx.Response(500))
        result = await source.fetch("Dune")

    assert result.matched is True
    assert result.metadata.description is None
    assert result.metadata.cover_url is not None


async def test_open_library_sends_a_descriptive_user_agent() -> None:
    source = OpenLibrarySource(get_settings())

    with respx.mock:
        route = respx.get(_OL_SEARCH_URL).mock(return_value=httpx.Response(200, json={"docs": []}))
        await source.fetch("Dune")

    assert "Glance" in route.calls.last.request.headers["user-agent"]


# --- Wikipedia --------------------------------------------------------------


_ARTICLE = """Dune is a 1965 science fiction novel by Frank Herbert.


== Plot ==

Duke Leto Atreides accepts stewardship of Arrakis.


== Reception ==

Dune won the Hugo Award. Arthur C. Clarke called it unique.


== Publication history ==

Serialised in Analog magazine.


== References ==

Citations follow.
"""


# Any language edition. The source searches `wikipedia_languages` in order
# (en, then ro), so a mock pinned to `en.wikipedia.org` leaves the second
# request unrouted.
_WIKI_ANY_LANG = r"https://\w+\.wikipedia\.org/w/api\.php"


def _wiki_mock(
    search_title: str = "Dune",
    extract: str = _ARTICLE,
    per_language: dict[str, list[str]] | None = None,
) -> None:
    """Routes both Wikipedia calls, across every language edition.

    Args:
        search_title: The single article title every edition returns.
        extract: The article text returned for any extract request.
        per_language: Overrides `search_title` with a list of result titles
            per language code, for testing the fallback from one edition to
            the next.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        language = request.url.host.split(".")[0]
        if request.url.params.get("list") == "search":
            if per_language is not None:
                titles = per_language.get(language, [])
            else:
                titles = [search_title]
            return httpx.Response(200, json={"query": {"search": [{"title": t} for t in titles]}})
        return httpx.Response(200, json={"query": {"pages": {"1": {"extract": extract}}}})

    respx.get(url__regex=_WIKI_ANY_LANG).mock(side_effect=handler)


async def test_wikipedia_extracts_only_reception_and_plot_sections() -> None:
    # The whole point of this source: Reception is the critical-opinion
    # corpus that replaces the review scraping the project does not do.
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock()
        result = await source.fetch("Dune", "Frank Herbert")

    kinds = [p.kind for p in result.passages]
    assert kinds == [SourceKind.PLOT, SourceKind.RECEPTION]

    reception = next(p for p in result.passages if p.kind == SourceKind.RECEPTION)
    assert "Hugo Award" in reception.content
    assert "Serialised in Analog" not in reception.content, "must stop at the next heading"

    headings = {p.heading for p in result.passages}
    assert "Publication history" not in headings
    assert "References" not in headings
    assert result.license == "CC BY-SA 4.0"


async def test_wikipedia_matches_a_disambiguated_article_title() -> None:
    # Articles are routinely titled "Dune (novel)"; the disambiguator must
    # not push the similarity below the floor.
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(search_title="Dune (novel)")
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.matched is True
    assert result.url == "https://en.wikipedia.org/wiki/Dune_(novel)"


async def test_wikipedia_no_article_is_a_no_match_not_a_failure() -> None:
    # The common case for Romanian editions — non-fatal by design.
    source = WikipediaSource(get_settings())

    with respx.mock:
        respx.get(url__regex=_WIKI_ANY_LANG).mock(
            return_value=httpx.Response(200, json={"query": {"search": []}})
        )
        result = await source.fetch("Some Obscure Edition")

    assert result.matched is False
    assert result.available is True


async def test_wikipedia_unrelated_search_hit_is_rejected() -> None:
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(search_title="History of Bulgarian Agriculture")
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.matched is False


async def test_wikipedia_article_with_no_usable_sections_yields_no_match() -> None:
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(extract="A stub with no sections at all.")
        result = await source.fetch("Dune")

    assert result.matched is False
    assert result.passages == []


async def test_wikipedia_timeout_reports_unavailable() -> None:
    source = WikipediaSource(get_settings())

    with respx.mock:
        respx.get(url__regex=_WIKI_ANY_LANG).mock(side_effect=httpx.ConnectTimeout("slow"))
        result = await source.fetch("Dune")

    assert result.available is False


# --- Multi-language search, and rejecting the wrong kind of article ----------
#
# Added after a real failure: "Căpitan la cincisprezece ani" is on
# ro.wikipedia under exactly that title, and the source — pinned to English
# — reported no article, so the book got no summary at all.


async def test_wikipedia_falls_back_to_the_next_language() -> None:
    # A Romanian edition: en.wikipedia has never heard of the title, ro has
    # the article under precisely that name.
    source = WikipediaSource(get_settings())
    romanian = "Căpitan la cincisprezece ani"

    with respx.mock:
        _wiki_mock(extract=_ARTICLE, per_language={"en": [], "ro": [romanian]})
        result = await source.fetch(romanian, "Jules Verne")

    assert result.matched is True
    assert result.record_ref == f"ro:{romanian}"
    assert result.url.startswith("https://ro.wikipedia.org/wiki/")


async def test_wikipedia_prefers_the_first_language_that_matches() -> None:
    # An English-language book must resolve on en and never reach ro, whose
    # article would be shorter and whose scores are not comparable anyway.
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(per_language={"en": ["Dune (novel)"], "ro": ["Dune (roman)"]})
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.record_ref == "en:Dune (novel)"


async def test_wikipedia_rejects_a_film_article() -> None:
    # The disambiguator is stripped before scoring, so "Moarte pe Nil (film
    # din 2022)" scores 100 against the novel. Ingesting it would build a
    # cited summary of the film.
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(per_language={"en": [], "ro": ["Moarte pe Nil (film din 2022)"]})
        result = await source.fetch("Moarte pe Nil", "Agatha Christie")

    assert result.matched is False


async def test_wikipedia_rejects_an_unrelated_disambiguated_article() -> None:
    # The reason this is a whitelist and not a blacklist: "Câmp (river)"
    # scores 80 against a book called "Câmpul", and "river" would never
    # have been on a list of things to exclude.
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(per_language={"en": ["Câmp (river)"], "ro": ["Câmp (river)"]})
        result = await source.fetch("Câmpul", None)

    assert result.matched is False


async def test_wikipedia_prefers_a_confirmed_book_article() -> None:
    # "Baltagul (roman)" is the novel; a bare "Baltagul" could be anything.
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(per_language={"en": [], "ro": ["Baltagul", "Baltagul (roman)"]})
        result = await source.fetch("Baltagul", "Mihail Sadoveanu")

    assert result.record_ref == "ro:Baltagul (roman)"


async def test_wikipedia_accepts_an_author_named_disambiguator() -> None:
    source = WikipediaSource(get_settings())

    with respx.mock:
        _wiki_mock(per_language={"en": ["Michael Strogoff (Jules Verne)"]})
        result = await source.fetch("Michael Strogoff", "Jules Verne")

    assert result.matched is True


async def test_wikipedia_query_carries_no_english_filler() -> None:
    # The bug: the query appended the English word "novel", and Wikipedia
    # ANDs its search terms — so on ro.wikipedia, where no article contains
    # it, every result was filtered out.
    source = WikipediaSource(get_settings())
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list") == "search":
            seen.append(str(request.url.params.get("srsearch")))
            return httpx.Response(200, json={"query": {"search": []}})
        return httpx.Response(200, json={"query": {"pages": {}}})

    with respx.mock:
        respx.get(url__regex=_WIKI_ANY_LANG).mock(side_effect=handler)
        await source.fetch("Baltagul", "Mihail Sadoveanu")

    assert seen
    for query in seen:
        assert "novel" not in query.lower()
        assert "book" not in query.lower()
        assert query == "Baltagul Mihail Sadoveanu"


async def test_wikipedia_extracts_romanian_sections() -> None:
    # Romanian articles resolved correctly and then produced *zero*
    # passages, because the heading vocabulary was English-only. Note the
    # diacritics: a raw `[a-z]+` split turns "Acțiune" into "ac" + "iune".
    source = WikipediaSource(get_settings())
    article = (
        "Introducere.\n\n"
        "== Rezumat ==\n\nDick Sand preia comanda navei.\n\n"
        "== Teme principale ==\n\nSclavia și maturizarea.\n\n"
        "== Aprecieri critice ==\n\nCriticii au lăudat ritmul romanului.\n\n"
        "== Traduceri în limba română ==\n\nO listă de ediții.\n\n"
        "== Ecranizări ==\n\nUn film din 1974.\n"
    )

    with respx.mock:
        _wiki_mock(extract=article, per_language={"en": [], "ro": ["Baltagul (roman)"]})
        result = await source.fetch("Baltagul", "Mihail Sadoveanu")

    by_kind = {p.kind: p for p in result.passages}
    assert SourceKind.PLOT in by_kind
    assert SourceKind.THEMES in by_kind
    assert SourceKind.RECEPTION in by_kind
    assert "lăudat" in by_kind[SourceKind.RECEPTION].content

    headings = {p.heading for p in result.passages}
    assert "Traduceri în limba română" not in headings
    assert "Ecranizări" not in headings


# --- Tie-breaking between equally-titled candidates --------------------------
#
# A popular novel returns several volumes whose titles all match perfectly:
# the novel, an "A Novel" reissue, a literary-criticism study of it. The old
# `>=` comparison let the *last* of them win, discarding the catalog's own
# relevance ranking. Observed live: "Seraphina" resolved to a criticism
# volume with no description, so the book cached with a cover and nothing
# to read.


async def test_google_books_prefers_a_described_volume_over_a_bare_tie() -> None:
    payload = {
        "items": [
            {"volumeInfo": {"title": "Seraphina", "categories": ["Literary Criticism"]}},
            {
                "volumeInfo": {
                    "title": "Seraphina",
                    "description": "A kingdom of dragons and a half-dragon musician.",
                    "categories": ["Fiction"],
                }
            },
        ]
    }
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Seraphina", "Rachel Hartman")

    assert result.metadata.description is not None
    assert result.metadata.categories == ["Fiction"]
    assert len(result.passages) == 1


async def test_google_books_keeps_catalog_order_when_candidates_tie_exactly() -> None:
    # Both carry a description and score identically, so Google's own
    # relevance ranking decides — the first wins, not the last.
    payload = {
        "items": [
            {"volumeInfo": {"title": "Dune", "description": "The novel."}},
            {"volumeInfo": {"title": "Dune", "description": "A study guide."}},
        ]
    }
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.metadata.description == "The novel."


async def test_google_books_similarity_still_outranks_richness() -> None:
    # A described but wrong-titled volume must not beat the right book.
    payload = {
        "items": [
            {"volumeInfo": {"title": "Dune", "categories": ["Fiction"]}},
            {"volumeInfo": {"title": "Heretics of Dune", "description": "Book five."}},
        ]
    }
    source = GoogleBooksSource(get_settings())

    with respx.mock:
        respx.get(_VOLUMES_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await source.fetch("Dune", "Frank Herbert")

    assert result.metadata.title == "Dune"
    assert result.metadata.description is None


async def test_open_library_prefers_a_hit_carrying_a_cover_on_a_tie() -> None:
    search = {
        "docs": [
            {"key": "/works/OL1W", "title": "Seraphina", "author_name": ["Rachel Hartman"]},
            {
                "key": "/works/OL2W",
                "title": "Seraphina",
                "author_name": ["Rachel Hartman"],
                "cover_i": 42,
            },
        ]
    }
    source = OpenLibrarySource(get_settings())

    with respx.mock:
        respx.get(_OL_SEARCH_URL).mock(return_value=httpx.Response(200, json=search))
        respx.get("https://openlibrary.org/works/OL2W.json").mock(
            return_value=httpx.Response(200, json={"description": "A half-dragon musician."})
        )
        result = await source.fetch("Seraphina", "Rachel Hartman")

    assert result.metadata.cover_url is not None
    assert result.url == "https://openlibrary.org/works/OL2W"

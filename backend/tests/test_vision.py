"""Tests for `VisionService`'s vision-model-first / OCR-fallback branching."""

import json
from io import BytesIO

import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.exceptions import CoverNotRecognized
from app.services.ocr_service import OcrService, TextCandidate
from app.services.title_lookup import BookCandidate
from app.services.vision_service import (
    VisionService,
    _looks_like_person_name,
    _parse_vision_response,
    _score_candidate,
    normalize_for_match,
)
from tests.fakes import FakeOcrEngine, FakeOllamaClient, FakeTitleLookup


def _cover_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (400, 600), color=(50, 50, 50)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _make_service(
    ocr_candidates: list[TextCandidate],
    lookup_candidates: list[BookCandidate],
    ollama_response: str = "",
    lookup_available: bool = True,
) -> tuple[VisionService, FakeOllamaClient, FakeTitleLookup]:
    ocr = OcrService(FakeOcrEngine(ocr_candidates))
    ollama = FakeOllamaClient(ollama_response)
    lookup = FakeTitleLookup(lookup_candidates, available=lookup_available)
    service = VisionService(ocr=ocr, ollama=ollama, lookup=lookup, settings=get_settings())
    return service, ollama, lookup


# --- Vision-first short-circuit ----------------------------------------------


async def test_confident_vision_match_returns_immediately_without_running_ocr() -> None:
    # OCR candidates are present and would also match — proving the vision
    # model's confirmed guess is returned before OCR's machinery ever runs,
    # not just that the final title happens to agree.
    ocr_candidates = [
        TextCandidate(text="Dune", score=0.95, relative_height=40, line_index=0),
        TextCandidate(text="Frank Herbert", score=0.9, relative_height=20, line_index=1),
    ]
    lookup_candidates = [BookCandidate(title="Dune", authors=["Frank Herbert"])]
    service, ollama, lookup = _make_service(
        ocr_candidates,
        lookup_candidates,
        ollama_response=json.dumps({"title": "Dune", "author": "Frank Herbert"}),
    )

    result = await service.identify(_cover_bytes())

    assert result.method == "vision"
    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert result.needs_review is False
    assert len(ollama.calls) == 1
    # A single query, from the vision guess — OCR's own (multi-query) lookup
    # machinery in `_build_queries` never ran.
    assert lookup.queries == ["Dune"]


# --- The OCR fallback path ---------------------------------------------------


async def test_ocr_fallback_wins_when_vision_fails_to_parse() -> None:
    # The default `ollama_response=""` in `_make_service` can't be parsed
    # into a title, so the vision model produces nothing usable and OCR
    # takes over.
    ocr_candidates = [
        TextCandidate(text="Dune", score=0.95, relative_height=40, line_index=0),
        TextCandidate(text="Frank Herbert", score=0.9, relative_height=20, line_index=1),
    ]
    lookup_candidates = [BookCandidate(title="Dune", authors=["Frank Herbert"])]
    service, ollama, _lookup = _make_service(ocr_candidates, lookup_candidates)

    result = await service.identify(_cover_bytes())

    assert result.method == "ocr"
    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert result.needs_review is False
    assert len(ollama.calls) == 1


async def test_catalog_queries_use_cover_reading_order_not_text_size() -> None:
    # As printed: "Ocolul Pamantului" above "in optzeci de zile", with the
    # author line largest. Sorting by height would emit the phrase scrambled.
    ocr_candidates = [
        TextCandidate(text="Jules Verne", score=0.95, relative_height=50, line_index=0),
        TextCandidate(text="Ocolul Pamantului", score=0.9, relative_height=30, line_index=1),
        TextCandidate(text="in optzeci de zile", score=0.9, relative_height=20, line_index=2),
    ]
    service, _ollama, lookup = _make_service(ocr_candidates, [])

    await service.identify(_cover_bytes())

    assert lookup.queries[0] == "Jules Verne Ocolul Pamantului in optzeci de zile"


async def test_ocr_unconfirmed_reading_beats_vision_unconfirmed_guess() -> None:
    # Neither the vision model's guess nor the OCR reading is catalog-
    # confirmed (a Romanian edition the catalog doesn't have). Legible cover
    # text still beats a model's guess: OCR's fixed unconfirmed confidence
    # is set above vision's, so `identify()`'s max-confidence tie-break picks it.
    settings = get_settings()
    ocr_candidates = [
        TextCandidate(text="Ocolul Pamantului", score=0.95, relative_height=50, line_index=1),
        TextCandidate(text="Jules Verne", score=0.9, relative_height=25, line_index=0),
    ]
    service, ollama, _lookup = _make_service(
        ocr_candidates,
        lookup_candidates=[],
        ollama_response=json.dumps({"title": "Some Other Guess", "author": None}),
    )

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.method == "ocr"
    assert result.title == "Ocolul Pamantului"
    assert result.author == "Jules Verne"
    assert result.confidence == settings.vision_ocr_unconfirmed_confidence
    assert result.confidence > settings.vision_unverified_confidence
    assert result.needs_review is True


async def test_unreachable_catalog_still_trusts_ocr() -> None:
    # Google Books 429: `available=False`. Confidence must not be treated as
    # evidence against the reading.
    ocr_candidates = [
        TextCandidate(text="Moartea pe Nil", score=0.95, relative_height=50, line_index=0),
        TextCandidate(text="Agatha Christie", score=0.9, relative_height=25, line_index=1),
    ]
    service, ollama, _lookup = _make_service(
        ocr_candidates, lookup_candidates=[], lookup_available=False
    )

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.method == "ocr"
    assert result.title == "Moartea pe Nil"
    assert result.author == "Agatha Christie"
    assert result.needs_review is True


async def test_small_print_is_excluded_from_the_primary_catalog_query() -> None:
    # The Goggins cover: a subtitle sits between the title lines and the
    # cover carries marketing and publisher text. Including all of it buried
    # the title and matched nothing.
    ocr_candidates = [
        TextCandidate(text="NU", score=0.9, relative_height=90, line_index=0),
        TextCandidate(text="STAPANESTE-TI MINTEA SI", score=0.9, relative_height=18, line_index=1),
        TextCandidate(text="MA POTI", score=0.9, relative_height=90, line_index=2),
        TextCandidate(text="RANI", score=0.9, relative_height=90, line_index=3),
        TextCandidate(text="DAVID GOGGINS", score=0.9, relative_height=34, line_index=4),
        TextCandidate(text="PESTE 4 MILIOANE VANDUTE", score=0.9, relative_height=12, line_index=5),
        TextCandidate(text="LITERA", score=0.9, relative_height=14, line_index=6),
    ]
    service, _ollama, lookup = _make_service(ocr_candidates, [])

    await service.identify(_cover_bytes())

    assert lookup.queries[0] == "NU MA POTI RANI DAVID GOGGINS"


async def test_a_confident_match_stops_further_catalog_queries() -> None:
    ocr_candidates = [
        TextCandidate(text="Dune", score=0.95, relative_height=50, line_index=0),
        TextCandidate(text="Frank Herbert", score=0.9, relative_height=20, line_index=1),
    ]
    service, _ollama, lookup = _make_service(
        ocr_candidates, [BookCandidate(title="Dune", authors=["Frank Herbert"])]
    )

    await service.identify(_cover_bytes())

    # Spending quota on vaguer queries can only replace a good answer with a
    # worse one that happens to score higher.
    assert len(lookup.queries) == 1


def test_author_name_in_a_catalog_title_does_not_inflate_the_title_score() -> None:
    # Catalogs sometimes append the author to the title. Scored naively, that
    # padding beat the correct edition, because the cover text also contains
    # the author's name.
    reference = "Jules Verne Un capitand the cinsprezece ani"
    correct = BookCandidate(title="Căpitan la cincisprezece ani", authors=["Jules Verne"])
    padded = BookCandidate(title="Capitan la 15 ani ilustrat, Jules Verne", authors=["Jules Verne"])

    assert _score_candidate(correct, reference) > _score_candidate(padded, reference)


async def test_multi_line_title_in_caps_is_not_mistaken_for_the_author() -> None:
    # "NU" / "MA POTI" / "RANI" are all caps and name-shaped, but they share
    # one large size — a wrapped title. The author stands alone at its own.
    ocr_candidates = [
        TextCandidate(text="NU", score=0.9, relative_height=90, line_index=0),
        TextCandidate(text="MA POTI", score=0.9, relative_height=90, line_index=1),
        TextCandidate(text="RANI", score=0.9, relative_height=90, line_index=2),
        TextCandidate(text="DAVID GOGGINS", score=0.9, relative_height=34, line_index=3),
    ]
    service, _ollama, _lookup = _make_service(
        ocr_candidates, lookup_candidates=[], lookup_available=False
    )

    result = await service.identify(_cover_bytes())

    assert result.author == "DAVID GOGGINS"
    assert result.title == "NU MA POTI RANI"


async def test_author_line_is_found_via_catalog_even_when_the_title_is_not() -> None:
    # The real Jules Verne cover: the author's name is printed LARGER than
    # the title, so "tallest line is the title" gets it exactly backwards.
    # No catalog has this Romanian edition, but every catalog knows the
    # author — which is what identifies the author line.
    ocr_candidates = [
        TextCandidate(text="Jules Verne", score=0.95, relative_height=50, line_index=0),
        TextCandidate(text="Ocolul Pamantului", score=0.9, relative_height=34, line_index=1),
        TextCandidate(text="in optzeci de zile", score=0.9, relative_height=28, line_index=2),
    ]
    # A different edition by the same author — confirms the name, not the title.
    lookup_candidates = [
        BookCandidate(title="Around the World in Eighty Days", authors=["Jules Verne"])
    ]
    service, ollama, _lookup = _make_service(ocr_candidates, lookup_candidates)

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.author == "Jules Verne"
    # The title spans two lines and must be rejoined in reading order.
    assert result.title == "Ocolul Pamantului in optzeci de zile"
    assert result.needs_review is True


async def test_author_falls_back_to_name_shape_when_no_catalog_answers() -> None:
    ocr_candidates = [
        TextCandidate(text="Jules Verne", score=0.95, relative_height=50, line_index=0),
        TextCandidate(text="Ocolul Pamantului", score=0.9, relative_height=34, line_index=1),
    ]
    service, _ollama, _lookup = _make_service(
        ocr_candidates, lookup_candidates=[], lookup_available=False
    )

    result = await service.identify(_cover_bytes())

    assert result.author == "Jules Verne"
    assert result.title == "Ocolul Pamantului"


async def test_diacritic_folding_lets_ocr_match_the_catalog() -> None:
    # OCR drops Romanian diacritics; the catalog spells them correctly.
    ocr_candidates = [
        TextCandidate(
            text="Ocolul Pamantului in optzeci de zile",
            score=0.95,
            relative_height=50,
            line_index=0,
        ),
        TextCandidate(text="Jules Verne", score=0.9, relative_height=25, line_index=1),
    ]
    lookup_candidates = [
        BookCandidate(title="Ocolul Pământului în optzeci de zile", authors=["Jules Verne"])
    ]
    service, _ollama, _lookup = _make_service(ocr_candidates, lookup_candidates)

    result = await service.identify(_cover_bytes())

    assert result.method == "ocr"
    assert result.title == "Ocolul Pământului în optzeci de zile"
    assert result.needs_review is False


# --- The vision model (primary) ---------------------------------------------


async def test_confident_vision_guess_is_used_when_ocr_has_nothing_useful() -> None:
    service, ollama, lookup = _make_service(
        ocr_candidates=[TextCandidate(text="Xy", score=0.9, relative_height=10, line_index=0)],
        lookup_candidates=[BookCandidate(title="Neuromancer", authors=["William Gibson"])],
        ollama_response=json.dumps({"title": "Neuromancer", "author": "William Gibson"}),
    )

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.method == "vision"
    assert result.title == "Neuromancer"
    assert lookup.queries == ["Neuromancer"]


async def test_vision_model_reply_length_is_capped() -> None:
    settings = get_settings()
    service, ollama, _lookup = _make_service(
        ocr_candidates=[],
        lookup_candidates=[],
        ollama_response=json.dumps({"title": "Anything", "author": None}),
    )

    await service.identify(_cover_bytes())

    assert ollama.calls[0].options == {"num_predict": settings.ollama_vision_num_predict}


async def test_truncated_vision_json_is_salvaged_not_failed() -> None:
    # Verbatim from a real run: the model overran into invented keys and was
    # cut mid-string. The title and author had already arrived intact.
    truncated = (
        '{"title": "Moartea pe Nil", "author": "Agatha Christie", "year": "1927", '
        '"language": "English", "numberOfShips": 1, "numberOfDepts'
    )
    service, _ollama, _lookup = _make_service(
        ocr_candidates=[], lookup_candidates=[], ollama_response=truncated
    )

    result = await service.identify(_cover_bytes())

    assert result.title == "Moartea pe Nil"
    assert result.author == "Agatha Christie"


async def test_vision_response_without_a_title_raises_cover_not_recognized() -> None:
    service, _ollama, _lookup = _make_service(
        ocr_candidates=[],
        lookup_candidates=[],
        ollama_response="Sure! This looks like a mystery novel.",
    )

    with pytest.raises(CoverNotRecognized):
        await service.identify(_cover_bytes())


async def test_unconfirmed_vision_guess_gets_fixed_low_confidence() -> None:
    settings = get_settings()
    service, ollama, lookup = _make_service(
        ocr_candidates=[],
        lookup_candidates=[],
        ollama_response=json.dumps({"title": "An Obscure Zine", "author": None}),
    )

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.method == "vision"
    assert result.confidence == settings.vision_unverified_confidence
    assert result.needs_review is True
    assert lookup.queries == ["An Obscure Zine"]
    # An unconfirmed model guess must stay below an unconfirmed OCR reading.
    assert result.confidence < settings.vision_ocr_unconfirmed_confidence


# --- Helpers ----------------------------------------------------------------


def test_normalize_for_match_folds_diacritics_and_case() -> None:
    assert normalize_for_match("Ocolul Pământului ÎN") == "ocolul pamantului in"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Agatha Christie", True),
        ("Jules Verne", True),
        ("Honoré de Balzac", True),
        ("Ludwig van Beethoven", True),
        ("Mihail Sadoveanu", True),
        # Titles that share a name's word count but not its capitalization.
        ("Moartea pe Nil", False),
        ("Ocolul Pamantului in optzeci de zile", False),
        ("Dune", False),  # single word
        ("Volume 2 Collected", False),  # digits
        ("", False),
    ],
)
def test_looks_like_person_name(line: str, expected: bool) -> None:
    assert _looks_like_person_name(line) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"title": "A", "author": "B"}', ("A", "B")),
        ('{"title": "A", "author": null}', ("A", None)),
        ('{"title": "A", "author": "unknown"}', ("A", None)),
        ('{"title": "A", "author": "B", "extra": "trunc', ("A", "B")),
        ('prose then {"title": "A", "author": "B"}', ("A", "B")),
        ("no json at all", None),
        ('{"author": "B"}', None),
    ],
)
def test_parse_vision_response(raw: str, expected: tuple[str, str | None] | None) -> None:
    assert _parse_vision_response(raw) == expected

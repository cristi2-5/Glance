"""Tests for `VisionService`'s OCR-first / Moondream-fallback branching."""

import json
from io import BytesIO

import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.exceptions import CoverNotRecognized
from app.services.ocr_service import OcrService, TextCandidate
from app.services.title_lookup import BookCandidate
from app.services.vision_service import VisionService
from tests.fakes import FakeOcrEngine, FakeOllamaClient, FakeTitleLookup


def _cover_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (400, 600), color=(50, 50, 50)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _make_service(
    ocr_candidates: list[TextCandidate],
    lookup_candidates: list[BookCandidate],
    ollama_response: str = "",
) -> tuple[VisionService, FakeOllamaClient, FakeTitleLookup]:
    ocr = OcrService(FakeOcrEngine(ocr_candidates))
    ollama = FakeOllamaClient(ollama_response)
    lookup = FakeTitleLookup(lookup_candidates)
    service = VisionService(ocr=ocr, ollama=ollama, lookup=lookup, settings=get_settings())
    return service, ollama, lookup


async def test_strong_ocr_match_short_circuits_moondream() -> None:
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
    assert ollama.calls == []


async def test_sparse_ocr_falls_back_to_moondream() -> None:
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


async def test_ocr_present_but_poor_match_falls_back_to_moondream() -> None:
    ocr_candidates = [
        TextCandidate(text="Some Random Cover Text", score=0.9, relative_height=40, line_index=0),
    ]
    # The lookup returns a completely unrelated book — the fuzzy score should
    # land well below the confidence threshold.
    service, ollama, _lookup = _make_service(
        ocr_candidates,
        lookup_candidates=[BookCandidate(title="Moby Dick", authors=["Herman Melville"])],
        ollama_response=json.dumps({"title": "Moby Dick", "author": "Herman Melville"}),
    )

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.method == "vision"


async def test_moondream_non_json_response_raises_cover_not_recognized() -> None:
    service, _ollama, _lookup = _make_service(
        ocr_candidates=[],
        lookup_candidates=[],
        ollama_response="Sure! This looks like a mystery novel.",
    )

    with pytest.raises(CoverNotRecognized):
        await service.identify(_cover_bytes())


async def test_moondream_result_unverified_by_lookup_gets_fixed_low_confidence() -> None:
    settings = get_settings()
    service, ollama, lookup = _make_service(
        ocr_candidates=[],
        lookup_candidates=[],  # lookup finds nothing to confirm the guess
        ollama_response=json.dumps({"title": "An Obscure Zine", "author": None}),
    )

    result = await service.identify(_cover_bytes())

    assert len(ollama.calls) == 1
    assert result.method == "vision"
    assert result.confidence == settings.vision_unverified_confidence
    assert result.needs_review is True
    assert lookup.queries == ["An Obscure Zine"]

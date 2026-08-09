"""End-to-end vision tests against real cover photos.

Runs the full stack for real: RapidOCR, Ollama (Moondream), and a live
Google Books lookup. No fakes, no mocked HTTP. Slow (network + model
inference) and requires Ollama running locally with `moondream` pulled —
hence `@pytest.mark.slow`, excluded from the default `pytest` run.

Each fixture is skipped individually (not failed) if the image file hasn't
been dropped in yet, or `expected.json` still has its placeholder value —
see `tests/fixtures/covers/expected.json` for the filenames/format needed.
"""

import json
from pathlib import Path

import pytest
from rapidfuzz import fuzz

from app.core.config import get_settings
from app.services.ocr_service import OcrService, RapidOcrEngine
from app.services.ollama_client import get_ollama_client
from app.services.title_lookup import build_title_lookup
from app.services.vision_service import VisionService

pytestmark = pytest.mark.slow

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "covers"
_EXPECTED_PATH = _FIXTURES_DIR / "expected.json"
_MIN_TITLE_MATCH_SCORE = 85.0
_PLACEHOLDER = "REPLACE ME"


def _load_expected() -> dict[str, dict[str, str]]:
    if not _EXPECTED_PATH.exists():
        return {}
    data: dict[str, dict[str, str]] = json.loads(_EXPECTED_PATH.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("filename", list(_load_expected().keys()) or ["__no_fixtures__"])
async def test_real_cover_is_identified_correctly(filename: str) -> None:
    expected = _load_expected()
    if filename not in expected:
        pytest.skip("No fixture covers configured yet — see tests/fixtures/covers/expected.json.")

    expected_title = expected[filename]["title"]
    if expected_title == _PLACEHOLDER:
        pytest.skip(f"{filename}: fill in the real title/author in expected.json first.")

    image_path = _FIXTURES_DIR / filename
    if not image_path.exists():
        pytest.skip(f"{filename}: fixture image not present yet.")

    settings = get_settings()
    service = VisionService(
        ocr=OcrService(RapidOcrEngine()),
        ollama=get_ollama_client(),
        lookup=build_title_lookup(),
        settings=settings,
    )

    result = await service.identify(image_path.read_bytes())

    score = fuzz.token_set_ratio(result.title, expected_title)
    assert score >= _MIN_TITLE_MATCH_SCORE, (
        f"{filename}: got title {result.title!r} (method={result.method}, "
        f"confidence={result.confidence:.2f}), expected {expected_title!r} (score={score:.1f})"
    )

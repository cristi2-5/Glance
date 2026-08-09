"""OCR extraction of text candidates from a book cover, via RapidOCR.

`RapidOcrEngine` sits behind the `OcrEngine` Protocol so tests can inject a
fake and never pay RapidOCR's model-loading cost.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import anyio
import structlog
from rapidocr_onnxruntime import RapidOCR

logger = structlog.get_logger(__name__)

_MIN_TEXT_LENGTH = 2
_MIN_OCR_SCORE = 0.5


@lru_cache
def _get_rapid_ocr() -> RapidOCR:
    """Lazily builds the process-wide RapidOCR instance.

    Model loading is expensive (ONNX Runtime session init); this must not
    run on every request, so `RapidOcrEngine` instances share one engine.
    """
    return RapidOCR()


@dataclass(frozen=True)
class TextCandidate:
    """A single line of text recognized on the cover.

    Attributes:
        text: The recognized text, stripped.
        score: The OCR engine's confidence for this line, 0-1.
        relative_height: The line's bounding-box height as a fraction of the
            image height — larger text (titles) tends to have a higher value.
        line_index: The original position in the engine's output, top to bottom.
    """

    text: str
    score: float
    relative_height: float
    line_index: int


class OcrEngine(Protocol):
    """Abstraction over an OCR backend, so it can be faked in tests."""

    def read(self, image_bytes: bytes) -> list[TextCandidate]:
        """Synchronously extracts text candidates from an image.

        Args:
            image_bytes: The prepared (preprocessed) JPEG image content.

        Returns:
            The recognized text lines, in the engine's native order.
        """
        ...


class RapidOcrEngine:
    """`OcrEngine` backed by RapidOCR (ONNX Runtime, no torch).

    Cheap to instantiate: the underlying `RapidOCR` model session is a
    lazily-built, process-wide singleton (see `_get_rapid_ocr`), so creating
    a new `RapidOcrEngine` per request does not reload models.
    """

    def read(self, image_bytes: bytes) -> list[TextCandidate]:
        """Runs RapidOCR synchronously and normalizes its output.

        Args:
            image_bytes: The prepared JPEG image content.

        Returns:
            The recognized text lines, with per-line score and relative height.
        """
        result, _elapsed = _get_rapid_ocr()(image_bytes)
        if not result:
            return []

        candidates = []
        for index, (box, text, score) in enumerate(result):
            ys = [point[1] for point in box]
            height = max(ys) - min(ys)
            candidates.append(
                TextCandidate(text=text, score=score, relative_height=height, line_index=index)
            )
        return candidates


class OcrService:
    """Async-friendly wrapper that runs an `OcrEngine` off the event loop."""

    def __init__(self, engine: OcrEngine) -> None:
        self._engine = engine

    async def extract_candidates(self, image_bytes: bytes) -> list[TextCandidate]:
        """Extracts and normalizes text candidates from a cover image.

        Runs the (blocking) OCR engine in a worker thread, then filters out
        noise: very short lines and low-confidence recognitions. Candidates
        are sorted by relative height, descending — larger text is more
        likely to be the title than the publisher's imprint or a blurb.

        Args:
            image_bytes: The prepared JPEG image content.

        Returns:
            The filtered, height-sorted text candidates.
        """
        raw = await anyio.to_thread.run_sync(self._engine.read, image_bytes)

        filtered = [
            candidate
            for candidate in raw
            if len(candidate.text.strip()) >= _MIN_TEXT_LENGTH and candidate.score >= _MIN_OCR_SCORE
        ]
        filtered.sort(key=lambda c: c.relative_height, reverse=True)

        logger.debug("ocr_candidates_extracted", raw_count=len(raw), kept_count=len(filtered))
        return filtered

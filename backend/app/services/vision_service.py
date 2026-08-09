"""Cover identification: OCR-first, Moondream as fallback.

`VisionService.identify` runs RapidOCR on the cover, validates the reading
against Google Books via fuzzy matching, and only invokes the (much slower)
Moondream vision model when the OCR text is too sparse or no candidate
clears the confidence threshold.
"""

import json
from dataclasses import dataclass
from typing import Literal

import structlog
from rapidfuzz import fuzz

from app.core.config import Settings, get_settings
from app.core.exceptions import CoverNotRecognized
from app.schemas.vision import CoverIdentification
from app.services.image_preprocessing import preprocess_cover
from app.services.ocr_service import OcrService, RapidOcrEngine, TextCandidate
from app.services.ollama_client import OllamaClient, get_ollama_client
from app.services.title_lookup import BookCandidate, TitleLookup, build_title_lookup

logger = structlog.get_logger(__name__)

_MAX_TOP_LINES_FOR_QUERY = 3

_MOONDREAM_PROMPT = (
    "Look at this book cover. Reply with a single JSON object with exactly two keys: "
    '"title" (the book title) and "author" (the author\'s full name, or null if not '
    "visible). No other text, no markdown."
)


@dataclass(frozen=True)
class _ScoredCandidate:
    """A `BookCandidate` paired with its fuzzy-match score against OCR text."""

    candidate: BookCandidate
    score: float  # 0-1


def _score_candidate(candidate: BookCandidate, reference_text: str) -> float:
    """Scores how well a book candidate matches a reference text, 0-1.

    Weighted 0.7 title / 0.3 author when the candidate has known authors,
    title alone otherwise — the title carries most of the identifying
    signal, but a matching author breaks ties between similarly-named books.
    """
    title_score = fuzz.token_set_ratio(candidate.title, reference_text)
    if candidate.authors:
        author_score = fuzz.token_set_ratio(" ".join(candidate.authors), reference_text)
        combined = 0.7 * title_score + 0.3 * author_score
    else:
        combined = title_score
    return combined / 100


async def _best_lookup_match(
    lookup: TitleLookup, queries: list[str], reference_text: str
) -> _ScoredCandidate | None:
    """Runs each query against the lookup and returns the best-scoring candidate overall."""
    best: _ScoredCandidate | None = None
    for query in queries:
        if not query.strip():
            continue
        for candidate in await lookup.search(query):
            score = _score_candidate(candidate, reference_text)
            if best is None or score > best.score:
                best = _ScoredCandidate(candidate=candidate, score=score)
    return best


def _parse_moondream_response(raw: str) -> tuple[str, str | None] | None:
    """Parses Moondream's JSON reply into `(title, author)`, or `None` if unusable."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    author = data.get("author")
    if not isinstance(author, str) or not author.strip():
        author = None

    return title.strip(), author


class VisionService:
    """Identifies a book's title/author from a photo of its cover."""

    def __init__(
        self,
        ocr: OcrService,
        ollama: OllamaClient,
        lookup: TitleLookup,
        settings: Settings,
    ) -> None:
        self._ocr = ocr
        self._ollama = ollama
        self._lookup = lookup
        self._settings = settings

    async def identify(self, image_bytes: bytes) -> CoverIdentification:
        """Identifies the book shown on a cover photo.

        Args:
            image_bytes: The raw, unprocessed uploaded image content.

        Returns:
            The recognized title/author with a confidence score.

        Raises:
            ImageProcessingFailed: If the image cannot be decoded.
            CoverNotRecognized: If neither OCR nor the vision fallback
                produced a usable title.
        """
        prepared = preprocess_cover(image_bytes)
        ocr_candidates = await self._ocr.extract_candidates(prepared.jpeg_bytes)
        ocr_text = " ".join(candidate.text for candidate in ocr_candidates)

        if ocr_candidates and len(ocr_text.strip()) >= self._settings.vision_min_ocr_chars:
            match = await self._match_via_ocr(ocr_candidates, ocr_text)
            if match is not None and match.score >= self._settings.vision_confidence_threshold:
                logger.info("vision_identified_via_ocr", score=match.score)
                return self._build_result(
                    title=match.candidate.title,
                    author=match.candidate.authors[0] if match.candidate.authors else None,
                    confidence=match.score,
                    method="ocr",
                )

        return await self._identify_via_vision(prepared.jpeg_bytes)

    async def _match_via_ocr(
        self, ocr_candidates: list[TextCandidate], ocr_text: str
    ) -> _ScoredCandidate | None:
        """Queries the title lookup from OCR text and returns the best match."""
        top_lines = ocr_candidates[:_MAX_TOP_LINES_FOR_QUERY]
        joined_query = " ".join(candidate.text for candidate in top_lines)
        largest_line_query = ocr_candidates[0].text

        queries = [joined_query]
        if largest_line_query != joined_query:
            queries.append(largest_line_query)

        return await _best_lookup_match(self._lookup, queries, ocr_text)

    async def _identify_via_vision(self, image_bytes: bytes) -> CoverIdentification:
        """Runs the Moondream fallback and validates its guess against the lookup."""
        raw = await self._ollama.generate(
            model=self._settings.ollama_vision_model,
            prompt=_MOONDREAM_PROMPT,
            images=[image_bytes],
            format="json",
        )
        parsed = _parse_moondream_response(raw)
        if parsed is None:
            logger.warning("vision_moondream_unparsable", raw=raw[:200])
            raise CoverNotRecognized("Could not identify the book from the cover image.")

        title, author = parsed
        reference_text = f"{title} {author}" if author else title
        match = await _best_lookup_match(self._lookup, [title], reference_text)

        if match is not None and match.score >= self._settings.vision_confidence_threshold:
            logger.info("vision_identified_via_moondream_verified", score=match.score)
            return self._build_result(
                title=match.candidate.title,
                author=match.candidate.authors[0] if match.candidate.authors else author,
                confidence=match.score,
                method="vision",
            )

        logger.info("vision_identified_via_moondream_unverified")
        return self._build_result(
            title=title,
            author=author,
            confidence=self._settings.vision_unverified_confidence,
            method="vision",
        )

    def _build_result(
        self,
        title: str,
        author: str | None,
        confidence: float,
        method: Literal["ocr", "vision"],
    ) -> CoverIdentification:
        return CoverIdentification(
            title=title,
            author=author,
            confidence=confidence,
            method=method,
            needs_review=confidence < self._settings.vision_confidence_threshold,
        )


def build_vision_service() -> VisionService:
    """Factory for the production `VisionService`, wired with real backends.

    Returns:
        A `VisionService` using RapidOCR, Ollama, and Google Books. Cheap to
        call repeatedly: the expensive OCR model session is a cached
        singleton (see `app.services.ocr_service._get_rapid_ocr`).
    """
    settings = get_settings()
    return VisionService(
        ocr=OcrService(RapidOcrEngine()),
        ollama=get_ollama_client(),
        lookup=build_title_lookup(),
        settings=settings,
    )

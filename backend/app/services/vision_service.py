"""Cover identification: vision-model-first, OCR as a fallback.

The vision model (Groq by default, Ollama if `Settings.ai_provider` is
switched back to local — see `groq_client.py`) runs on every cover. OCR
(`RapidOCR`) only runs when the vision model's guess isn't confirmed by the
catalog, or fails outright — and when it does run, whichever of the two
ends up more confident wins, so a confirmed OCR reading can still beat an
unconfirmed vision guess. See "Vision: vision-model-first, OCR as fallback"
in `CLAUDE.md` for why this order was chosen over OCR-first.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import structlog
from rapidfuzz import fuzz

from app.core.config import Settings, get_settings
from app.core.exceptions import CoverNotRecognized
from app.schemas.vision import CoverIdentification
from app.services.groq_client import get_active_ai_client
from app.services.image_preprocessing import preprocess_cover
from app.services.ocr_service import OcrService, RapidOcrEngine, TextCandidate
from app.services.ollama_client import OllamaClient
from app.services.title_lookup import BookCandidate, TitleLookup, build_title_lookup

logger = structlog.get_logger(__name__)

_MAX_TOP_LINES_FOR_QUERY = 4

# How closely an OCR line must match a catalog author name to be treated as
# the author line rather than part of the title.
_AUTHOR_LINE_MATCH_MIN = 85.0

# A line counts as "prominent" (title or author, not small print) when it is
# at least this fraction as tall as the largest text on the cover.
_PROMINENT_HEIGHT_RATIO = 0.35

_VISION_PROMPT = (
    "Look at this book cover. Reply with ONLY a JSON object with exactly two keys: "
    '"title" and "author". Use the exact words printed on the cover, in their original '
    "language. If the author is not visible, use null. Example: "
    '{"title": "...", "author": "..."} — nothing else.'
)

# Lowercase words allowed inside a name without breaking capitalization
# ("Ludwig van Beethoven", "Honoré de Balzac").
_NAME_PARTICLES = frozenset(
    {"de", "del", "della", "di", "da", "du", "van", "von", "der", "den", "la", "le", "y", "bin"}
)


def _looks_like_person_name(text: str) -> bool:
    """Whether a cover line reads like an author's name.

    Requires 2-4 words, every one either capitalized or a name particle,
    and no digits. The capitalization requirement is what separates
    "Agatha Christie" from a title such as "Moartea pe Nil", which is
    otherwise the same shape.

    Args:
        text: A single OCR line.

    Returns:
        `True` if the line has the shape of a person's name.
    """
    words = text.split()
    if not 2 <= len(words) <= 4:
        return False

    capitalized = 0
    for word in words:
        stripped = word.strip(".,'-")
        if not stripped or any(c.isdigit() for c in stripped):
            return False
        if stripped[0].isupper():
            capitalized += 1
        elif stripped.lower() not in _NAME_PARTICLES:
            return False

    return capitalized >= 2


def normalize_for_match(text: str) -> str:
    """Folds text to a diacritic-free, lowercase form for fuzzy comparison.

    OCR frequently drops Romanian diacritics ("Pamantului" for
    "Pământului"), which costs ~10 points of `token_set_ratio` against a
    correctly-spelled catalog title. Folding both sides removes that
    penalty without loosening the match in any other way.

    Args:
        text: Arbitrary text (OCR output or a catalog title).

    Returns:
        The lowercased text with combining marks stripped.
    """
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return without_marks.lower().strip()


@dataclass(frozen=True)
class _ScoredCandidate:
    """A `BookCandidate` paired with its fuzzy-match score against OCR text."""

    candidate: BookCandidate
    score: float  # 0-1


def _without_tokens(text: str, unwanted: set[str]) -> str:
    """Drops whole words present in `unwanted`, keeping the original if empty."""
    kept = " ".join(word for word in text.split() if word not in unwanted)
    return kept or text


def _score_candidate(candidate: BookCandidate, reference_text: str) -> float:
    """Scores how well a book candidate matches a reference text, 0-1.

    Weighted 0.7 title / 0.3 author when the candidate has known authors,
    title alone otherwise — the title carries most of the identifying
    signal, but a matching author breaks ties between similarly-named books.
    Both sides are diacritic-folded first (see `normalize_for_match`).

    The author's name is removed from *both* sides before the titles are
    compared. The cover text contains the author, and catalogs sometimes
    append it to the title ("Capitan la 15 ani ilustrat, Jules Verne"), so
    leaving it in scores that padding as if it were a title match — enough
    to beat the correct edition. Author agreement is still measured, once,
    through its own term.
    """
    reference = normalize_for_match(reference_text)
    title = normalize_for_match(candidate.title)

    if not candidate.authors:
        return fuzz.token_set_ratio(title, reference) / 100

    authors = normalize_for_match(" ".join(candidate.authors))
    author_tokens = set(authors.split())

    title_score = fuzz.token_set_ratio(
        _without_tokens(title, author_tokens), _without_tokens(reference, author_tokens)
    )
    author_score = fuzz.token_set_ratio(authors, reference)
    return (0.7 * title_score + 0.3 * author_score) / 100


def _parse_vision_response(raw: str) -> tuple[str, str | None] | None:
    """Extracts `(title, author)` from the vision model's reply.

    Moondream frequently overruns the two keys it was asked for, inventing
    extra fields until it hits the token cap and leaves the JSON truncated
    mid-string. Strict parsing throws that away entirely, failing the whole
    job over a reply whose useful part arrived intact — so we fall back to
    reading the two keys directly out of the text.

    Args:
        raw: The model's unparsed reply.

    Returns:
        The title and optional author, or `None` if no title could be found.
    """

    def clean(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or stripped.lower() in {"null", "none", "n/a", "unknown"}:
            return None
        return stripped

    title: str | None = None
    author: str | None = None

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            raw_title = data.get("title")
            raw_author = data.get("author")
            title = clean(raw_title) if isinstance(raw_title, str) else None
            author = clean(raw_author) if isinstance(raw_author, str) else None
    except ValueError:
        pass

    if title is None:
        title_match = re.search(r'"title"\s*:\s*"([^"]*)"', raw)
        title = clean(title_match.group(1)) if title_match else None
        author_match = re.search(r'"author"\s*:\s*"([^"]*)"', raw)
        author = clean(author_match.group(1)) if author_match else None

    if title is None:
        return None
    return title, author


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

        The vision model runs first. OCR is the fallback: it only runs when
        the vision model's guess isn't confirmed by the catalog (or fails
        outright), and if it does run, the higher-confidence result of the
        two wins — a confirmed OCR reading can still beat an unconfirmed
        vision guess.

        Args:
            image_bytes: The raw, unprocessed uploaded image content.

        Returns:
            The recognized title/author with a confidence score.

        Raises:
            ImageProcessingFailed: If the image cannot be decoded.
            CoverNotRecognized: If neither the vision model nor OCR
                produced a usable title.
        """
        prepared = preprocess_cover(image_bytes)

        vision_result: CoverIdentification | None
        try:
            vision_result = await self._identify_via_vision_model(prepared.jpeg_bytes)
        except CoverNotRecognized:
            vision_result = None

        if (
            vision_result is not None
            and vision_result.confidence >= self._settings.vision_confidence_threshold
        ):
            return vision_result

        ocr_result = await self._identify_via_ocr_fallback(prepared.jpeg_bytes)

        if ocr_result is None:
            if vision_result is None:
                raise CoverNotRecognized("Could not identify the book from the cover image.")
            return vision_result

        if vision_result is None:
            return ocr_result

        best = max(vision_result, ocr_result, key=lambda result: result.confidence)
        logger.info(
            "vision_ocr_fallback_chose",
            method=best.method,
            vision_confidence=round(vision_result.confidence, 3),
            ocr_confidence=round(ocr_result.confidence, 3),
        )
        return best

    async def _identify_via_ocr_fallback(self, image_bytes: bytes) -> CoverIdentification | None:
        """Runs OCR and confirms it against the catalog, as the fallback to the vision model.

        Returns:
            The OCR-based identification, or `None` if OCR read too little
            text to be worth trying at all.
        """
        ocr_candidates = await self._ocr.extract_candidates(image_bytes)
        ocr_text = self._reading_order_text(ocr_candidates)

        has_usable_ocr = (
            bool(ocr_candidates) and len(ocr_text.strip()) >= self._settings.vision_min_ocr_chars
        )
        if not has_usable_ocr:
            logger.info("vision_ocr_too_sparse", char_count=len(ocr_text.strip()))
            return None

        return await self._identify_from_ocr(ocr_candidates, ocr_text)

    @staticmethod
    def _reading_order_text(candidates: list[TextCandidate]) -> str:
        """Joins OCR lines in the order they appear on the cover, top to bottom.

        `OcrService` sorts candidates by text height so the title is easy to
        pick out, but that order scrambles the phrase itself — a cover reading
        "Ocolul Pamantului / in optzeci de zile" came back as
        "in optzeci de zile Ocolul Pamantului", which no catalog matches.
        Search queries therefore use the engine's original line order.
        """
        return " ".join(c.text for c in sorted(candidates, key=lambda c: c.line_index))

    async def _identify_from_ocr(
        self, ocr_candidates: list[TextCandidate], ocr_text: str
    ) -> CoverIdentification:
        """Confirms an OCR reading against the catalog, trusting OCR if it can't."""
        outcome_available, match, known_authors = await self._best_lookup_match(
            self._build_queries(ocr_candidates), ocr_text
        )

        if match is not None and match.score >= self._settings.vision_confidence_threshold:
            logger.info("vision_identified_via_ocr", score=round(match.score, 3))
            return self._build_result(
                title=match.candidate.title,
                author=match.candidate.authors[0] if match.candidate.authors else None,
                confidence=match.score,
                method="ocr",
            )

        # The catalog could not confirm the reading — either it does not have
        # this edition (routine for Romanian printings) or it was unreachable.
        # Still returned (not discarded): `identify()` compares this against
        # the vision model's own unconfirmed guess and keeps whichever is
        # more confident.
        title, author = self._split_title_and_author(ocr_candidates, known_authors)
        logger.info(
            "vision_identified_via_ocr_unconfirmed",
            catalog_available=outcome_available,
            best_score=round(match.score, 3) if match else None,
            author_from_catalog=author is not None and bool(known_authors),
        )
        return self._build_result(
            title=title,
            author=author,
            confidence=self._settings.vision_ocr_unconfirmed_confidence,
            method="ocr",
        )

    def _build_queries(self, ocr_candidates: list[TextCandidate]) -> list[str]:
        """Builds the catalog search queries to try, most specific first.

        The first query keeps only *prominent* text — lines at least
        `_PROMINENT_HEIGHT_RATIO` as tall as the largest one. Covers carry a
        lot of small print (subtitle, blurb, "PESTE 4 MILIOANE DE EXEMPLARE
        VÂNDUTE", the publisher's name), and including it buries the title in
        noise: the Goggins cover searched as "NU STAPANESTE-TI MINTEA SI
        REUSESTE IMPOSIBILUL RANI DAVID GOGGINS PESTE 4 MILIOANE…" and matched
        nothing, while "NU MA POTI RANI DAVID GOGGINS" matches exactly.

        Word order barely matters here — scoring uses `token_set_ratio` and
        the catalogs are search engines — so these are token-selection
        choices, not ordering ones.
        """
        reading_order = sorted(ocr_candidates, key=lambda c: c.line_index)
        tallest = max(c.relative_height for c in ocr_candidates)
        cutoff = tallest * _PROMINENT_HEIGHT_RATIO

        prominent = [c for c in reading_order if c.relative_height >= cutoff]
        queries = [" ".join(c.text for c in prominent)]

        # Everything OCR read, in case the title is set in small type and the
        # prominence filter dropped it.
        full_query = " ".join(c.text for c in reading_order[:_MAX_TOP_LINES_FOR_QUERY])
        if full_query and full_query not in queries:
            queries.append(full_query)

        # Last resort: the single largest line on its own.
        largest = max(ocr_candidates, key=lambda c: c.relative_height).text
        if largest and largest not in queries:
            queries.append(largest)

        return [q for q in queries if q.strip()]

    async def _best_lookup_match(
        self, queries: list[str], reference_text: str
    ) -> tuple[bool, _ScoredCandidate | None, list[str]]:
        """Runs each query, returning `(available, best match, every author seen)`.

        The author list matters even when no candidate scores high enough to
        confirm the title: catalogs know "Jules Verne" regardless of which
        edition is on the cover, and that is what lets
        `_split_title_and_author` tell the author line from the title.
        """
        best: _ScoredCandidate | None = None
        any_available = False
        known_authors: list[str] = []

        for query in queries:
            if not query.strip():
                continue
            outcome = await self._lookup.search(query)
            any_available = any_available or outcome.available
            for candidate in outcome.candidates:
                known_authors.extend(candidate.authors)
                score = _score_candidate(candidate, reference_text)
                if best is None or score > best.score:
                    best = _ScoredCandidate(candidate=candidate, score=score)

            # Queries are ordered most-trustworthy first, so a confident match
            # ends the search: running the vaguer ones afterwards can only
            # replace a good answer with a worse one that happens to score
            # higher (an author-only query returning the wrong edition), and
            # it spends Google Books quota for nothing.
            if best is not None and best.score >= self._settings.vision_confidence_threshold:
                break

        return any_available, best, known_authors

    @staticmethod
    def _split_title_and_author(
        candidates: list[TextCandidate], known_authors: list[str]
    ) -> tuple[str, str | None]:
        """Decides which OCR lines are the author and which form the title.

        Used only when no catalog could confirm the reading. Text size is a
        poor signal here — covers routinely print a well-known author's name
        larger than the title — so the author line is identified by matching
        against author names the catalog returned, even though it could not
        confirm the title itself. Whatever is left becomes the title, joined
        in reading order so a title split across lines stays intact.

        With no catalog authors to match (total outage), it falls back to
        the shortest line that reads like a person's name.

        Args:
            candidates: The OCR lines for this cover.
            known_authors: Author names seen in catalog results, if any.

        Returns:
            The title, and the author if one could be identified.
        """
        reading_order = sorted(candidates, key=lambda c: c.line_index)
        author_index: int | None = None

        normalized_authors = [normalize_for_match(a) for a in known_authors if a.strip()]
        if normalized_authors:
            best_score = 0.0
            for index, candidate in enumerate(reading_order):
                line = normalize_for_match(candidate.text)
                if not line:
                    continue
                score = max(fuzz.token_set_ratio(line, author) for author in normalized_authors)
                if score >= _AUTHOR_LINE_MATCH_MIN and score > best_score:
                    best_score, author_index = score, index

        if author_index is None:
            name_lines = [
                index
                for index, candidate in enumerate(reading_order)
                if _looks_like_person_name(candidate.text.strip())
            ]
            # A title wrapped over several lines sets them all at the same
            # large size ("NU" / "MA POTI" / "RANI"), and in caps each looks
            # name-shaped. The author is normally alone at its own size, so
            # prefer a line that does not share its height with others.
            heights = [round(c.relative_height) for c in reading_order]
            unshared = [i for i in name_lines if heights.count(heights[i]) == 1]
            pool = unshared or name_lines
            if pool:
                author_index = min(pool, key=lambda i: len(reading_order[i].text))

        author = reading_order[author_index].text.strip() if author_index is not None else None
        title_parts = [
            candidate.text.strip()
            for index, candidate in enumerate(reading_order)
            if index != author_index and candidate.text.strip()
        ]

        # Every line looked like the author — keep the reading as the title
        # rather than returning nothing at all.
        title = " ".join(title_parts) if title_parts else reading_order[0].text.strip()
        return title, author

    async def _identify_via_vision_model(self, image_bytes: bytes) -> CoverIdentification:
        """Runs the vision model and validates its guess against the catalog."""
        raw = await self._ollama.generate(
            model=self._settings.vision_model,
            prompt=_VISION_PROMPT,
            images=[image_bytes],
            format="json",
            options={"num_predict": self._settings.ollama_vision_num_predict},
        )
        parsed = _parse_vision_response(raw)
        if parsed is None:
            logger.warning("vision_model_unparsable", raw=raw[:200])
            raise CoverNotRecognized("Could not identify the book from the cover image.")

        title, author = parsed
        reference_text = f"{title} {author}" if author else title
        _available, match, _authors = await self._best_lookup_match([title], reference_text)

        if match is not None and match.score >= self._settings.vision_confidence_threshold:
            logger.info("vision_identified_via_model_confirmed", score=round(match.score, 3))
            return self._build_result(
                title=match.candidate.title,
                author=match.candidate.authors[0] if match.candidate.authors else author,
                confidence=match.score,
                method="vision",
            )

        logger.info("vision_identified_via_model_unconfirmed")
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
        A `VisionService` using RapidOCR, the catalog lookup chain, and
        whichever AI backend `Settings.ai_provider` selects (Groq by
        default, Ollama as the local fallback — see
        `app.services.groq_client.get_active_ai_client`). Cheap to call
        repeatedly: the expensive OCR model session is a cached singleton
        (see `app.services.ocr_service._get_rapid_ocr`).
    """
    settings = get_settings()
    return VisionService(
        ocr=OcrService(RapidOcrEngine()),
        ollama=get_active_ai_client(),
        lookup=build_title_lookup(),
        settings=settings,
    )

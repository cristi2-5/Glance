"""Fake implementations of the service protocols, for fast, offline tests."""

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models.book import SourceName
from app.services.ocr_service import OcrEngine, TextCandidate
from app.services.ollama_client import OllamaClient
from app.services.sources.base import SourceResult
from app.services.title_lookup import BookCandidate, LookupOutcome, TitleLookup


class FakeOcrEngine(OcrEngine):
    """`OcrEngine` returning a fixed, pre-scripted list of text lines."""

    def __init__(self, candidates: list[TextCandidate] | None = None) -> None:
        self.candidates = candidates if candidates is not None else []

    def read(self, image_bytes: bytes) -> list[TextCandidate]:
        return self.candidates


@dataclass
class RecordedGenerateCall:
    """One recorded call to `FakeOllamaClient.generate`."""

    model: str
    prompt: str
    images: list[bytes] | None
    format: str | None
    options: dict[str, Any] | None


class FakeOllamaClient(OllamaClient):
    """`OllamaClient` returning a fixed response and recording every call made to it."""

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.calls: list[RecordedGenerateCall] = []

    async def generate(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        format: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            RecordedGenerateCall(
                model=model, prompt=prompt, images=images, format=format, options=options
            )
        )
        return self.response


class FakeTitleLookup(TitleLookup):
    """`TitleLookup` returning a fixed outcome for every query.

    `available=False` simulates an unreachable catalog (quota exhausted,
    timeout) as opposed to one that simply has no matching book.
    """

    def __init__(
        self, candidates: list[BookCandidate] | None = None, available: bool = True
    ) -> None:
        self.candidates = candidates if candidates is not None else []
        self.available = available
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 5) -> LookupOutcome:
        self.queries.append(query)
        return LookupOutcome(candidates=list(self.candidates), available=self.available)


class FakeContentSource:
    """`ContentSource` returning a scripted result and recording its calls.

    Set `raises` to simulate a source that blows up rather than reporting
    a failure — `BookDataFetcher` must contain that, not propagate it.
    """

    def __init__(
        self,
        source: SourceName,
        result: SourceResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._name = source
        self._result = result if result is not None else SourceResult.no_match(source)
        self._raises = raises
        self.calls: list[tuple[str, str | None]] = []

    @property
    def name(self) -> SourceName:
        return self._name

    async def fetch(self, title: str, author: str | None = None) -> SourceResult:
        self.calls.append((title, author))
        if self._raises is not None:
            raise self._raises
        return self._result


class FakeCoverFallback:
    """`CoverFallback` returning a fixed URL and recording every call.

    `calls` staying empty is the assertion that matters most: the fallback
    exists to close a gap, and must not fire when a source already
    supplied a cover.
    """

    def __init__(self, cover_url: str | None = None) -> None:
        self.cover_url = cover_url
        self.calls: list[tuple[str | None, str | None, str | None]] = []

    async def find_cover(
        self,
        isbn_13: str | None,
        isbn_10: str | None,
        wikipedia_article: str | None,
    ) -> str | None:
        self.calls.append((isbn_13, isbn_10, wikipedia_article))
        return self.cover_url


_WORD = re.compile(r"[a-z0-9']+")


class HashingEmbeddingClient:
    """`EmbeddingClient` producing deterministic bag-of-words vectors.

    Not a stub returning constants: the retrieval tests need similarity
    that actually *means* something, or "the reception query retrieved the
    reception passage" would be luck rather than behaviour. Each word is
    hashed into a bucket and the vector is L2-normalized, so cosine
    distance tracks lexical overlap — crude next to `nomic-embed-text`,
    but real, offline, and identical on every run.

    Nothing here contacts Ollama, so the suite keeps its "zero network
    calls" property.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[bucket] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # An all-zero vector has no direction, and Chroma's cosine
            # space cannot rank it. One arbitrary non-zero component keeps
            # empty or punctuation-only text queryable instead of erroring.
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]


class ScriptedSummaryClient(OllamaClient):
    """`OllamaClient` whose reply is computed from the prompt it receives.

    The synthesis tests need to script a reply that cites *real* chunk
    ids, and those ids are only known once the corpus has been ingested —
    they appear in the prompt. A responder callable lets a test read the
    ids out of the prompt and answer with them, or deliberately answer
    with ids that do not exist, which is how the hallucination-rejection
    path is exercised.
    """

    def __init__(self, responder: Callable[[str], str]) -> None:
        self._responder = responder
        self.calls: list[RecordedGenerateCall] = []

    async def generate(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        format: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            RecordedGenerateCall(
                model=model, prompt=prompt, images=images, format=format, options=options
            )
        )
        return self._responder(prompt)

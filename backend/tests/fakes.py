"""Fake implementations of the service protocols, for fast, offline tests."""

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

"""Fake implementations of the vision-layer protocols, for fast, offline tests."""

from dataclasses import dataclass
from typing import Any

from app.services.ocr_service import OcrEngine, TextCandidate
from app.services.ollama_client import OllamaClient
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

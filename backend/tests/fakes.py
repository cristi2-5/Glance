"""Fake implementations of the service protocols, for fast, offline tests."""

import hashlib
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import BlobNotFound, StorageUnavailable
from app.models.book import SourceName
from app.models.vector import EMBEDDING_DIMENSIONS
from app.services.ocr_service import OcrEngine, TextCandidate
from app.services.ollama_client import OllamaClient
from app.services.sources.base import (
    BookMetadata,
    DiscoveryQuery,
    DiscoveryResult,
    SourceResult,
)
from app.services.storage import JobMessage, QueuedMessage
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


class FakeCandidateSource:
    """`CandidateSource` answering discovery queries from a script.

    Keyed by query label (`"subject:Science fiction"`) so a test can give
    one answer per seed and assert on which seeds were actually used —
    which is how the "generic subjects are never queried" property is
    checked. Unlisted queries answer with nothing, the same as a catalog
    that simply holds no such books.

    `available` covers the other case entirely: a source that could not be
    reached, which must not be recorded as a completed discovery run.
    """

    def __init__(
        self,
        source: SourceName,
        results: dict[str, list[BookMetadata]] | None = None,
        available: bool = True,
        raises: Exception | None = None,
        unavailable_after: int | None = None,
    ) -> None:
        self._name = source
        self._results = results or {}
        self._available = available
        self._raises = raises
        # Simulates a catalog that answers for a while and then goes down
        # mid-run — the shape that made a synchronous request hang for
        # minutes before discovery learned to stop asking.
        self._unavailable_after = unavailable_after
        self.queries: list[str] = []
        #: Wall-clock offsets of each call, so a test can assert the
        #: queries were paced rather than fired all at once.
        self.call_times: list[float] = []
        self._started = time.monotonic()

    @property
    def name(self) -> SourceName:
        return self._name

    async def discover(self, query: DiscoveryQuery, limit: int) -> DiscoveryResult:
        self.queries.append(query.label())
        self.call_times.append(time.monotonic() - self._started)
        if self._raises is not None:
            raise self._raises
        if not self._available:
            return DiscoveryResult.unavailable(self._name)
        if self._unavailable_after is not None and len(self.queries) > self._unavailable_after:
            return DiscoveryResult.unavailable(self._name)
        return DiscoveryResult(
            source=self._name,
            candidates=list(self._results.get(query.label(), []))[:limit],
        )


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

    The width matches the real model (768), because the `vector(768)` columns
    reject any other size.

    Nothing here contacts Ollama, so the suite keeps its "zero network
    calls" property.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
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
            # An all-zero vector has no direction: pgvector's cosine distance
            # to it is NaN, which cannot be ranked. One arbitrary non-zero
            # component keeps empty or punctuation-only text queryable.
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


class InMemoryBlobStore:
    """In-memory `BlobStore`; `fail` makes every call raise `StorageUnavailable`."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.fail = False
        self.fail_delete = False

    async def put(self, name: str, data: bytes, content_type: str = "image/jpeg") -> None:
        if self.fail:
            raise StorageUnavailable("Blob storage is unavailable.")
        self.blobs[name] = data

    async def get(self, name: str) -> bytes:
        if self.fail:
            raise StorageUnavailable("Blob storage is unavailable.")
        if name not in self.blobs:
            raise BlobNotFound("The stored photo no longer exists.")
        return self.blobs[name]

    async def delete(self, name: str) -> None:
        if self.fail or self.fail_delete:
            raise StorageUnavailable("Blob storage is unavailable.")
        self.blobs.pop(name, None)


class InMemoryJobQueue:
    """In-memory `JobQueue` with the delivery semantics the queue worker relies on.

    A received message turns invisible until deleted; `expire_visibility`
    stands in for the visibility timeout elapsing, making every received but
    undeleted message deliverable again with a higher `dequeue_count`. (The
    real behaviour is proven against Azurite in `test_storage.py`.)
    """

    def __init__(self) -> None:
        self.fail_send = False
        self.fail_receive = False
        self.fail_extend = False
        #: Every (message_id, visibility_timeout) passed to `extend`.
        self.extended: list[tuple[str, int]] = []
        #: Receipt currently valid per message id; a stale one deletes nothing.
        self._receipts: dict[str, str] = {}
        self._next_id = 1
        # id -> [body, dequeue_count, invisible]
        self._messages: dict[str, list[Any]] = {}
        self.deleted: list[str] = []

    @property
    def bodies(self) -> list[str]:
        """The bodies of every message still on the queue."""
        return [entry[0] for entry in self._messages.values()]

    async def send(self, message: JobMessage) -> None:
        if self.fail_send:
            raise StorageUnavailable("Queue storage is unavailable.")
        self.send_raw(message.model_dump_json(exclude_none=True))

    def send_raw(self, body: str) -> None:
        """Puts arbitrary text on the queue (for malformed-message tests)."""
        self._messages[str(self._next_id)] = [body, 0, False]
        self._next_id += 1

    async def receive(self, visibility_timeout: int) -> QueuedMessage | None:
        if self.fail_receive:
            raise StorageUnavailable("Queue storage is unavailable.")
        for message_id, entry in self._messages.items():
            if not entry[2]:
                entry[1] += 1
                entry[2] = True
                self._receipts[message_id] = f"receipt-{entry[1]}"
                return QueuedMessage(
                    message_id=message_id,
                    pop_receipt=f"receipt-{entry[1]}",
                    body=entry[0],
                    dequeue_count=entry[1],
                )
        return None

    async def extend(self, message: QueuedMessage, visibility_timeout: int) -> str:
        if self.fail_extend:
            raise StorageUnavailable("Queue storage is unavailable.")
        if self._receipts.get(message.message_id) != message.pop_receipt:
            raise StorageUnavailable("stale receipt")
        self.extended.append((message.message_id, visibility_timeout))
        receipt = f"{message.pop_receipt}+"
        self._receipts[message.message_id] = receipt
        return receipt

    async def delete(self, message: QueuedMessage) -> None:
        # Like Azure: a stale receipt deletes nothing.
        if self._receipts.get(message.message_id) != message.pop_receipt:
            return
        self._messages.pop(message.message_id, None)
        self.deleted.append(message.message_id)

    def expire_visibility(self) -> None:
        """Makes every received-but-undeleted message deliverable again."""
        for entry in self._messages.values():
            entry[2] = False

"""Local embedding generation via Ollama's `nomic-embed-text`.

**Embeddings are local, always, regardless of `ai_provider`.** The
Groq pivot moved vision and summary *generation* to the cloud; it did not
move storage or embeddings, and the "Strict rules" section of CLAUDE.md
holds that line. This module therefore talks to Ollama directly and has
no provider switch — adding one would be the bug, not the feature.

`nomic-embed-text` is ~275 MB resident and produces 768-dimensional
vectors. It is intended to stay loaded permanently (long keep-alive):
it is small enough not to contend with anything else on a 7.4 GB laptop,
and reloading it per request would cost seconds on every scan.

The same vectors serve RAG (Module 5) and recommendations (Module 6) —
see the "Dual-purpose embeddings" decision. Nothing here is
summary-specific for that reason.
"""

import asyncio
from functools import lru_cache
from typing import Protocol

import httpx
import structlog
from ollama import AsyncClient, ResponseError

from app.core.config import Settings, get_settings
from app.core.exceptions import OllamaUnavailable

logger = structlog.get_logger(__name__)

_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)
_BACKOFF_BASE_SECONDS = 0.5


class EmbeddingClient(Protocol):
    """Abstraction over an embedding backend, so it can be faked in tests."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeds a batch of texts.

        Args:
            texts: The texts to embed. May be empty.

        Returns:
            One vector per input text, in the same order.

        Raises:
            OllamaUnavailable: If Ollama cannot be reached after retries.
        """
        ...


class OllamaEmbeddingClient:
    """`EmbeddingClient` backed by Ollama, with timeout + retry.

    Batches go to Ollama's `embed` endpoint in one request rather than one
    per chunk: a book's corpus is typically a few dozen chunks, and paying
    a round trip each would dominate ingestion time.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = settings.ollama_embedding_model
        self._client = AsyncClient(
            host=settings.ollama_host, timeout=settings.ollama_request_timeout_seconds
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """See `EmbeddingClient.embed`.

        Retries `Settings.ollama_max_retries` times with exponential
        backoff on connection/timeout failures. A well-formed error from
        Ollama (`ResponseError` — typically the model not being pulled) is
        not retried, since retrying repeats the same failure.

        Raises:
            OllamaUnavailable: If Ollama is unreachable after retries, or
                returned a vector count that does not match the input.
        """
        if not texts:
            return []

        attempts = self._settings.ollama_max_retries + 1
        last_exception: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.embed(
                    model=self._model,
                    input=texts,
                    keep_alive=self._settings.ollama_embedding_keep_alive,
                )
                vectors = [list(vector) for vector in response.embeddings]
                if len(vectors) != len(texts):
                    # A silent length mismatch would misalign every chunk
                    # against its vector — worse than an outage, because
                    # it corrupts the store instead of failing the request.
                    raise OllamaUnavailable(
                        f"Embedding model {self._model} returned {len(vectors)} vectors "
                        f"for {len(texts)} inputs."
                    )
                return vectors
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exception = exc
                logger.warning(
                    "embedding_retry",
                    model=self._model,
                    attempt=attempt,
                    max_attempts=attempts,
                    error=str(exc),
                )
                if attempt < attempts:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            except ResponseError as exc:
                logger.error("embedding_response_error", model=self._model, error=str(exc))
                raise OllamaUnavailable(f"Ollama returned an error: {exc}") from exc

        raise OllamaUnavailable(
            f"Embedding model {self._model} is unavailable after {attempts} attempts."
        ) from last_exception


@lru_cache
def get_embedding_client() -> OllamaEmbeddingClient:
    """Returns the (cached) shared `OllamaEmbeddingClient` instance.

    Returns:
        The `OllamaEmbeddingClient` configured from application settings.
    """
    return OllamaEmbeddingClient(get_settings())

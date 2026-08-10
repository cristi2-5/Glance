"""Shared async wrapper around the `ollama` Python client.

Used by the vision fallback (Module 3) and, later, by the RAG summary and
embedding steps (Module 5) — the retry/timeout policy and the `Protocol`
boundary belong here once, not per-caller.
"""

import asyncio
from functools import lru_cache
from typing import Any, Protocol

import httpx
import structlog
from ollama import AsyncClient, ResponseError

from app.core.config import Settings, get_settings
from app.core.exceptions import OllamaUnavailable

logger = structlog.get_logger(__name__)

_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)
_BACKOFF_BASE_SECONDS = 0.5


class OllamaClient(Protocol):
    """Abstraction over an Ollama backend, so it can be faked in tests."""

    async def generate(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        format: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Generates a completion from a local Ollama model.

        Args:
            model: The Ollama model name (e.g. `moondream`, `llama3.2`).
            prompt: The text prompt.
            images: Optional raw image bytes, for vision models.
            format: Optional response format constraint (`"json"`).
            options: Optional Ollama runtime options (e.g. `num_predict`,
                `temperature`).

        Returns:
            The generated response text.

        Raises:
            OllamaUnavailable: If Ollama cannot be reached after retries.
        """
        ...


class AsyncOllamaClient:
    """`OllamaClient` backed by `ollama.AsyncClient`, with timeout + retry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncClient(
            host=settings.ollama_host, timeout=settings.ollama_request_timeout_seconds
        )

    async def generate(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        format: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """See `OllamaClient.generate`.

        Retries `Settings.ollama_max_retries` times, with exponential
        backoff, on connection/timeout failures only — a well-formed error
        response from Ollama (`ResponseError`) is not retried, since retrying
        would just repeat the same failure.
        """
        attempts = self._settings.ollama_max_retries + 1
        last_exception: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.generate(
                    model=model,
                    prompt=prompt,
                    images=images,
                    format=format,  # type: ignore[arg-type]
                    options=options,
                )
                return response.response or ""
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exception = exc
                logger.warning(
                    "ollama_generate_retry",
                    model=model,
                    attempt=attempt,
                    max_attempts=attempts,
                    error=str(exc),
                )
                if attempt < attempts:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            except ResponseError as exc:
                logger.error("ollama_generate_response_error", model=model, error=str(exc))
                raise OllamaUnavailable(f"Ollama returned an error: {exc}") from exc

        raise OllamaUnavailable(
            f"Ollama at {self._settings.ollama_host} is unavailable after {attempts} attempts."
        ) from last_exception


@lru_cache
def get_ollama_client() -> AsyncOllamaClient:
    """Returns the (cached) shared `AsyncOllamaClient` instance.

    Returns:
        The `AsyncOllamaClient` configured from application settings.
    """
    return AsyncOllamaClient(get_settings())

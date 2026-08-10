"""Groq Cloud client — the vision and LLM-generation backend when
`Settings.ai_provider == "groq"`.

Structurally implements the same `OllamaClient` protocol as
`app.services.ollama_client.AsyncOllamaClient` (`generate(model, prompt,
images, format, options) -> str`), so `VisionService` and the RAG service
(Module 5) call either backend identically — swapping `Settings.ai_provider`
is the only thing that changes between them.
"""

import asyncio
import base64
from functools import lru_cache
from typing import Any

import structlog
from groq import APIConnectionError, AsyncGroq, GroqError, InternalServerError, RateLimitError
from groq import APIStatusError as GroqAPIStatusError
from groq.types.chat import (
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionUserMessageParam,
)

from app.core.config import Settings, get_settings
from app.core.exceptions import AIProviderUnavailable
from app.services.ollama_client import OllamaClient, get_ollama_client

logger = structlog.get_logger(__name__)

# Retried with backoff: connection/timeout failures, rate limits, and 5xx —
# all transient. qwen/qwen3.6-27b is a Groq preview model, so rate limiting
# and server-side instability are expected more often than for a GA model.
_RETRYABLE_EXCEPTIONS = (APIConnectionError, RateLimitError, InternalServerError)
_BACKOFF_BASE_SECONDS = 0.5


class AsyncGroqClient:
    """`OllamaClient`-shaped wrapper around `groq.AsyncGroq`, with timeout + retry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        try:
            self._client = AsyncGroq(
                api_key=settings.groq_api_key,
                timeout=settings.groq_request_timeout_seconds,
                max_retries=0,  # retried explicitly below, with logging
            )
        except GroqError as exc:
            # Missing/empty api_key raises here, at construction — before any
            # request is made. Turned into a domain error so a misconfigured
            # GROQ_API_KEY surfaces as a 503 through the normal error
            # handling path instead of an unhandled exception on first use.
            raise AIProviderUnavailable(f"Groq client could not be configured: {exc}") from exc

    async def generate(
        self,
        model: str,
        prompt: str,
        images: list[bytes] | None = None,
        format: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """See `OllamaClient.generate`.

        Retries `Settings.groq_max_retries` times, with exponential
        backoff, on connection/timeout/rate-limit/server errors. A
        request-shaped error (bad API key, invalid model, malformed
        request) is not retried, since retrying would just repeat the same
        failure — it is raised immediately as `AIProviderUnavailable`.
        """
        message = ChatCompletionUserMessageParam(
            role="user", content=self._build_content(prompt, images)
        )
        kwargs = self._build_kwargs(format, options)
        attempts = self._settings.groq_max_retries + 1
        last_exception: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=[message],
                    **kwargs,
                )
                return response.choices[0].message.content or ""
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exception = exc
                logger.warning(
                    "groq_generate_retry",
                    model=model,
                    attempt=attempt,
                    max_attempts=attempts,
                    error=str(exc),
                )
                if attempt < attempts:
                    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            except GroqAPIStatusError as exc:
                logger.error("groq_generate_response_error", model=model, error=str(exc))
                raise AIProviderUnavailable(f"Groq returned an error: {exc}") from exc

        raise AIProviderUnavailable(
            f"Groq model {model} is unavailable after {attempts} attempts."
        ) from last_exception

    @staticmethod
    def _build_content(
        prompt: str, images: list[bytes] | None
    ) -> str | list[ChatCompletionContentPartTextParam | ChatCompletionContentPartImageParam]:
        """Builds the message `content`.

        Plain text with no images; an OpenAI-style multimodal parts list
        (text + base64 `image_url` data URIs) when images are attached.
        """
        if not images:
            return prompt

        parts: list[ChatCompletionContentPartTextParam | ChatCompletionContentPartImageParam] = [
            ChatCompletionContentPartTextParam(type="text", text=prompt)
        ]
        for image_bytes in images:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            parts.append(
                ChatCompletionContentPartImageParam(
                    type="image_url",
                    image_url={"url": f"data:image/jpeg;base64,{encoded}"},
                )
            )
        return parts

    def _build_kwargs(self, format: str | None, options: dict[str, Any] | None) -> dict[str, Any]:
        """Translates the provider-neutral `format`/`options` into Groq's own
        chat-completion parameters (`response_format`, `max_tokens`, ...).
        """
        kwargs: dict[str, Any] = {}
        if format == "json":
            kwargs["response_format"] = {"type": "json_object"}
            # Groq's models here (qwen/qwen3.6-27b, openai/gpt-oss-120b) are
            # reasoning models: left alone, they spend the token budget on a
            # hidden chain-of-thought before ever emitting the JSON answer,
            # which empties out the visible content under a tight cap and
            # fails Groq's own JSON validation (400 json_validate_failed).
            # We're extracting a small structured answer, not asking for
            # reasoning, so skip it.
            kwargs["reasoning_effort"] = "none"
        if options:
            if "num_predict" in options:
                # `num_predict` is an Ollama-tuned cap (short, non-reasoning
                # models). Groq's reasoning models need more headroom even
                # with reasoning off, so it's a floor here, not a ceiling.
                kwargs["max_tokens"] = max(options["num_predict"], self._settings.groq_max_tokens)
            if "temperature" in options:
                kwargs["temperature"] = options["temperature"]
        return kwargs


@lru_cache
def get_groq_client() -> AsyncGroqClient:
    """Returns the (cached) shared `AsyncGroqClient` instance.

    Returns:
        The `AsyncGroqClient` configured from application settings.
    """
    return AsyncGroqClient(get_settings())


def get_active_ai_client() -> OllamaClient:
    """Returns the vision/LLM client for the currently configured `ai_provider`.

    The single switch point for the local-vs-cloud pivot: callers ask for
    "the active AI client" instead of picking a backend directly, so
    reverting `Settings.ai_provider` to `"ollama"` needs no code change
    anywhere else.

    Returns:
        `get_groq_client()` when `Settings.ai_provider == "groq"`,
        `get_ollama_client()` otherwise.
    """
    settings = get_settings()
    if settings.ai_provider == "groq":
        return get_groq_client()
    return get_ollama_client()

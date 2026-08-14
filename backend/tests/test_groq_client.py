"""Tests for `AsyncGroqClient` — request shaping, retries, error mapping.

The request *shape* is the point of most of these. Groq validates
parameters server-side and answers 400 for a value the model does not
accept, which `generate` deliberately does not retry — so a wrong constant
here is not a degraded call, it is every call to that model failing. This
happened once, with `reasoning_effort="none"`: valid for the Qwen3 vision
model, rejected outright by `openai/gpt-oss-120b`, which took down every
RAG summary while the vision path stayed green.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from groq import APIConnectionError, RateLimitError
from httpx import Request, Response

from app.core.config import Settings
from app.core.exceptions import AIProviderUnavailable
from app.services.groq_client import AsyncGroqClient


@dataclass
class _FakeMessage:
    content: str | None


@dataclass
class _FakeChoice:
    message: _FakeMessage
    finish_reason: str = "stop"


@dataclass
class _FakeCompletion:
    choices: list[_FakeChoice]


@dataclass
class _FakeCompletions:
    """Stands in for `client.chat.completions`, recording every call."""

    content: str | None = "{}"
    finish_reason: str = "stop"
    errors: list[Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> _FakeCompletion:
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return _FakeCompletion(
            choices=[
                _FakeChoice(
                    message=_FakeMessage(content=self.content),
                    finish_reason=self.finish_reason,
                )
            ]
        )


@dataclass
class _FakeChat:
    completions: _FakeCompletions


@dataclass
class _FakeGroq:
    chat: _FakeChat


def _build_client(settings: Settings, completions: _FakeCompletions) -> AsyncGroqClient:
    """An `AsyncGroqClient` with its `groq.AsyncGroq` swapped for a fake."""
    client = AsyncGroqClient(settings)
    client._client = _FakeGroq(chat=_FakeChat(completions=completions))  # type: ignore[assignment]
    return client


@pytest.fixture
def settings() -> Settings:
    return Settings(groq_api_key="test-key", groq_max_retries=2)


@pytest.fixture
def completions() -> _FakeCompletions:
    return _FakeCompletions()


async def test_llm_model_never_asks_to_disable_reasoning(
    settings: Settings, completions: _FakeCompletions
) -> None:
    """`openai/gpt-oss-120b` has no "off" — `none` is a 400, not a slow call."""
    client = _build_client(settings, completions)

    await client.generate(model=settings.groq_llm_model, prompt="p", format="json")

    assert completions.calls[0]["reasoning_effort"] in {"low", "medium", "high"}


async def test_vision_model_disables_reasoning(
    settings: Settings, completions: _FakeCompletions
) -> None:
    """The Qwen3 vision model does accept `none`, and we want it: the answer
    is two short keys, and thinking first truncates them."""
    client = _build_client(settings, completions)

    await client.generate(model=settings.groq_vision_model, prompt="p", format="json")

    assert completions.calls[0]["reasoning_effort"] == "none"


async def test_unknown_model_sends_no_reasoning_effort(
    settings: Settings, completions: _FakeCompletions
) -> None:
    """We can't know which vocabulary an unconfigured model accepts, and a
    wrong guess is a hard 400 — so send nothing and take the default."""
    client = _build_client(settings, completions)

    await client.generate(model="some/other-model", prompt="p", format="json")

    assert "reasoning_effort" not in completions.calls[0]


async def test_json_format_requests_a_json_object(
    settings: Settings, completions: _FakeCompletions
) -> None:
    client = _build_client(settings, completions)

    await client.generate(model=settings.groq_llm_model, prompt="p", format="json")

    assert completions.calls[0]["response_format"] == {"type": "json_object"}


async def test_plain_format_sends_no_response_format(
    settings: Settings, completions: _FakeCompletions
) -> None:
    client = _build_client(settings, completions)

    await client.generate(model=settings.groq_llm_model, prompt="p")

    assert "response_format" not in completions.calls[0]


async def test_num_predict_is_a_floor_not_a_ceiling(
    settings: Settings, completions: _FakeCompletions
) -> None:
    """An Ollama-tuned cap is far too tight for a reasoning model."""
    client = _build_client(settings, completions)

    await client.generate(model=settings.groq_llm_model, prompt="p", options={"num_predict": 96})

    assert completions.calls[0]["max_tokens"] == settings.groq_max_tokens


async def test_images_are_sent_as_base64_data_uris(
    settings: Settings, completions: _FakeCompletions
) -> None:
    client = _build_client(settings, completions)

    await client.generate(model=settings.groq_vision_model, prompt="p", images=[b"jpegbytes"])

    content = completions.calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "p"}
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_transient_failure_is_retried_then_succeeds(
    settings: Settings, completions: _FakeCompletions
) -> None:
    completions.errors = [APIConnectionError(request=Request("POST", "https://api.groq.com"))]
    completions.content = '{"ok": true}'
    client = _build_client(settings, completions)

    result = await client.generate(model=settings.groq_llm_model, prompt="p")

    assert result == '{"ok": true}'
    assert len(completions.calls) == 2


async def test_retries_are_exhausted_then_surface_as_provider_unavailable(
    settings: Settings, completions: _FakeCompletions
) -> None:
    completions.errors = [
        RateLimitError(
            "rate limited",
            response=Response(429, request=Request("POST", "https://api.groq.com")),
            body=None,
        )
        for _ in range(settings.groq_max_retries + 1)
    ]
    client = _build_client(settings, completions)

    with pytest.raises(AIProviderUnavailable):
        await client.generate(model=settings.groq_llm_model, prompt="p")

    assert len(completions.calls) == settings.groq_max_retries + 1


async def test_bad_request_is_not_retried(
    settings: Settings, completions: _FakeCompletions
) -> None:
    """A 400 is a request-shaping bug — the exact failure this module's
    `reasoning_effort` handling exists to prevent. Retrying just repeats it."""
    from groq import BadRequestError

    completions.errors = [
        BadRequestError(
            "`reasoning_effort` must be one of `low`, `medium`, or `high`",
            response=Response(400, request=Request("POST", "https://api.groq.com")),
            body=None,
        )
    ]
    client = _build_client(settings, completions)

    with pytest.raises(AIProviderUnavailable):
        await client.generate(model=settings.groq_llm_model, prompt="p")

    assert len(completions.calls) == 1


async def test_missing_api_key_is_a_domain_error_not_a_raw_groq_error() -> None:
    """Surfaces as a 503 through the normal handler, at construction time."""
    with pytest.raises(AIProviderUnavailable):
        AsyncGroqClient(Settings(groq_api_key=None))

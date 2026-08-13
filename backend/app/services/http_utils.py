"""Shared HTTP helpers for the external catalogs and content sources.

Extracted from `title_lookup.py` so the Module 4 sources retry on exactly
the same terms the Module 3 lookups already do, rather than growing a
second, subtly different backoff policy.

The policy, unchanged: retry what an immediate second attempt can fix
(5xx, timeouts, connection resets) and never retry what it cannot (a 429
quota refusal or a rejected key answers the same way however many times
it is asked, so retrying only makes every scan slower).

Unlike the Module 3 version, the result distinguishes **"reached it, no
such page"** (a 404 — a real answer, safe to cache) from **"could not
reach it"** (timeout, 5xx, quota — must not be cached, or one transient
outage would pin an empty entry in SQLite for the whole TTL).
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

RETRY_BACKOFF_SECONDS = 0.4


@dataclass(frozen=True)
class JsonFetch:
    """The outcome of a JSON GET.

    Attributes:
        payload: The decoded body, or `None` when there was nothing to
            decode (404, refusal, or an unreachable host).
        available: `False` when the source could not be reached or refused
            to answer. `available=True` with `payload=None` is the
            meaningful case: the source answered, and has no such page.
    """

    payload: Any | None = None
    available: bool = True

    @classmethod
    def unavailable(cls) -> "JsonFetch":
        """Builds the outcome representing an unreachable or refusing source."""
        return cls(payload=None, available=False)

    @classmethod
    def not_found(cls) -> "JsonFetch":
        """Builds the outcome representing a reachable source with no such page."""
        return cls(payload=None, available=True)


async def head_exists(
    url: str,
    timeout: float,
    max_retries: int,
    source: str,
    headers: dict[str, str] | None = None,
) -> bool:
    """Checks whether a URL actually serves something, without downloading it.

    Used by the cover-image fallback, which builds candidate URLs from an
    identifier rather than being handed them by a search result: Open
    Library's Covers API takes any ISBN and answers 404 for the ones it has
    no image for. Storing an unverified URL would put a broken image in the
    cache for the whole TTL, and — because a `cover_url` counts as content
    in `_has_content` — would flag an otherwise empty book as found.

    Redirects are followed: the Covers API answers 302 to the CDN.

    Unlike `get_json_with_retry`, this collapses "no such image" and "could
    not reach the host" into a single `False`. The distinction would be
    worth keeping if the answer were cached on its own, but it isn't: a
    cover is cosmetic, and a book that has other metadata keeps the long
    TTL regardless of whether its cover lookup happened to fail. Missing a
    cover for one TTL is a smaller cost than the tri-state plumbing.

    Args:
        url: The URL to probe.
        timeout: Per-request timeout, in seconds.
        max_retries: Extra attempts after the first.
        source: Short label used in log events.
        headers: Extra request headers.

    Returns:
        `True` if the URL answered successfully. Never raises.
    """
    attempts = max_retries + 1

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.head(url)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning(
                    "head_request_failed", source=source, attempt=attempt, error=str(exc)
                )
            else:
                if response.is_success:
                    return True
                if response.status_code < 500:
                    logger.info("head_not_found", source=source, status=response.status_code)
                    return False
                logger.warning(
                    "head_request_transient_error",
                    source=source,
                    attempt=attempt,
                    status=response.status_code,
                )

            if attempt < attempts:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return False


async def get_json_with_retry(
    url: str,
    params: dict[str, Any] | None,
    timeout: float,
    max_retries: int,
    source: str,
    headers: dict[str, str] | None = None,
) -> JsonFetch:
    """GETs a JSON document, retrying transient failures.

    Args:
        url: The endpoint to query.
        params: Query-string parameters, if any.
        timeout: Per-request timeout, in seconds. Bounds how long a slow
            external site can hold up the caller.
        max_retries: Extra attempts after the first.
        source: Short label used in log events.
        headers: Extra request headers. Wikipedia in particular requires a
            descriptive `User-Agent` and throttles or refuses without one.

    Returns:
        The decoded body plus whether the source was reachable at all.
        Never raises for a remote failure — callers decide what an absent
        source means for them.
    """
    attempts = max_retries + 1

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning(
                    "source_request_failed", source=source, attempt=attempt, error=str(exc)
                )
            else:
                if response.is_success:
                    try:
                        return JsonFetch(payload=response.json())
                    except ValueError as exc:
                        # A body we cannot parse is not evidence the book is
                        # absent, so it must not be cached as an empty entry.
                        logger.warning("source_bad_json", source=source, error=str(exc))
                        return JsonFetch.unavailable()

                if response.status_code == httpx.codes.NOT_FOUND:
                    logger.info("source_not_found", source=source, url=url)
                    return JsonFetch.not_found()

                if response.status_code < 500:
                    logger.warning(
                        "source_request_refused", source=source, status=response.status_code
                    )
                    return JsonFetch.unavailable()

                logger.warning(
                    "source_request_transient_error",
                    source=source,
                    attempt=attempt,
                    status=response.status_code,
                )

            if attempt < attempts:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return JsonFetch.unavailable()

"""Glance's own metrics: what the app does, as numbers an alert can watch.

Auto-instrumentation (`app.core.telemetry`) already measures the plumbing --
HTTP requests, SQL, calls out to Groq and the catalogs. This module is the
other half: the *business* events only the code knows about (how a scan ended,
which path recognised the cover, whether Groq is rate-limiting us, whether the
`book_id` invariant held). Names live under `glance.`.

| metric                           | type              | answers                              |
|----------------------------------|-------------------|--------------------------------------|
| `glance.scans`                   | counter           | how many scans, how they end         |
| `glance.scan.duration`           | histogram (s)     | how long a scan takes                |
| `glance.scan.attempt_timeouts`   | counter           | attempts the runtime limit cancelled |
| `glance.queue.wait`              | histogram (s)     | upload -> a queue worker picks it up |
| `glance.ai.errors`               | counter           | Groq 429s and outages                |
| `glance.vision.ocr_fallback`     | counter           | how often OCR saves a scan           |
| `glance.summaries`               | counter           | summary success rate                 |
| `glance.corpus_leaks`            | counter           | **must stay 0** (the book_id guard)  |
| `glance.jobs.oldest_pending_age` | observable gauge  | scans stuck in the queue (api only)  |

**Safe when telemetry is off.** Until `bind` hands over a real meter provider
the instruments come from OpenTelemetry's no-op provider, so every function
here is a cheap do-nothing in tests and plain development. Call sites never
check whether telemetry is on.

**Why instruments are rebuilt by `bind` instead of created at import.** The
OpenTelemetry global meter provider can be set once per process, which a test
suite that wants a fresh in-memory reader per test cannot live with. Here the
provider is injected: `bind(provider)` rebuilds the instruments against it, and
`bind(None)` returns to no-op. Production binds once at startup.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from opentelemetry.metrics import (
    CallbackOptions,
    Counter,
    Histogram,
    MeterProvider,
    NoOpMeterProvider,
    Observation,
)

#: Bucket edges (seconds) for the two duration histograms. The OpenTelemetry
#: default edges top out at 10 000 and are meant for milliseconds; a scan takes
#: 30-120 s, so with them everything would land in two buckets.
_SECONDS_BUCKETS = [1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 300.0, 600.0]


@dataclass(frozen=True)
class _Instruments:
    """The instruments of one meter provider."""

    scans: Counter
    scan_duration: Histogram
    attempt_timeouts: Counter
    queue_wait: Histogram
    ai_errors: Counter
    ocr_fallback: Counter
    summaries: Counter
    corpus_leaks: Counter


def _build(provider: MeterProvider) -> _Instruments:
    meter = provider.get_meter("glance")
    return _Instruments(
        scans=meter.create_counter(
            "glance.scans",
            unit="{scan}",
            description="Scan jobs by final outcome (done/failed/poisoned/unrecognized).",
        ),
        scan_duration=meter.create_histogram(
            "glance.scan.duration",
            unit="s",
            description="Time a queue worker spent on one scan or correction.",
            explicit_bucket_boundaries_advisory=_SECONDS_BUCKETS,
        ),
        attempt_timeouts=meter.create_counter(
            "glance.scan.attempt_timeouts",
            unit="{attempt}",
            description="Delivery attempts cancelled by the runtime limit (not final outcomes).",
        ),
        queue_wait=meter.create_histogram(
            "glance.queue.wait",
            unit="s",
            description="Time from the upload to a queue worker picking the scan up.",
            explicit_bucket_boundaries_advisory=_SECONDS_BUCKETS,
        ),
        ai_errors=meter.create_counter(
            "glance.ai.errors",
            unit="{error}",
            description="Failed AI provider calls (rate_limited/unavailable/bad_request).",
        ),
        ocr_fallback=meter.create_counter(
            "glance.vision.ocr_fallback",
            unit="{scan}",
            description="Scans for which OCR had to run after the vision model.",
        ),
        summaries=meter.create_counter(
            "glance.summaries",
            unit="{summary}",
            description="Summary requests by availability and cache hit.",
        ),
        corpus_leaks=meter.create_counter(
            "glance.corpus_leaks",
            unit="{leak}",
            description="Retrievals that returned another book's chunk. Must stay 0.",
        ),
    )


_provider: MeterProvider = NoOpMeterProvider()
_instruments = _build(_provider)
#: Latest age of the oldest pending scan, refreshed by a background task in
#: the api (see `app.services.job_metrics`) and read by the gauge callback.
_pending_age_seconds: float | None = None
_gauge_registered = False


def bind(provider: MeterProvider | None) -> None:
    """Points every instrument at `provider` (`None` returns to no-op).

    Args:
        provider: The meter provider to record into, or `None` for the no-op one.
    """
    global _provider, _instruments, _gauge_registered
    _gauge_registered = False
    _provider = provider if provider is not None else NoOpMeterProvider()
    _instruments = _build(_provider)


def record_scan(
    outcome: str, method: str, kind: str, duration_seconds: float | None = None
) -> None:
    """Counts one finished scan (or correction) and, when known, how long it took.

    Args:
        outcome: A *final* outcome: `done`, `failed`, `poisoned` or
            `unrecognized` (the photo was not a readable book cover -- a normal
            outcome, not a fault). A timed-out attempt is not final (the message
            is redelivered, and a job that always hangs ends `poisoned`), so it
            goes to `record_attempt_timeout` instead.
        method: How the cover was recognised: `vision`, `ocr`, `manual` (a
            correction) or `none` (it was never recognised).
        kind: The job kind: `scan` or `refresh`.
        duration_seconds: Time spent on it, when measured.
    """
    _instruments.scans.add(1, {"outcome": outcome, "method": method, "kind": kind})
    if duration_seconds is not None:
        _instruments.scan_duration.record(duration_seconds, {"outcome": outcome})


def record_attempt_timeout(kind: str) -> None:
    """Counts one delivery attempt cancelled by `job_max_runtime_seconds`.

    Kept out of `glance.scans` so one hung job is one failure in the failure
    ratio (its final `poisoned`), not one per attempt.

    Args:
        kind: The job kind: `scan`, `refresh` or `unknown`.
    """
    _instruments.attempt_timeouts.add(1, {"kind": kind})


def record_queue_wait(seconds: float) -> None:
    """Records how long a scan waited between upload and pick-up."""
    _instruments.queue_wait.record(max(seconds, 0.0))


def record_ai_error(provider: str, model: str, reason: str) -> None:
    """Counts one failed AI provider call.

    Args:
        provider: `groq` or `ollama`.
        model: The model that was called.
        reason: `rate_limited`, `unavailable` or `bad_request`.
    """
    _instruments.ai_errors.add(1, {"provider": provider, "model": model, "reason": reason})


def record_ocr_fallback(reason: str) -> None:
    """Counts a scan that needed OCR after the vision model.

    Args:
        reason: `unconfirmed` (the model's guess was not confirmed by the
            catalogs) or `provider_unavailable` (the model could not be reached).
    """
    _instruments.ocr_fallback.add(1, {"reason": reason})


def record_summary(available: bool, cached: bool) -> None:
    """Counts one summary request.

    Args:
        available: Whether the reader got a summary (`False` = fall back to the blurb).
        cached: Whether it was served from the stored one.
    """
    _instruments.summaries.add(1, {"available": available, "cached": cached})


def record_corpus_leak() -> None:
    """Counts a retrieval that returned another book's chunk. Alerting on any."""
    _instruments.corpus_leaks.add(1)


def set_oldest_pending_age(seconds: float | None) -> None:
    """Stores the value the `glance.jobs.oldest_pending_age` gauge reports.

    Args:
        seconds: Age of the oldest `pending` job (0 when none), or `None` if unknown.
    """
    global _pending_age_seconds
    _pending_age_seconds = seconds


def _observe_pending_age(_options: CallbackOptions) -> Iterable[Observation]:
    """Gauge callback: reads the cached value, never touches the database.

    The SDK calls this from the exporter's thread, synchronously, so doing a
    query here would mean running async database code from the wrong thread.
    """
    if _pending_age_seconds is not None:
        yield Observation(_pending_age_seconds)


def register_pending_age_gauge() -> None:
    """Creates the observable gauge on the current meter provider (api only).

    Once per `bind`: the apps a test run builds would otherwise each register
    a duplicate.
    """
    global _gauge_registered
    if _gauge_registered:
        return
    _gauge_registered = True
    _provider.get_meter("glance").create_observable_gauge(
        "glance.jobs.oldest_pending_age",
        callbacks=[_observe_pending_age],
        unit="s",
        description="Age of the oldest scan still waiting for a queue worker (0 when none).",
    )


__all__ = [
    "bind",
    "record_ai_error",
    "record_attempt_timeout",
    "record_corpus_leak",
    "record_ocr_fallback",
    "record_queue_wait",
    "record_scan",
    "record_summary",
    "register_pending_age_gauge",
    "set_oldest_pending_age",
]

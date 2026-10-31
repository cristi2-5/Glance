"""OpenTelemetry setup: traces and metrics for the api and the queue worker.

Until cloud step 6 the only signal was structlog JSON on stdout: no timings, no
counts, and no way to follow one scan from the phone's upload through the queue
to Groq and Postgres. This module wires **OpenTelemetry** (vendor-neutral) in
once per process; where the data goes is configuration, not code:

| configured                                  | destination                       |
|---------------------------------------------|-----------------------------------|
| `APPLICATIONINSIGHTS_CONNECTION_STRING`     | Azure Monitor (Application Insights) |
| else `OTEL_EXPORTER_OTLP_ENDPOINT`          | an OTLP/HTTP receiver (Aspire locally) |
| neither                                     | **nothing**: telemetry is a no-op |

**No-op unless configured.** Tests and plain `uvicorn` runs set neither, so no
provider is built, nothing is patched and no byte leaves the process. Every
helper here (`span`, `annotate`, `inject_traceparent`, ...) is safe to call in
that state; call sites never ask whether telemetry is on.

**Providers are held here, not installed globally -- except in production.**
OpenTelemetry's global tracer/meter providers can be set once per process, which
a test suite that wants a fresh in-memory exporter per test cannot work with.
So `setup_telemetry` keeps its providers in module state, `get_tracer` reads
them from there, and the auto-instrumentors are handed them explicitly. Only the
real (exporter-configured) path also publishes them globally, because one
consumer, the Azure SDK's tracing plugin (Blob/Queue spans), reads the global
provider and has no parameter for it. Tests inject their own providers
(`setup_telemetry(..., tracer_provider=..., meter_provider=...)`) and call
`shutdown_telemetry()` afterwards, which returns the module to the off state.

**Idempotent.** A second `setup_telemetry` while one is active returns `True`
and changes nothing, so the several apps a test run builds do not stack
instrumentation.

What is instrumented automatically: incoming requests (FastAPI), outgoing HTTP
(httpx: Groq, the catalogs, Wikipedia, Ollama), SQL (SQLAlchemy) and Blob/Queue
calls (azure-core). `/health` and `/ready` are excluded: the platform probes
them every few seconds, which would be most of the traces and most of the bill.
Orphan root spans (the queue worker's idle polling, heartbeats) are dropped by
`_Sampler` for the same reason.
"""

import contextlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, Span, SpanKind, Status, StatusCode, TraceState
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import Attributes

from app.core import metrics
from app.core.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)

#: Service names: the two boxes of Application Insights' Application Map.
API_SERVICE_NAME = "glance-api"
QUEUE_WORKER_SERVICE_NAME = "glance-queue-worker"

#: Regexes (searched in the URL) of the endpoints that produce no traces.
_EXCLUDED_URLS = r"/health$,/ready$"

#: A trace may only *start* at an incoming request or at a handled queue
#: message. Any other root span is an orphan with no request behind it: the
#: queue worker's idle polling of an empty queue (one storage call every 1-10 s,
#: forever), its visibility heartbeat, the api's pending-age query. Kept, they
#: would bury every real trace and be most of the Application Insights bill.
_TRACE_ROOT_KINDS = (SpanKind.SERVER, SpanKind.CONSUMER)


class _Sampler(Sampler):
    """Parent-based ratio sampling that also drops orphan root spans.

    `ParentBased` makes a child follow its parent's decision, so a scan is
    recorded whole or not at all; only roots roll the dice. The orphan check
    (see `_TRACE_ROOT_KINDS`) only ever fires for a *root* span: once a request
    or a message is being handled, the storage calls beneath it are children and
    are kept.
    """

    def __init__(self, ratio: float) -> None:
        self._ratio = ratio
        self._delegate = ParentBased(root=TraceIdRatioBased(ratio))

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        """See `Sampler.should_sample`."""
        parent = trace.get_current_span(parent_context).get_span_context()
        if not parent.is_valid and kind not in _TRACE_ROOT_KINDS:
            return SamplingResult(Decision.DROP)
        return self._delegate.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        """See `Sampler.get_description`."""
        return f"GlanceSampler(parent_based_ratio={self._ratio})"


@dataclass
class _State:
    """What `setup_telemetry` created, so `shutdown_telemetry` can undo it."""

    service_name: str
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    #: True when this module built the providers (and must shut them down).
    owns_providers: bool
    instrumented_apps: list["FastAPI"] = field(default_factory=list)
    httpx_instrumented: bool = False
    sqlalchemy_instrumented: bool = False
    azure_tracing: bool = False


_state: _State | None = None

#: Upper bound for each final flush at shutdown.
_FLUSH_TIMEOUT_MILLIS = 5000


def is_enabled() -> bool:
    """Whether telemetry is active in this process."""
    return _state is not None


def _service_version() -> str:
    try:
        return metadata.version("glance-backend")
    except metadata.PackageNotFoundError:
        # The image copies `app/` without pip-installing the project.
        return "unknown"


def _build_exporters(settings: Settings) -> tuple[Any, Any]:
    """Creates the span and metric exporters the settings select.

    Imported lazily: a process with telemetry off never loads either exporter
    library.

    Returns:
        `(span_exporter, metric_exporter)`.
    """
    if settings.telemetry_exporter == "azure_monitor":
        from azure.monitor.opentelemetry.exporter import (
            AzureMonitorMetricExporter,
            AzureMonitorTraceExporter,
        )

        connection = settings.applicationinsights_connection_string
        return (
            AzureMonitorTraceExporter(connection_string=connection),
            AzureMonitorMetricExporter(connection_string=connection),
        )

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    base = (settings.otel_exporter_otlp_endpoint or "").rstrip("/")
    return (
        OTLPSpanExporter(endpoint=f"{base}/v1/traces"),
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
    )


def setup_telemetry(
    settings: Settings,
    service_name: str,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> bool:
    """Turns telemetry on for this process, if it is configured. Safe to call twice.

    Args:
        settings: Selects the destination (`telemetry_exporter`), the sampling
            ratio and the metric export interval.
        service_name: `glance-api` or `glance-queue-worker`.
        tracer_provider: Test hook -- use this provider instead of building
            one from the settings (it is then not published globally and not
            shut down by `shutdown_telemetry`).
        meter_provider: Test hook, as above, for metrics.

    Returns:
        `True` when telemetry is active (now, or already), `False` when it is
        not configured and everything stays a no-op.
    """
    global _state
    if _state is not None:
        return True

    injected = tracer_provider is not None or meter_provider is not None
    if not injected and settings.telemetry_exporter is None:
        return False

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": _service_version(),
            "deployment.environment": settings.app_env,
        }
    )

    if injected:
        tracer_provider = tracer_provider or TracerProvider(resource=resource)
        meter_provider = meter_provider or MeterProvider(resource=resource)
    else:
        span_exporter, metric_exporter = _build_exporters(settings)
        tracer_provider = TracerProvider(
            resource=resource, sampler=_Sampler(settings.otel_traces_sample_ratio)
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=settings.otel_metric_export_interval_seconds * 1000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])

    state = _State(
        service_name=service_name,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        owns_providers=not injected,
    )

    if not injected:
        # The Azure SDK's tracing plugin has no provider parameter: it reads
        # the global one. Published once; OpenTelemetry ignores later attempts.
        trace.set_tracer_provider(tracer_provider)
        from opentelemetry import metrics as otel_metrics

        otel_metrics.set_meter_provider(meter_provider)
        from azure.core.settings import settings as azure_settings

        azure_settings.tracing_implementation = "opentelemetry"
        state.azure_tracing = True

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument(
        tracer_provider=tracer_provider, meter_provider=meter_provider
    )
    state.httpx_instrumented = True

    metrics.bind(meter_provider)
    _state = state
    logger.info(
        "telemetry_enabled",
        service=service_name,
        exporter="injected" if injected else settings.telemetry_exporter,
        sample_ratio=settings.otel_traces_sample_ratio,
    )
    return True


def instrument_app(app: "FastAPI") -> None:
    """Traces incoming requests of a FastAPI app (no-op when telemetry is off).

    Args:
        app: The application to instrument. `/health` and `/ready` are excluded.
    """
    if _state is None or app in _state.instrumented_apps:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_state.tracer_provider,
        meter_provider=_state.meter_provider,
        excluded_urls=_EXCLUDED_URLS,
        # The per-ASGI-message "http send"/"http receive" spans triple the span
        # count of every request and tell nothing the request span does not.
        exclude_spans=["receive", "send"],
    )
    _state.instrumented_apps.append(app)


def instrument_engine(engine: "AsyncEngine") -> None:
    """Traces the SQL an engine runs (no-op when telemetry is off).

    Args:
        engine: The async engine; its underlying sync engine is instrumented.
    """
    if _state is None or _state.sqlalchemy_instrumented:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    # `skip_dep_check`: the instrumentation declares support for SQLAlchemy
    # < 2.1 and refuses to run on the 2.1 this project is locked to, although
    # the cursor events it hooks are unchanged. `test_sql_statements_are_traced`
    # exercises it against a real query, so a release that breaks it fails there.
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine, tracer_provider=_state.tracer_provider, skip_dep_check=True
    )
    _state.sqlalchemy_instrumented = True


def shutdown_telemetry() -> None:
    """Flushes what is buffered and returns the process to the off state.

    Called at shutdown so the last scan's spans are not lost in the batch
    buffer, and by tests to restore global state. Safe when telemetry is off.
    """
    global _state
    state, _state = _state, None
    if state is None:
        return

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    for app in state.instrumented_apps:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.uninstrument_app(app)
    if state.sqlalchemy_instrumented:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().uninstrument()
    if state.httpx_instrumented:
        HTTPXClientInstrumentor().uninstrument()
    if state.azure_tracing:
        from azure.core.settings import settings as azure_settings

        azure_settings.tracing_implementation = None

    metrics.bind(None)
    metrics.set_oldest_pending_age(None)
    if state.owns_providers:
        # A flush that fails (the receiver is down) must not turn a clean
        # shutdown into an error; the spans are lost either way. Every call is
        # suppressed on its own, so a dead trace exporter does not also cost the
        # metrics their final flush (and vice versa), and each flush is bounded
        # so an unreachable receiver cannot hold the container's shutdown.
        for call in (
            lambda: state.tracer_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS),
            lambda: state.meter_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS),
            state.tracer_provider.shutdown,
            state.meter_provider.shutdown,
        ):
            with contextlib.suppress(Exception):
                call()


def get_tracer() -> trace.Tracer:
    """Returns the tracer spans are created with (a no-op tracer when telemetry is off)."""
    if _state is None:
        return trace.NoOpTracer()
    return _state.tracer_provider.get_tracer("glance")


@contextlib.contextmanager
def span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    context: Context | None = None,
) -> Iterator[Span]:
    """Runs a block inside a child span of the current one (or of `context`).

    An exception passing through is recorded on the span and marks it failed,
    then propagates unchanged.

    Args:
        name: Span name, e.g. `scan.vision`.
        attributes: Attributes to set at start.
        kind: The span kind.
        context: Parent context to use instead of the current one.

    Yields:
        The span (a non-recording one when telemetry is off).
    """
    with get_tracer().start_as_current_span(
        name,
        context=context,
        kind=kind,
        attributes=dict(attributes) if attributes else None,
    ) as current:
        yield current


def annotate(**attributes: Any) -> None:
    """Sets attributes on the current span, if there is a recording one.

    Used to stamp the request span of `POST /books/analyze-cover` with the job
    id, so a trace can be found by job id (`glance.job_id`).
    """
    current = trace.get_current_span()
    if current.is_recording():
        current.set_attributes({key: value for key, value in attributes.items()})


def inject_traceparent() -> str | None:
    """The W3C `traceparent` of the current span, or `None` when there is none.

    The api puts this in the queue message so the queue worker's job span
    becomes a child of the request that created the job. It is an opaque id
    (trace id, span id, sampled flag), not user data.
    """
    if _state is None:
        return None
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


@contextlib.contextmanager
def continue_trace(
    traceparent: str | None,
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Span]:
    """Runs a block as a CONSUMER span parented on a message's `traceparent`.

    With no (or an unparsable) `traceparent` the span simply starts a new trace,
    so a message from an older api, or one sent with telemetry off, still works.

    Args:
        traceparent: The W3C header value carried by the queue message.
        name: Span name, e.g. `queue.process cover-jobs`.
        attributes: Attributes to set at start.

    Yields:
        The span.
    """
    parent = (
        TraceContextTextMapPropagator().extract({"traceparent": traceparent})
        if traceparent
        else None
    )
    with span(name, attributes, kind=SpanKind.CONSUMER, context=parent) as current:
        try:
            yield current
        except BaseException as exc:
            # A job that raised -- or was cancelled by the runtime limit, which
            # is a `CancelledError` and so not an `Exception` -- must not look
            # like a successful request in Application Insights.
            if isinstance(exc, Exception):
                current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def mark_error(description: str) -> None:
    """Marks the current span failed, for an outcome that raises nothing.

    A scan that ends `failed` (unreadable cover, poison message) returns
    normally, so the span would otherwise read as a success.

    Args:
        description: A short reason, e.g. `scan failed`.
    """
    current = trace.get_current_span()
    if current.is_recording():
        current.set_status(Status(StatusCode.ERROR, description))


def current_trace_id() -> str | None:
    """The current span's trace id (lowercase hex), or `None` outside a span."""
    context = trace.get_current_span().get_span_context()
    return trace.format_trace_id(context.trace_id) if context.is_valid else None

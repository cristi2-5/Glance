"""Feeds the `glance.jobs.oldest_pending_age` gauge: how long the oldest scan has waited.

The gauge answers "is a scan stuck in the queue?" -- the failure no request ever
reports, because the client is simply still polling. A `pending` job is one the
api committed and enqueued but no queue worker has started.

**Why a background task and a cached value, not a query in the gauge callback.**
OpenTelemetry calls an observable gauge's callback from the metric reader's own
thread, synchronously. The database layer here is async: querying from that
callback would mean driving an `AsyncSession` from a foreign thread (or blocking
the thread on a new event loop), both of which break or deadlock in ways that
only show up in production. So an ordinary asyncio task in the api's own event
loop runs the one cheap `SELECT min(created_at)` (over a recent window) every `interval` seconds and
stores the result with `metrics.set_oldest_pending_age`; the callback only
reads that number. The cost is a value up to one interval old, which is
irrelevant for an alert measured in minutes.
"""

import asyncio
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics
from app.core.config import get_settings
from app.models.job import Job, JobStatus

logger = structlog.get_logger(__name__)


async def oldest_pending_age_seconds(
    db: AsyncSession, now: datetime | None = None, window_seconds: float | None = None
) -> float:
    """Returns how long the oldest *recent* `pending` job has been waiting.

    Only jobs created within `window_seconds` count. A `pending` job older than
    that is not a slow scan but an orphan (the api died between committing it and
    enqueueing it, or its message was dropped as malformed): nothing will ever
    pick it up, and left in the query it would hold the gauge above the alert
    threshold forever. The cost: a scan genuinely stuck for longer than the
    window stops alerting after an hour -- by then the alert has long fired.

    Args:
        db: A database session.
        now: The current UTC time (injectable for tests).
        window_seconds: How far back to look; defaults to
            `Settings.pending_age_window_seconds`.

    Returns:
        Age in seconds, or `0.0` when no recent job is pending.
    """
    now = now or datetime.utcnow()
    if window_seconds is None:
        window_seconds = get_settings().pending_age_window_seconds
    oldest = (
        await db.execute(
            select(func.min(Job.created_at)).where(
                Job.status == JobStatus.PENDING,
                Job.created_at >= now - timedelta(seconds=window_seconds),
            )
        )
    ).scalar_one()
    if oldest is None:
        return 0.0
    return max((now - oldest).total_seconds(), 0.0)


async def run_pending_age_monitor(
    session_factory: async_sessionmaker[AsyncSession], interval_seconds: float = 60.0
) -> None:
    """Refreshes the cached gauge value forever (until cancelled).

    A failed refresh is logged and skipped, never fatal: a monitoring query
    must not take the api down, and the gauge simply keeps its last value.

    Args:
        session_factory: Opens the sessions the query runs in.
        interval_seconds: Pause between refreshes.
    """
    while True:
        try:
            async with session_factory() as db:
                metrics.set_oldest_pending_age(await oldest_pending_age_seconds(db))
        except Exception:
            logger.exception("pending_age_refresh_failed")
        await asyncio.sleep(interval_seconds)

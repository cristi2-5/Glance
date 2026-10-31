"""Entry point of the Glance FastAPI application."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import auth, books, jobs, users
from app.core import metrics, telemetry
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.init_db import init_db
from app.db.session import AsyncSessionLocal, dispose_engine, engine, get_db
from app.services.job_metrics import run_pending_age_monitor
from app.services.storage import close_storage

logger = structlog.get_logger(__name__)

#: How long `/ready` waits for the database before answering 503. Short on
#: purpose: a readiness probe that hangs is indistinguishable from a dead api.
READINESS_TIMEOUT_SECONDS = 2.0


def create_app(app_settings: Settings) -> FastAPI:
    """Builds the FastAPI application for the given settings.

    A factory (rather than a bare module-level object) so tests can build the
    production-mode app -- docs off -- next to the development one.

    Args:
        app_settings: The settings to build from.

    Returns:
        The configured application. In production, startup (the lifespan)
        refuses to proceed on development settings.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Refuses insecure production settings and a database behind the migrations."""
        # First, before any connection: a misconfigured production container
        # must stop here, listing every problem, not fail later in some
        # narrower way.
        app_settings.validate_for_production()
        await init_db()
        # The oldest-pending-scan gauge is fed by a task of this event loop
        # (see `job_metrics` for why not from the gauge callback); there is
        # nothing to feed when telemetry is off.
        monitor = (
            asyncio.create_task(run_pending_age_monitor(AsyncSessionLocal))
            if telemetry.is_enabled()
            else None
        )
        try:
            yield
        finally:
            if monitor is not None:
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor
            try:
                # Close the Blob/Queue HTTP sessions (created lazily on first
                # use) and the database pool (plus its managed-identity
                # credential, if any).
                await close_storage()
                await dispose_engine(engine)
            finally:
                # Last, so the spans of the shutdown itself are flushed too --
                # and always, even when closing the above raised.
                telemetry.shutdown_telemetry()

    # In production the api publishes no map of itself: /docs, /redoc and
    # /openapi.json are removed.
    docs_enabled = app_settings.app_env != "production"
    app = FastAPI(
        title=app_settings.app_name,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Only needed for clients running in a browser (Expo Web). The native app
    # does not go through CORS, so the default list is empty.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Cloud step 6: with an exporter configured (or providers injected by a
    # test) this traces requests, SQL and outgoing HTTP; otherwise it is a no-op.
    if telemetry.setup_telemetry(app_settings, telemetry.API_SERVICE_NAME):
        telemetry.instrument_engine(engine)
        telemetry.instrument_app(app)
        metrics.register_pending_age_gauge()

    register_exception_handlers(app)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(books.router)
    app.include_router(jobs.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness: the process is up and answering.

        Deliberately depends on nothing else. If this checked the database, a
        database outage would make the platform kill and restart every healthy
        api container in a loop, which fixes nothing and adds downtime.

        Returns:
            A dictionary with the application's status and its name.
        """
        return {"status": "ok", "app": app_settings.app_name}

    @app.get("/ready", response_model=None)
    async def ready(db: Annotated[AsyncSession, Depends(get_db)]) -> JSONResponse | dict[str, str]:
        """Readiness: the api can actually serve requests, i.e. Postgres answers.

        Unlike `/health` this does depend on the database, because its only
        consequence is that the platform stops routing traffic here while the
        database is unreachable -- nothing is restarted.

        Args:
            db: A database session.

        Returns:
            `{"status": "ready"}`, or a 503 JSON body when the database does
            not answer `SELECT 1` within `READINESS_TIMEOUT_SECONDS`.
        """
        try:
            await asyncio.wait_for(db.execute(text("SELECT 1")), READINESS_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready"; logged below
            # A warning without a traceback: the platform probes every few
            # seconds, and a stack trace per probe would drown the log.
            logger.warning("readiness_check_failed", error_type=type(exc).__name__)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "detail": "The database is not reachable."},
            )
        return {"status": "ready"}

    return app


settings = get_settings()
configure_logging(debug=settings.debug)
app = create_app(settings)

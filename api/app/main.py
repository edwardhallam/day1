"""Application factory and lifespan — the outermost layer.

``create_app()`` assembles all layers into a running FastAPI application:
  1. Builds the FastAPI instance with lifespan context manager
  2. Registers middleware (SecurityHeaders, optional CORS)
  3. Wires exception handlers (validation errors, generic catch-all)
  4. Mounts routers at their canonical prefixes

``lifespan`` manages the full startup/shutdown sequence:
  Startup:
    1. Open shared ``httpx.AsyncClient`` (connection reuse — POLL-REQ-012)
    2. Create ``ParcelAPIClient`` with the shared HTTP client
    3. Create ``CarrierCache``; trigger initial async refresh
    4. Create ``PollingScheduler`` and register carrier-refresh job
    5. Start the scheduler (which also fires the cold-start poll — POLL-REQ-003)
    6. Store all shared objects on ``app.state`` for DI access

  Shutdown:
    1. Stop the scheduler (``wait=True`` — allow in-progress poll — POLL-REQ-002)
    2. Close the shared HTTP client
    3. Dispose the database engine connection pool

Module-level ``app = create_app()`` is the entry point Uvicorn loads.

Architecture: ARCH-PRESENTATION §3, §8; ARCH-OVERVIEW §6
Requirements: API-REQ-021–025, SEC-REQ-029–034, DEPLOY-REQ-021–023, POLL-REQ-001–004
"""
from __future__ import annotations

import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

import httpx
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.application.dtos.system_dtos import CarrierListDTO
from app.application.services.interfaces import (
    AbstractCarrierCache,
    AbstractSchedulerState,
)
from app.config import settings
from app.infrastructure.database.engine import engine
from app.infrastructure.parcel_api.carrier_cache import CarrierCache
from app.infrastructure.parcel_api.client import ParcelAPIClient
from app.infrastructure.scheduler.polling_scheduler import PollingScheduler
from app.presentation.middleware.security_headers import SecurityHeadersMiddleware
from app.presentation.routers.auth_router import router as auth_router
from app.presentation.routers.deliveries_router import router as deliveries_router
from app.presentation.routers.system_router import router as system_router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Configure structured logging from the ``LOG_LEVEL`` setting."""
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


# ---------------------------------------------------------------------------
# Demo mode stubs
# ---------------------------------------------------------------------------


class _DemoSchedulerStub(AbstractSchedulerState):
    """No-op scheduler stub for demo mode — health endpoint reports not running."""

    def is_running(self) -> bool:
        return False

    def get_next_poll_at(self) -> Optional[datetime]:
        return None


class _DemoCarrierCacheStub(AbstractCarrierCache):
    """No-op carrier cache stub for demo mode."""

    def get_carriers(self) -> CarrierListDTO:
        return CarrierListDTO(carriers=[], cached_at=None, cache_status="unavailable")

    async def refresh(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage shared resources across the full application lifecycle.

    Startup order ensures each component has its dependencies ready before
    the next is initialised.  Shutdown runs in reverse order so the scheduler
    can finish any in-progress poll before the HTTP client is closed.
    """
    _configure_logging()
    logger.info(
        "Starting Delivery Tracking Service v%s (env=%s, demo=%s)",
        settings.VERSION,
        settings.ENVIRONMENT,
        settings.DEMO_MODE,
    )

    if settings.DEMO_MODE:
        # ── Demo mode: no Parcel API, no scheduler, no carrier cache ─────
        app.state.polling_scheduler = _DemoSchedulerStub()
        app.state.carrier_cache = _DemoCarrierCacheStub()
        logger.info("Demo mode: polling and carrier cache disabled")
        logger.info("Application startup complete — accepting requests")

        yield

        logger.info("Application shutdown complete")
    else:
        # ── Normal mode: full startup sequence ───────────────────────────

        # Step 1: Shared HTTP client — connection reuse (POLL-REQ-012).
        # TLS verification always enabled (SEC-REQ-055).
        http_client = httpx.AsyncClient(verify=True)

        # Step 2: Parcel API client
        parcel_client = ParcelAPIClient(
            client=http_client,
            api_key=settings.PARCEL_API_KEY.get_secret_value(),
            timeout=float(settings.POLL_HTTP_TIMEOUT_SECONDS),
        )

        # Step 3: Carrier cache — initial refresh fires as background task
        carrier_cache = CarrierCache(parcel_client=parcel_client)
        import asyncio  # noqa: PLC0415

        asyncio.create_task(
            carrier_cache.refresh(),
            name="carrier_cache_initial_refresh",
        )

        # Step 4: Polling scheduler
        polling_scheduler = PollingScheduler(
            parcel_client=parcel_client,
            interval_minutes=settings.POLL_INTERVAL_MINUTES,
            jitter_seconds=settings.POLL_JITTER_SECONDS,
        )

        # Register carrier-refresh job on the internal APScheduler instance
        # BEFORE calling start() so it is activated alongside the poll job.
        polling_scheduler._scheduler.add_job(  # noqa: SLF001
            func=carrier_cache.refresh,
            trigger=IntervalTrigger(hours=24),
            id="carrier_refresh",
            name="Carrier Cache Refresh",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            replace_existing=True,
        )

        # Step 5: Start scheduler (also fires cold-start poll — POLL-REQ-003)
        polling_scheduler.start()

        # Step 6: Store on app.state for DI access
        app.state.http_client = http_client
        app.state.parcel_client = parcel_client
        app.state.carrier_cache = carrier_cache
        app.state.polling_scheduler = polling_scheduler

        logger.info("Application startup complete — accepting requests")

        yield

        # ── Shutdown ─────────────────────────────────────────────────────
        logger.info("Application shutdown initiated")

        # Stop scheduler — wait=True lets an in-progress poll complete
        # (POLL-REQ-002; APScheduler default grace period applies)
        polling_scheduler.shutdown()

        # Close shared HTTP client (drains keep-alive connections)
        await http_client.aclose()

        # Dispose SQLAlchemy engine connection pool
        await engine.dispose()

        logger.info("Application shutdown complete")


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return 422 with a standard error envelope for Pydantic validation failures.

    Transforms FastAPI's default validation error format into the
    ``ErrorResponse`` envelope used across all endpoints (API-REQ-005).
    """
    details = [
        {"field": ".".join(str(l) for l in err.get("loc", [])), "message": err["msg"]}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": details,
            }
        },
    )


async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return 500 with a generic error envelope for any unhandled exception.

    The full traceback is logged server-side; only a generic message is
    returned to the client — no implementation details are leaked
    (API-REQ-025, SEC-REQ-016).
    """
    logger.exception(
        "Unhandled exception for %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred. Please try again later.",
            }
        },
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Assemble and return the FastAPI application.

    Called once at module load time — the result is imported by Uvicorn via
    ``app = create_app()`` at the bottom of this file.
    """
    # OpenAPI docs only available in development (API-REQ-023)
    docs_url = "/api/docs" if settings.ENVIRONMENT == "development" else None
    redoc_url = "/api/redoc" if settings.ENVIRONMENT == "development" else None
    openapi_url = "/api/openapi.json" if settings.ENVIRONMENT == "development" else None

    application = FastAPI(
        title="Delivery Tracking API",
        version=settings.VERSION,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    # ── Middleware (order matters: first registered = outermost wrapper) ──

    # Security headers — always applied (API-REQ-021, SEC-REQ-031)
    application.add_middleware(SecurityHeadersMiddleware)

    # CORS — development only (SEC-REQ-029–030)
    if settings.ENVIRONMENT == "development":
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3000", "http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # ── Exception handlers ────────────────────────────────────────────────
    application.add_exception_handler(
        RequestValidationError,
        _validation_exception_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        Exception,
        _generic_exception_handler,  # type: ignore[arg-type]
    )

    # ── Routers ───────────────────────────────────────────────────────────
    application.include_router(auth_router, prefix="/api/auth")
    application.include_router(deliveries_router, prefix="/api/deliveries")
    application.include_router(system_router, prefix="/api")

    return application


# ---------------------------------------------------------------------------
# Module-level application instance
# ---------------------------------------------------------------------------

# Uvicorn imports this object:  uvicorn app.main:app --factory OR directly.
# ``create_app()`` is called once at import time; config validation runs via
# ``settings = Settings()`` at the top of config.py before this point.
app = create_app()

"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import verify_api_key
from app.cleanup import cleanup_loop
from app.config import get_settings
from app.downloader import get_download_manager
from app.models import (
    ErrorResponse,
    HealthResponse,
    ServiceInfoResponse,
    StatsResponse,
)
from app.routes import download, files, jobs
from app.storage.job_store import get_job_store
from app.utils import get_directory_size

logger = logging.getLogger(__name__)

_start_time: float = 0.0
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)


def _configure_logging() -> None:
    """Configure root logging format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    global _start_time
    _configure_logging()
    settings = get_settings()

    settings.download_path.mkdir(parents=True, exist_ok=True)
    logger.info("Download directory: %s", settings.download_path)

    cleanup_task = asyncio.create_task(cleanup_loop())

    _start_time = time.monotonic()
    logger.info(
        "Gallery API ready on %s:%s",
        settings.host,
        settings.port,
    )

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="gallery-api",
        description="REST API wrapper for gallery-dl downloads",
        version="1.0.0",
        lifespan=lifespan,
    )

    _register_exception_handlers(app)
    _register_middleware(app)
    _register_routes(app)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers for consistent JSON errors."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        if isinstance(exc.detail, dict) and "success" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": str(exc.detail)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        message = errors[0].get("msg", "Invalid request") if errors else "Invalid request"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"success": False, "error": message},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error(
            "Unhandled exception on %s %s: %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "error": "Internal server error"},
        )


def _register_middleware(app: FastAPI) -> None:
    """Register request logging and rate limiting middleware."""

    @app.middleware("http")
    async def logging_and_rate_limit_middleware(
        request: Request, call_next: Callable,
    ):
        client = request.client.host if request.client else "unknown"
        logger.info("Request received: %s %s from %s", request.method, request.url.path, client)

        if request.url.path != "/health":
            api_key = request.headers.get("X-API-Key", "")
            if api_key and not _check_rate_limit(api_key):
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"success": False, "error": "Rate limit exceeded"},
                )

        response = await call_next(request)
        return response


def _check_rate_limit(api_key: str) -> bool:
    """Sliding-window rate limit per API key. Returns False if limited."""
    settings = get_settings()
    now = time.monotonic()
    window = 60.0
    limit = settings.rate_limit_per_minute

    bucket = _rate_limit_buckets[api_key]
    _rate_limit_buckets[api_key] = [t for t in bucket if now - t < window]

    if len(_rate_limit_buckets[api_key]) >= limit:
        return False

    _rate_limit_buckets[api_key].append(now)
    return True


def _register_routes(app: FastAPI) -> None:
    """Register all API routes."""

    @app.get("/", response_model=ServiceInfoResponse)
    async def root(_api_key: str = Depends(verify_api_key)) -> ServiceInfoResponse:
        """Return service identity."""
        return ServiceInfoResponse()

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Return health status without authentication."""
        settings = get_settings()
        store = get_job_store()

        active = await store.list_jobs_by_status("downloading")
        queued = await store.list_jobs_by_status("queued")
        completed = await store.list_jobs_by_status("completed")
        failed = await store.list_jobs_by_status("failed")

        uptime = time.monotonic() - _start_time if _start_time else 0.0

        return HealthResponse(
            status="healthy",
            uptime_seconds=round(uptime, 2),
            active_jobs=len(active),
            queued_jobs=len(queued),
            completed_jobs=len(completed),
            failed_jobs=len(failed),
            storage_bytes=get_directory_size(settings.download_path),
        )

    @app.get("/stats", response_model=StatsResponse)
    async def stats(_api_key: str = Depends(verify_api_key)) -> StatsResponse:
        """Return aggregate download statistics."""
        store = get_job_store()
        job_stats = await store.get_stats()

        avg_duration = None
        if job_stats.completed_jobs > 0:
            avg_duration = round(
                job_stats.total_duration_seconds / job_stats.completed_jobs, 2
            )

        return StatsResponse(
            total_jobs=job_stats.total_jobs,
            completed_jobs=job_stats.completed_jobs,
            failed_jobs=job_stats.failed_jobs,
            total_files=job_stats.total_files,
            total_bytes=job_stats.total_bytes,
            average_duration_seconds=avg_duration,
        )

    app.include_router(download.router)
    app.include_router(jobs.router)
    app.include_router(files.router)


app = create_app()

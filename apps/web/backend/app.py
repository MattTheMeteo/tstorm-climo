"""FastAPI application factory.

The app loads its artifacts once at startup (lifespan) and serves the API
over them. Artifacts are read lazily -- ``load_observation_artifact`` holds
a scan plan, not data -- so startup stays cheap and idle memory stays low.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from tstorm_climo.artifacts import load_observation_artifact
from tstorm_climo.gridded import load_gridded_artifact
from tstorm_climo.progress import format_count, format_duration

from .api import router
from .config import Settings
from .state import Artifacts

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app, loading artifacts from ``settings`` at startup."""
    resolved = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        started = time.monotonic()
        logger.info(
            "Starting tstorm-climo API (observation artifact: %s, gridded: %s)",
            resolved.artifact_dir or "none",
            resolved.gridded_dir or "none",
        )
        try:
            observation = (
                load_observation_artifact(resolved.artifact_dir)
                if resolved.artifact_dir is not None
                else None
            )
            gridded = (
                load_gridded_artifact(resolved.gridded_dir)
                if resolved.gridded_dir is not None
                else None
            )
        except Exception:
            logger.exception("Failed to load artifacts during startup")
            raise
        if observation is None:
            logger.warning(
                "No observation artifact configured; data endpoints will return 503"
            )
        else:
            logger.info(
                "Loaded observation artifact: %s places, %s to %s",
                format_count(len(observation.places)),
                observation.observed_start,
                observation.observed_end,
            )
        if gridded is None:
            logger.info("No gridded artifact configured; /api/wetness returns 503")
        else:
            logger.info(
                "Loaded gridded artifact: %s places, %s to %s",
                format_count(int(gridded.manifest.get("place_count") or 0)),
                gridded.observed_start,
                gridded.observed_end,
            )
        app.state.artifacts = Artifacts(observation=observation, gridded=gridded)
        logger.info("API ready in %s", format_duration(time.monotonic() - started))
        try:
            yield
        finally:
            logger.info("Shutting down tstorm-climo API")
            if gridded is not None:
                gridded.dataset.close()

    application = FastAPI(title="tstorm-climo API", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Time each request; warn when one exceeds the configured budget."""
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "%s %s failed after %s",
                request.method,
                request.url.path,
                format_duration(time.perf_counter() - started),
            )
            raise
        elapsed = time.perf_counter() - started
        message = "%s %s -> %s in %s"
        arguments = (
            request.method,
            request.url.path,
            response.status_code,
            format_duration(elapsed),
        )
        if elapsed >= resolved.slow_request_seconds:
            logger.warning("%s %s -> %s in %s (slow request)", *arguments)
        else:
            logger.debug(message, *arguments)
        return response

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(router)
    return application

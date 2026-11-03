"""FastAPI application factory.

The app loads its artifacts once at startup (lifespan) and serves the API
over them. Artifacts are read lazily -- ``load_observation_artifact`` holds
a scan plan, not data -- so startup stays cheap and idle memory stays low.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tstorm_climo.artifacts import load_observation_artifact
from tstorm_climo.gridded import load_gridded_artifact

from .api import router
from .config import Settings
from .state import Artifacts


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app, loading artifacts from ``settings`` at startup."""
    resolved = settings if settings is not None else Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
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
        app.state.artifacts = Artifacts(observation=observation, gridded=gridded)
        try:
            yield
        finally:
            if gridded is not None:
                gridded.dataset.close()

    application = FastAPI(title="tstorm-climo API", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(router)
    return application

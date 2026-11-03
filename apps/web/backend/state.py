"""Shared application state: the loaded artifacts and their accessor."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from tstorm_climo.artifacts import ObservationArtifact
from tstorm_climo.gridded import GriddedArtifact


@dataclass
class Artifacts:
    """The artifacts a request may query, loaded once at startup."""

    observation: ObservationArtifact | None
    gridded: GriddedArtifact | None


def get_artifacts(request: Request) -> Artifacts:
    """FastAPI dependency returning the loaded artifacts.

    ``request.app.state`` is Starlette's dynamic attribute bag, so the
    value is read as ``object`` and narrowed with ``isinstance`` rather
    than cast: a server that forgot to load its artifacts fails loudly
    instead of mis-serving data.
    """
    artifacts: object = getattr(request.app.state, "artifacts", None)
    if not isinstance(artifacts, Artifacts):
        raise RuntimeError("application state was not initialized")
    return artifacts

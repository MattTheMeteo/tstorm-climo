"""The API endpoints: a thin layer over the analysis package.

Every endpoint validates its input with pydantic, then delegates to
``tstorm_climo.query`` / ``tstorm_climo.gridded``. Nothing here opens the
HRRR archive or the network; it reads the already-prepared artifacts.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from tstorm_climo.query import artifact_report
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR

from .schemas import (
    CoverageResponse,
    PlaceResponse,
    ReportRequest,
    ReportResponse,
    WetnessResponse,
)
from .serialize import iso, json_safe, place_record, records, wetness_geojson
from .state import Artifacts, get_artifacts

router = APIRouter(prefix="/api")

ArtifactsDep = Annotated[Artifacts, Depends(get_artifacts)]


@router.get("/coverage")
def coverage(artifacts: ArtifactsDep) -> CoverageResponse:
    """Metadata the frontend needs to bound its controls."""
    if artifacts.observation is None:
        raise HTTPException(
            status_code=503, detail="no observation artifact configured"
        )
    manifest = artifacts.observation.manifest
    return CoverageResponse.model_validate(
        {
            "observed_start": iso(artifacts.observation.observed_start),
            "observed_end": iso(artifacts.observation.observed_end),
            "place_count": len(artifacts.observation.places),
            "kinds": sorted({place.kind for place in artifacts.observation.places}),
            "variable": manifest.get("variable", {}).get("id"),
            "units": manifest.get("variable", {}).get("units"),
            "gridded": artifacts.gridded is not None,
        }
    )


@router.get("/places")
def places(
    artifacts: ArtifactsDep,
    kind: Annotated[str | None, Query()] = None,
) -> dict[str, list[PlaceResponse]]:
    """The artifact's places, optionally filtered to one kind."""
    if artifacts.observation is None:
        raise HTTPException(
            status_code=503, detail="no observation artifact configured"
        )
    selected = [
        place
        for place in artifacts.observation.places
        if kind is None or place.kind == kind
    ]
    return {"places": [place_record(place) for place in selected]}


@router.post("/report")
def report(artifacts: ArtifactsDep, body: ReportRequest) -> ReportResponse:
    """The leaderboard (summary, monthly, hourly) for one selection."""
    if artifacts.observation is None:
        raise HTTPException(
            status_code=503, detail="no observation artifact configured"
        )
    known = {place.place_key for place in artifacts.observation.places}
    keys = list(dict.fromkeys(body.place_keys))
    if any(key not in known for key in keys):
        raise HTTPException(
            status_code=404,
            detail="one or more requested places are not in the artifact",
        )
    result = artifact_report(
        artifacts.observation,
        keys,
        start=body.start.isoformat() if body.start is not None else None,
        end=body.end.isoformat() if body.end is not None else None,
        threshold_mm_per_hour=body.threshold_mm_per_hour,
    )
    return ReportResponse.model_validate(
        {
            "summary": records(result.summary),
            "monthly": records(result.monthly),
            "hourly": records(result.hourly),
            "reliability": json_safe(result.reliability),
            "start": iso(result.start),
            "end": iso(result.end),
            "places": [place_record(place) for place in result.places],
        }
    )


@router.get("/wetness")
def wetness(
    artifacts: ArtifactsDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    threshold: Annotated[
        float, Query(ge=0, allow_inf_nan=False)
    ] = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> WetnessResponse:
    """The wet-hours-per-node map as GeoJSON, from the gridded store."""
    if artifacts.gridded is None:
        raise HTTPException(status_code=503, detail="no gridded artifact configured")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    store = artifacts.gridded
    coverage_start = store.observed_start.date() if store.observed_start else None
    coverage_end = store.observed_end.date() if store.observed_end else None

    window_start = start or coverage_start
    window_end = end or coverage_end
    if coverage_start is not None and (
        window_start is None or window_start < coverage_start
    ):
        window_start = coverage_start
    if coverage_end is not None and (window_end is None or window_end > coverage_end):
        window_end = coverage_end
    if window_start is None or window_end is None:
        raise HTTPException(
            status_code=404, detail="the gridded artifact has no observed coverage"
        )
    return wetness_geojson(
        store.dataset,
        start=str(window_start),
        end=str(window_end),
        threshold=threshold,
    )

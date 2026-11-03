"""Turn analysis outputs into JSON-serializable API responses.

The analysis layer returns polars frames and xarray arrays; the API turns
them into plain dicts and lists. NaN -- which JSON cannot represent -- is
mapped to ``null``, so the reliability figure (``nan`` when it cannot be
computed) becomes ``null``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import polars as pl
import xarray as xr

from tstorm_climo.gridded import valid_hours, wet_hours
from tstorm_climo.places import Place

from .schemas import PlaceResponse, PointGeometry


def json_safe(value: Any) -> Any:
    """Recursively make a value JSON-serializable (NaN/Inf -> None)."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def records(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """A polars frame as a list of JSON-safe row dicts."""
    return [json_safe(row) for row in frame.to_dicts()]


def iso(value: datetime | None) -> str | None:
    """An aware datetime as an ISO-8601 string, or ``None``."""
    return value.isoformat() if value is not None else None


def place_record(place: Place) -> PlaceResponse:
    """A place as the API exposes it: scalar fields plus its point geometry."""
    return PlaceResponse(
        place_key=place.place_key,
        name=place.display_name,
        kind=place.kind,
        lat=place.lat,
        lon=place.lon,
        timezone=place.timezone,
        geometry=PointGeometry(coordinates=(place.lon, place.lat)),
    )


def wetness_geojson(
    dataset: xr.Dataset, *, start: str, end: str, threshold: float
) -> dict[str, Any]:
    """The wet-hours map as a GeoJSON FeatureCollection of grid nodes.

    One Point feature per node, with ``wet_hours``, ``valid_hours`` and the
    derived ``wet_fraction``. Missing-node hours are NaN and never count as
    wet (see :func:`tstorm_climo.gridded.wet_hours`).
    """
    window = dataset.sel(time=slice(start, end))
    wet = wet_hours(window, threshold=threshold)
    valid = valid_hours(window)

    longitudes = wet["longitude"].values.ravel()
    latitudes = wet["latitude"].values.ravel()
    wet_values = wet.values.ravel()
    valid_values = valid.values.ravel()

    features: list[dict[str, Any]] = []
    for lon, lat, wet_h, valid_h in zip(
        longitudes, latitudes, wet_values, valid_values, strict=True
    ):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)],
                },
                "properties": {
                    "wet_hours": int(wet_h),
                    "valid_hours": int(valid_h),
                    "wet_fraction": (
                        float(wet_h) / float(valid_h) if valid_h else None
                    ),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}

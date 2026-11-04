"""Pydantic request and response models for the API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tstorm_climo.artifacts import MAX_SELECTED_PLACES
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR

Longitude = Annotated[float, Field(ge=-180, le=180)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Position = tuple[Longitude, Latitude]


class ReportRequest(BaseModel):
    """A bounded drill-in: one selection of places, range, and threshold."""

    model_config = ConfigDict(extra="forbid")

    place_keys: list[str] = Field(min_length=1, max_length=MAX_SELECTED_PLACES)
    start: date | None = None
    end: date | None = None
    threshold_mm_per_hour: float = Field(
        default=DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
        ge=0,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def date_range_is_ordered(self) -> ReportRequest:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class PointGeometry(BaseModel):
    """RFC 7946 WGS84 point geometry."""

    type: Literal["Point"] = "Point"
    coordinates: tuple[Longitude, Latitude]


class PolygonGeometry(BaseModel):
    """RFC 7946 polygon footprint geometry."""

    type: Literal["Polygon"]
    coordinates: list[list[Position]]


class MultiPolygonGeometry(BaseModel):
    """RFC 7946 multipolygon footprint geometry."""

    type: Literal["MultiPolygon"]
    coordinates: list[list[list[Position]]]


GeoJSONGeometry = Annotated[
    PointGeometry | PolygonGeometry | MultiPolygonGeometry,
    Field(discriminator="type"),
]


class PlaceResponse(BaseModel):
    """Public place fields shared by place and report responses."""

    place_key: str
    name: str
    kind: str
    lat: Latitude
    lon: Longitude
    timezone: str
    geometry: GeoJSONGeometry


class CoverageResponse(BaseModel):
    """Artifact metadata used to bound frontend controls."""

    observed_start: str | None
    observed_end: str | None
    place_count: int
    kinds: list[str]
    variable: str | None
    units: str | None
    gridded: bool


class SummaryResponse(BaseModel):
    """One leaderboard row; analysis-only columns are not exposed."""

    place_key: str
    name: str
    kind: str
    wet_fraction: Annotated[float, Field(ge=0, le=1)] | None
    wet_hours: Annotated[int, Field(ge=0)]
    valid_hours: Annotated[int, Field(ge=0)]
    rain_mm: Annotated[float, Field(ge=0)] | None
    coverage: Annotated[float, Field(ge=0, le=1)] | None
    tied: bool
    cell_places: Annotated[int, Field(ge=1)]
    rank: Annotated[int, Field(ge=1)]


class MonthlyResponse(BaseModel):
    """Wetness grouped by place and local calendar month."""

    place_key: str
    local_month: Annotated[int, Field(ge=1, le=12)]
    name: str
    kind: str
    valid_hours: Annotated[int, Field(ge=0)]
    wet_hours: Annotated[int, Field(ge=0)]
    rain_mm: Annotated[float, Field(ge=0)] | None
    wet_fraction: Annotated[float, Field(ge=0, le=1)] | None


class HourlyResponse(BaseModel):
    """Wetness grouped by place and local hour of day."""

    place_key: str
    local_hour: Annotated[int, Field(ge=0, le=23)]
    name: str
    kind: str
    hours_observed: Annotated[int, Field(ge=0)]
    wet_hours: Annotated[int, Field(ge=0)]
    rain_mm: Annotated[float, Field(ge=0)] | None
    wet_fraction: Annotated[float, Field(ge=0, le=1)] | None


class ReportResponse(BaseModel):
    """Complete bounded report including its reliability result."""

    summary: list[SummaryResponse]
    monthly: list[MonthlyResponse]
    hourly: list[HourlyResponse]
    reliability: Annotated[float, Field(ge=-1, le=1)] | None
    start: str | None
    end: str | None
    places: list[PlaceResponse]


class WetnessProperties(BaseModel):
    """Wet and valid counts for a grid node."""

    wet_hours: Annotated[int, Field(ge=0)]
    valid_hours: Annotated[int, Field(ge=0)]
    wet_fraction: Annotated[float, Field(ge=0, le=1)] | None


class WetnessFeature(BaseModel):
    """One GeoJSON node feature."""

    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: WetnessProperties


class WetnessResponse(BaseModel):
    """RFC 7946 FeatureCollection for wet-hours map data."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[WetnessFeature]

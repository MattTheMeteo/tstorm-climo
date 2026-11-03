"""Pydantic request and response models for the API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tstorm_climo.artifacts import MAX_SELECTED_PLACES
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR


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
    coordinates: tuple[float, float]


class PlaceResponse(BaseModel):
    """Public place fields shared by place and report responses."""

    place_key: str
    name: str
    kind: str
    lat: float
    lon: float
    timezone: str
    geometry: PointGeometry


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
    wet_fraction: float | None
    wet_hours: int
    valid_hours: int
    rain_mm: float | None
    coverage: float | None
    tied: bool
    cell_places: int
    rank: int


class MonthlyResponse(BaseModel):
    """Wetness grouped by place and local calendar month."""

    place_key: str
    local_month: int
    name: str
    kind: str
    valid_hours: int
    wet_hours: int
    rain_mm: float | None
    wet_fraction: float | None


class HourlyResponse(BaseModel):
    """Wetness grouped by place and local hour of day."""

    place_key: str
    local_hour: int
    name: str
    kind: str
    hours_observed: int
    wet_hours: int
    rain_mm: float | None
    wet_fraction: float | None


class ReportResponse(BaseModel):
    """Complete bounded report including its reliability result."""

    summary: list[SummaryResponse]
    monthly: list[MonthlyResponse]
    hourly: list[HourlyResponse]
    reliability: float | None
    start: str | None
    end: str | None
    places: list[PlaceResponse]


class WetnessProperties(BaseModel):
    """Wet and valid counts for a grid node."""

    wet_hours: int
    valid_hours: int
    wet_fraction: float | None


class WetnessFeature(BaseModel):
    """One GeoJSON node feature."""

    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: WetnessProperties


class WetnessResponse(BaseModel):
    """RFC 7946 FeatureCollection for wet-hours map data."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[WetnessFeature]

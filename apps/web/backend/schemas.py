"""Pydantic request models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tstorm_climo.artifacts import MAX_SELECTED_PLACES
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR


class ReportRequest(BaseModel):
    """A bounded drill-in: one selection of places, range, and threshold.

    ``place_keys`` is capped at ``MAX_SELECTED_PLACES`` so a request cannot
    exceed the reader's architectural bound before it reaches the query
    layer. ``start``/``end`` are date or timestamp strings; the query layer
    parses them and clamps to the artifact's coverage.
    """

    place_keys: list[str] = Field(min_length=1, max_length=MAX_SELECTED_PLACES)
    start: str | None = None
    end: str | None = None
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR

"""Extract hourly precipitation time series for places.

Reads the analysis archive once, over the contiguous grid block that
covers the requested places, then gathers each place's value out of that
block in memory. Reading per place would issue a scattered request each
time; the archive chunks ``y``/``x`` at 135 km, so one block read
amortises across every place inside it.

Deliberate choices:

- **Rates are converted to mm/h.** The conversion lives in
  :func:`tstorm_climo.hrrr.to_mm_per_hour` and is applied here once, so
  the archive's kg m-2 s-1 never reaches the output and no caller has
  to remember the factor of 3600.
- **Missing data is flagged, not silently dry.** The archive carries
  NaN where source data is missing, and ``NaN > threshold`` is ``False``.
  Emitting a ``valid`` column keeps "no data" distinguishable from "no
  rain", which a wet-hour count would otherwise conflate.
- **Footprint geometry is averaged, not ignored.** A place carrying a
  ``POLYGON``/``MULTIPOLYGON`` footprint is sampled as the equal-weighted
  mean over the grid nodes whose centers fall inside it
  (:meth:`tstorm_climo.grid.Grid.cover_cells`). Point-only or no-geometry
  places keep the nearest-node sample.

No threshold is applied here. Deciding what counts as rain belongs to
the climatology layer; this module returns rates and lets that layer
choose.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl
from polars.datatypes import DataType, DataTypeClass

from tstorm_climo.grid import Grid, GridCell
from tstorm_climo.hrrr import (
    MIN_VALID_DATE,
    PRECIPITATION_MM_PER_HOUR,
    PRECIPITATION_VARIABLE,
    Opener,
    open_analysis,
    to_mm_per_hour,
)
from tstorm_climo.places import Place

# Column names of the returned frame, for callers that build on it.
TIME_COLUMN = "time"
RATE_COLUMN = PRECIPITATION_MM_PER_HOUR
VALID_COLUMN = "valid"

SCHEMA: dict[str, DataType | DataTypeClass] = {
    "place_key": pl.String,
    TIME_COLUMN: pl.Datetime("us", "UTC"),
    RATE_COLUMN: pl.Float64,
    VALID_COLUMN: pl.Boolean,
    "y_index": pl.Int64,
    "x_index": pl.Int64,
    "grid_lat": pl.Float64,
    "grid_lon": pl.Float64,
    "snap_distance_km": pl.Float64,
    "tz_name": pl.String,
}


def extract_series(
    places: Sequence[Place],
    start: str,
    end: str,
    *,
    grid: Grid | None = None,
    opener: Opener | None = None,
) -> pl.DataFrame:
    """Extract an hourly precipitation series for each place.

    Args:
        places: Places to extract for. Each is mapped to the grid nodes
            its footprint covers, or to its nearest node when it has no
            polygon footprint (see :meth:`Grid.cover_cells`).
        start: Inclusive start of the time range. Clamped to
            :data:`~tstorm_climo.hrrr.MIN_VALID_DATE`.
        end: Inclusive end of the time range.
        grid: Grid to locate places on. Built from the archive's own
            lat/lon coordinates when omitted.
        opener: Override for ``dynamical_catalog.open`` (for tests).

    Returns:
        A long-format frame with one row per (place, hour), carrying the
        footprint-averaged (or nearest-node) rate in mm/h, a ``valid``
        flag, and the anchor node each place was mapped to. Rows where the
        archive is missing are present with ``valid = False`` rather than
        omitted.

    Raises:
        PlaceOffGridError: If a place cannot be mapped to the grid.
    """
    if not places:
        return pl.DataFrame(schema=SCHEMA)
    place_keys = [place.place_key for place in places]
    if len(set(place_keys)) != len(place_keys):
        raise ValueError("places must have unique place_key values")

    ds = open_analysis(opener=opener)
    active_grid = grid if grid is not None else Grid.from_dataset(ds)
    anchors = active_grid.locate_places(places)

    # Footprint nodes when a polygon covers any, else the anchor node.
    sampling = _sampling_cells(active_grid, places, anchors)

    row_slice, col_slice = _sampling_bounds(sampling, active_grid)
    # Slice time and space *before* converting units: converting first
    # would materialize the whole 748 GiB archive (the read is lazy until
    # a value is needed, and the multiply would force it).
    block = (
        ds[PRECIPITATION_VARIABLE]
        .sel(time=slice(max(start, MIN_VALID_DATE), end))
        .isel(y=row_slice, x=col_slice)
    )
    if block.sizes["time"] == 0:
        return pl.DataFrame(schema=SCHEMA)
    block = to_mm_per_hour(block)

    # Footprint-weighted average per (place, time).
    mm_per_hour = _weighted_rates(
        block.values, sampling, row_slice.start, col_slice.start
    )
    times = np.asarray(block["time"].values)

    return _to_frame(places, anchors, times, mm_per_hour)


def _to_frame(
    places: Sequence[Place],
    cells: Sequence[GridCell],
    times: np.ndarray,
    mm_per_hour: np.ndarray,
) -> pl.DataFrame:
    """Assemble the long-format frame from (place, time) arrays."""
    n_places = len(places)
    n_times = times.size

    # Repeat each stable place key across its hours, and each hour across
    # every place.
    place_keys = np.repeat(
        np.array([place.place_key for place in places], dtype=object), n_times
    )
    frame_times = np.tile(times, n_places)
    rates = mm_per_hour.T.reshape(-1)
    valid = np.isfinite(rates)

    def per_place(attribute: str, dtype: DataType | DataTypeClass) -> pl.Series:
        values = np.repeat(np.array([getattr(c, attribute) for c in cells]), n_times)
        return pl.Series(attribute, values, dtype=dtype)

    def per_place_str(attribute: str) -> pl.Series:
        values = np.repeat(
            np.array([getattr(p, attribute) for p in places], dtype=object),
            n_times,
        )
        return pl.Series(attribute, values, dtype=pl.String)

    return pl.DataFrame(
        {
            "place_key": pl.Series(place_keys, dtype=pl.String),
            TIME_COLUMN: pl.Series(frame_times).cast(SCHEMA[TIME_COLUMN]),
            RATE_COLUMN: pl.Series(np.where(valid, rates, np.nan), dtype=pl.Float64),
            VALID_COLUMN: pl.Series(valid, dtype=pl.Boolean),
            "y_index": per_place("y_index", pl.Int64),
            "x_index": per_place("x_index", pl.Int64),
            "grid_lat": per_place("grid_lat", pl.Float64),
            "grid_lon": per_place("grid_lon", pl.Float64),
            "snap_distance_km": per_place("distance_km", pl.Float64),
            "tz_name": per_place_str("timezone"),
        },
        schema=SCHEMA,
    )


def _sampling_cells(
    grid: Grid, places: Sequence[Place], anchors: Sequence[GridCell]
) -> list[list[tuple[int, int, float]]]:
    """Per-place sampling cells: covered footprint nodes, else the anchor.

    A place carrying polygon geometry is sampled over every grid node
    whose center falls inside it (:meth:`Grid.cover_cells`). A place with
    no such geometry -- or a point/small polygon that covers no node
    center -- falls back to its nearest node with weight 1.0.
    """
    sampling: list[list[tuple[int, int, float]]] = []
    for place, anchor in zip(places, anchors, strict=True):
        covered = grid.cover_cells(place.geometry_wkb) if place.geometry_wkb else []
        if covered:
            sampling.append(
                [(cell.y_index, cell.x_index, cell.weight) for cell in covered]
            )
        else:
            sampling.append([(anchor.y_index, anchor.x_index, 1.0)])
    return sampling


def _sampling_bounds(
    sampling: Sequence[Sequence[tuple[int, int, float]]], grid: Grid
) -> tuple[slice, slice]:
    """Row/column slices covering every sampled cell, clamped to the grid."""
    y_values = np.array([y for cells in sampling for y, _, _ in cells])
    x_values = np.array([x for cells in sampling for _, x, _ in cells])
    n_rows, n_cols = grid.shape
    return (
        slice(int(max(y_values.min(), 0)), int(min(y_values.max() + 1, n_rows))),
        slice(int(max(x_values.min(), 0)), int(min(x_values.max() + 1, n_cols))),
    )


def _weighted_rates(
    values: np.ndarray,
    sampling: Sequence[Sequence[tuple[int, int, float]]],
    row_start: int,
    col_start: int,
) -> np.ndarray:
    """Footprint-weighted average rate, shaped ``(time, place)``.

    Each place's cells carry ``(y_index, x_index, weight)`` in absolute
    grid coordinates; ``row_start``/``col_start`` translate them into the
    block's local indexing.
    """
    rates = np.empty((values.shape[0], len(sampling)), dtype=np.float64)
    for i, cells in enumerate(sampling):
        weighted = np.zeros(values.shape[0], dtype=np.float64)
        for y, x, weight in cells:
            weighted += weight * values[:, y - row_start, x - col_start]
        rates[:, i] = weighted
    return rates

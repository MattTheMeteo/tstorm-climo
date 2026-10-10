"""Extract hourly precipitation time series for places.

Reads the analysis archive once, over the contiguous grid block that
covers the requested places, then gathers each place's grid node out of
that block in memory. Reading per place would issue a scattered request
each time; the archive chunks ``y``/``x`` at 135 km, so one block read
amortises across every place inside it.

Two deliberate choices:

- **Rates are converted to mm/h on the way out.** The archive stores
  ``precipitation_surface`` in kg m-2 s-1, where 1 unit is 1 mm/s is
  3600 mm/h. That unit is easy to misread by three orders of magnitude,
  so it never reaches the output.
- **Missing data is flagged, not silently dry.** The archive carries
  NaN where source data is missing, and ``NaN > threshold`` is ``False``.
  Emitting a ``valid`` column keeps "no data" distinguishable from "no
  rain", which a wet-hour count would otherwise conflate.

No threshold is applied here. Deciding what counts as rain belongs to
the climatology layer; this module returns rates and lets that layer
choose.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl

from tstorm_climo.grid import Grid, GridCell
from tstorm_climo.hrrr import (
    MIN_VALID_DATE,
    PRECIPITATION_VARIABLE,
    Opener,
    open_analysis,
)
from tstorm_climo.places import Place

# kg m-2 s-1 -> mm/h. 1 kg m-2 of liquid water is 1 mm of depth, so a
# rate of 1 kg m-2 s-1 is 1 mm/s, which is 3600 mm/h.
SECONDS_PER_HOUR = 3600.0

# Column names of the returned frame, for callers that build on it.
TIME_COLUMN = "time"
RATE_COLUMN = "precipitation_mm_per_hour"
VALID_COLUMN = "valid"

SCHEMA = {
    "place_id": pl.UInt32,
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
        places: Places to extract for. Each is mapped to its nearest
            grid node via :meth:`Grid.locate_places`.
        start: Inclusive start of the time range. Clamped to
            :data:`~tstorm_climo.hrrr.MIN_VALID_DATE`.
        end: Inclusive end of the time range.
        grid: Grid to locate places on. Built from the archive's own
            lat/lon coordinates when omitted.
        opener: Override for ``dynamical_catalog.open`` (for tests).

    Returns:
        A long-format frame with one row per (place, hour), carrying the
        rate in mm/h, a ``valid`` flag, and the grid node each place
        was mapped to. Rows where the archive is missing are present
        with ``valid = False`` rather than omitted.

    Raises:
        PlaceOffGridError: If a place cannot be mapped to the grid.
    """
    if not places:
        return pl.DataFrame(schema=SCHEMA)

    ds = open_analysis(opener=opener)
    active_grid = grid if grid is not None else Grid.from_dataset(ds)
    cells = active_grid.locate_places(places)

    row_slice, col_slice = _block_bounds(cells, active_grid)
    block = (
        ds[PRECIPITATION_VARIABLE]
        .sel(time=slice(max(start, MIN_VALID_DATE), end))
        .isel(y=row_slice, x=col_slice)
    )
    if block.sizes["time"] == 0:
        return pl.DataFrame(schema=SCHEMA)

    # Gather each place's node out of the block, then convert units.
    y_idx = np.array([c.y_index for c in cells]) - row_slice.start
    x_idx = np.array([c.x_index for c in cells]) - col_slice.start
    values = block.values[:, y_idx, x_idx].astype(np.float64)
    mm_per_hour = values * SECONDS_PER_HOUR
    times = np.asarray(block["time"].values)

    return _to_frame(places, cells, times, mm_per_hour)


def _to_frame(
    places: Sequence[Place],
    cells: Sequence[GridCell],
    times: np.ndarray,
    mm_per_hour: np.ndarray,
) -> pl.DataFrame:
    """Assemble the long-format frame from (place, time) arrays."""
    n_places = len(places)
    n_times = times.size

    # Repeat each place's identity across its hours, and each hour
    # across every place.
    place_ids = np.repeat(np.arange(n_places, dtype=np.uint32), n_times)
    frame_times = np.tile(times, n_places)
    rates = mm_per_hour.T.reshape(-1)
    valid = np.isfinite(rates)

    def per_place(attribute: str, dtype: pl.DataType) -> pl.Series:
        values = np.repeat(
            np.array([getattr(c, attribute) for c in cells]), n_times
        )
        return pl.Series(attribute, values, dtype=dtype)

    def per_place_str(attribute: str) -> pl.Series:
        values = np.repeat(
            np.array([getattr(p, attribute) for p in places], dtype=object),
            n_times,
        )
        return pl.Series(attribute, values, dtype=pl.String)

    return pl.DataFrame(
        {
            "place_id": pl.Series(place_ids, dtype=pl.UInt32),
            TIME_COLUMN: pl.Series(frame_times).cast(SCHEMA[TIME_COLUMN]),
            RATE_COLUMN: pl.Series(
                np.where(valid, rates, np.nan), dtype=pl.Float64
            ),
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


def _block_bounds(
    cells: Sequence[GridCell], grid: Grid
) -> tuple[slice, slice]:
    """Row/column slices covering every cell, clamped to the grid."""
    n_rows, n_cols = grid.shape
    y_values = np.array([c.y_index for c in cells])
    x_values = np.array([c.x_index for c in cells])
    return (
        slice(int(max(y_values.min(), 0)), int(min(y_values.max() + 1, n_rows))),
        slice(int(max(x_values.min(), 0)), int(min(x_values.max() + 1, n_cols))),
    )
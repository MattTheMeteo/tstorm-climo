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
  NaN propagates through a footprint average, so one missing node would
  discard the whole hour. That costs nothing in practice, because the
  archive's gaps are whole-timestep and domain-wide rather than
  scattered per cell. Measured over summers 2018-2022 on an 11x12 North
  Shore block: on every gapped hour 100% of the block was NaN (min,
  median and max all 100%), and a 21x20 block over summer 2024 had zero
  always-NaN and zero partially-NaN nodes. Gapped hours re-read across
  the full domain were 100.000% NaN. So "some node missing" and "every
  node missing" are the same event, and a footprint loses exactly the
  hours a point sample would have lost -- strict propagation and
  averaging over valid nodes give identical results here. If the archive
  ever gains scattered per-cell gaps this stops holding, and footprint
  coverage would fall toward ``1 - (1 - p) ** N`` with no error raised.
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
from typing import Any, NamedTuple

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

# How a place's value was sampled. ``footprint`` means an average over
# every covered node; ``point`` means a single nearest node.
SAMPLING_COLUMN = "sampling"
N_CELLS_COLUMN = "n_cells"
# Canonical string form of the sampled node set. Two places sharing a
# sampling_key sampled exactly the same nodes, so their numbers are not
# merely close -- they are the same measurement, and cannot be ranked
# against each other.
SAMPLING_KEY_COLUMN = "sampling_key"

FOOTPRINT_SAMPLING = "footprint"
POINT_SAMPLING = "point"

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
    SAMPLING_COLUMN: pl.String,
    N_CELLS_COLUMN: pl.Int64,
    SAMPLING_KEY_COLUMN: pl.String,
}

SAMPLING_FRAME_SCHEMA: dict[str, DataType | DataTypeClass] = {
    "place_key": pl.String,
    "y_index": pl.Int64,
    "x_index": pl.Int64,
    "grid_lat": pl.Float64,
    "grid_lon": pl.Float64,
    "weight": pl.Float64,
    SAMPLING_COLUMN: pl.String,
    N_CELLS_COLUMN: pl.Int64,
    SAMPLING_KEY_COLUMN: pl.String,
}


class SamplingCell(NamedTuple):
    """One grid node contributing to a place's value, and its share.

    This is the per-node record that :func:`sampling_cells` returns. It
    deliberately carries the node identity *and* the weight, so the full
    footprint survives into the artifact rather than being collapsed to
    a single anchor node.

    Attributes:
        y_index: Row index of the node in the source dataset's ``y``.
        x_index: Column index of the node in the source dataset's ``x``.
        grid_lat: Latitude of the node, decimal degrees.
        grid_lon: Longitude of the node, decimal degrees.
        weight: Share of the place's value this node carries. Weights
            across one place sum to 1.0.
        sampling: :data:`FOOTPRINT_SAMPLING` or :data:`POINT_SAMPLING`.
    """

    y_index: int
    x_index: int
    grid_lat: float
    grid_lon: float
    weight: float
    sampling: str

    @property
    def key(self) -> tuple[int, int]:
        """The ``(y, x)`` node identifier."""
        return (self.y_index, self.x_index)


class PlaceSampling(NamedTuple):
    """How one place was sampled: its anchor node and its sampled cells.

    Keeping both is the point. ``anchor`` is the node nearest the place's
    representative point -- a stable per-place fact, but *not* what was
    averaged once a footprint is in play. ``cells`` is what the value
    actually came from.

    Attributes:
        anchor: Nearest node to the place's representative point.
        cells: Every node contributing to the place's value.
    """

    anchor: GridCell
    cells: list[SamplingCell]

    @property
    def sampling(self) -> str:
        """``"footprint"`` or ``"point"``."""
        return self.cells[0].sampling

    @property
    def n_cells(self) -> int:
        """Number of nodes averaged."""
        return len(self.cells)

    @property
    def key(self) -> str:
        """Canonical identity of the sampled node set."""
        return sampling_key(self.cells)


def sampling_cells(grid: Grid, places: Sequence[Place]) -> list[PlaceSampling]:
    """Per-place sampling nodes: covered footprint nodes, else the anchor.

    A place carrying polygon geometry is sampled over every grid node
    whose center falls inside it (:meth:`Grid.cover_cells`). A place with
    no such geometry -- or a point/small polygon that covers no node
    center -- falls back to its nearest node with weight 1.0.

    This is a pure function of the grid and the places, with no archive
    read, so callers that need the footprint without re-extracting (the
    artifact builder, the summary layer) can call it directly.
    """
    result: list[PlaceSampling] = []
    for place, anchor in zip(places, grid.locate_places(places), strict=True):
        covered = grid.cover_cells(place.geometry_wkb) if place.geometry_wkb else []
        if covered:
            cells = [
                SamplingCell(
                    y_index=cell.y_index,
                    x_index=cell.x_index,
                    grid_lat=cell.grid_lat,
                    grid_lon=cell.grid_lon,
                    weight=cell.weight,
                    sampling=FOOTPRINT_SAMPLING,
                )
                for cell in covered
            ]
        else:
            cells = [
                SamplingCell(
                    y_index=anchor.y_index,
                    x_index=anchor.x_index,
                    grid_lat=anchor.grid_lat,
                    grid_lon=anchor.grid_lon,
                    weight=1.0,
                    sampling=POINT_SAMPLING,
                )
            ]
        result.append(PlaceSampling(anchor=anchor, cells=cells))
    return result


def sampling_key(cells: Sequence[SamplingCell]) -> str:
    """Canonical identity of a sampled node set.

    Order-independent, so two places whose footprints cover the same
    nodes in a different discovery order still produce the same key.
    """
    return ",".join(f"{y}x{x}" for y, x in sorted(cell.key for cell in cells))


def sampling_frame(
    places: Sequence[Place], sampling: Sequence[PlaceSampling]
) -> pl.DataFrame:
    """Flatten per-place sampling nodes into one row per (place, node).

    The result is the authoritative record of what each place was
    sampled from. It is static per place, so it is written once beside
    the observations rather than repeated on every hourly row.
    """
    rows: list[dict[str, Any]] = []
    for place, entry in zip(places, sampling, strict=True):
        for cell in entry.cells:
            rows.append(
                {
                    "place_key": place.place_key,
                    "y_index": cell.y_index,
                    "x_index": cell.x_index,
                    "grid_lat": cell.grid_lat,
                    "grid_lon": cell.grid_lon,
                    "weight": cell.weight,
                    SAMPLING_COLUMN: cell.sampling,
                    N_CELLS_COLUMN: entry.n_cells,
                    SAMPLING_KEY_COLUMN: entry.key,
                }
            )
    return pl.DataFrame(rows, schema=SAMPLING_FRAME_SCHEMA)


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
    # Footprint nodes when a polygon covers any, else the anchor node.
    sampling = sampling_cells(active_grid, places)

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

    return _to_frame(places, sampling, times, mm_per_hour)


def _to_frame(
    places: Sequence[Place],
    sampling: Sequence[PlaceSampling],
    times: np.ndarray,
    mm_per_hour: np.ndarray,
) -> pl.DataFrame:
    """Assemble the long-format frame from (place, time) arrays.

    The per-place ``y_index``/``x_index``/``grid_lat``/``grid_lon``/
    ``snap_distance_km`` columns describe the place's **anchor** -- the
    node nearest its representative point -- and keep their original
    meaning for backwards compatibility. They are not the nodes that
    were averaged once a footprint applies; ``n_cells`` and
    ``sampling_key`` say what actually was, and :func:`sampling_frame`
    holds the full node list.
    """
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

    def repeated(name: str, values: Sequence[Any], dtype: Any) -> pl.Series:
        return pl.Series(name, np.repeat(np.array(values), n_times), dtype=dtype)

    return pl.DataFrame(
        {
            "place_key": pl.Series(place_keys, dtype=pl.String),
            TIME_COLUMN: pl.Series(frame_times).cast(SCHEMA[TIME_COLUMN]),
            RATE_COLUMN: pl.Series(np.where(valid, rates, np.nan), dtype=pl.Float64),
            VALID_COLUMN: pl.Series(valid, dtype=pl.Boolean),
            "y_index": repeated(
                "y_index", [e.anchor.y_index for e in sampling], pl.Int64
            ),
            "x_index": repeated(
                "x_index", [e.anchor.x_index for e in sampling], pl.Int64
            ),
            "grid_lat": repeated(
                "grid_lat", [e.anchor.grid_lat for e in sampling], pl.Float64
            ),
            "grid_lon": repeated(
                "grid_lon", [e.anchor.grid_lon for e in sampling], pl.Float64
            ),
            "snap_distance_km": repeated(
                "snap_distance_km",
                [e.anchor.distance_km for e in sampling],
                pl.Float64,
            ),
            "tz_name": pl.Series(
                np.repeat(
                    np.array([place.timezone for place in places], dtype=object),
                    n_times,
                ),
                dtype=pl.String,
            ),
            SAMPLING_COLUMN: repeated(
                SAMPLING_COLUMN, [e.sampling for e in sampling], pl.String
            ),
            N_CELLS_COLUMN: repeated(
                N_CELLS_COLUMN, [e.n_cells for e in sampling], pl.Int64
            ),
            SAMPLING_KEY_COLUMN: repeated(
                SAMPLING_KEY_COLUMN, [e.key for e in sampling], pl.String
            ),
        },
        schema=SCHEMA,
    )


def _sampling_bounds(
    sampling: Sequence[PlaceSampling], grid: Grid
) -> tuple[slice, slice]:
    """Row/column slices covering every sampled node.

    Every index comes from :meth:`Grid.cover_cells` or
    :meth:`Grid.locate_places`, so they are already in range and no
    clamping is needed -- only the extent has to be computed.
    """
    n_rows, n_cols = grid.shape
    assert n_rows > 0 and n_cols > 0
    y_values = [cell.y_index for entry in sampling for cell in entry.cells]
    x_values = [cell.x_index for entry in sampling for cell in entry.cells]
    return (
        slice(min(y_values), max(y_values) + 1),
        slice(min(x_values), max(x_values) + 1),
    )


def _weighted_rates(
    values: np.ndarray,
    sampling: Sequence[PlaceSampling],
    row_start: int,
    col_start: int,
) -> np.ndarray:
    """Footprint-weighted average rate, shaped ``(time, place)``.

    Each place's cells carry absolute grid coordinates; ``row_start`` and
    ``col_start`` translate them into the block's local indexing.

    A NaN in any cell propagates into the sum, so the whole hour is
    flagged invalid. That is free in practice because the archive's gaps
    are whole-timestep and domain-wide -- see the module docstring.
    """
    rates = np.empty((values.shape[0], len(sampling)), dtype=np.float64)
    for i, entry in enumerate(sampling):
        weighted = np.zeros(values.shape[0], dtype=np.float64)
        for cell in entry.cells:
            weighted += (
                cell.weight
                * values[:, cell.y_index - row_start, cell.x_index - col_start]
            )
        rates[:, i] = weighted
    return rates

"""Map places onto the analysis grid.

The dynamical.org HRRR archive is on a native Lambert Conformal Conic
grid, but it also ships pre-computed 2-D ``latitude``/``longitude``
coordinate arrays on that grid. Those arrays let us place a point on the
grid with a nearest-neighbour search in plain NumPy -- no ``pyproj``
transform, and no assumption that the grid is regular in lon/lat.

Each node stands in for a cell around it. A point whose nearest node is
further than :attr:`Grid.max_snap_km` is rejected rather than silently
snapped to the domain edge: on a ranked leaderboard a wrong answer is
worse than no answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

import numpy as np
import xarray as xr
from pydantic import BaseModel, ConfigDict

from tstorm_climo.places import Place, great_circle_km

LATITUDE_COORD = "latitude"
LONGITUDE_COORD = "longitude"

# Metres per degree of latitude, for converting cell size to degrees.
METRES_PER_DEGREE = 111_320.0

# A point may sit at most this multiple of the cell diagonal from its
# node. 0.5 is the geometric maximum for a point inside the domain;
# 0.75 leaves headroom for points just outside the edge.
MAX_SNAP_FACTOR = 0.75

# Cap on grid nodes held in the pairwise distance array at once. Override
# per-Grid with the ``batch_nodes`` argument; lower it to trade speed for
# peak memory, raise it to go faster.
BATCH_NODES: int = 250_000

# Floor on cos(lat) when converting a ground distance to a longitude
# margin. Without it a place near a pole would divide by ~zero and inflate
# the search window to the whole domain.
MIN_COS_LAT = 0.05

# Point-in-polygon query run against the grid's cell centers. The bbox
# predicate lets DuckDB skip candidates before the spatial test, and the
# ``ORDER BY`` keeps the result deterministic.
_COVER_QUERY = """
    WITH poly AS (SELECT ST_GeomFromWKB(?::BLOB) AS geom)
    SELECT c.y_index, c.x_index, c.lon, c.lat
    FROM centers c, poly
    WHERE c.lon >= ST_XMin(geom) AND c.lon <= ST_XMax(geom)
      AND c.lat >= ST_YMin(geom) AND c.lat <= ST_YMax(geom)
      AND ST_Intersects(ST_Point(c.lon, c.lat), geom)
    ORDER BY c.y_index, c.x_index
"""


class PlaceOffGridError(LookupError):
    """Raised when no grid node is close enough to represent a point."""


class Bounds(NamedTuple):
    """Geographic bounds of a grid, in decimal degrees.

    Attributes:
        south: Lowest latitude in the grid.
        west: Lowest longitude in the grid.
        north: Highest latitude in the grid.
        east: Highest longitude in the grid.
    """

    south: float
    west: float
    north: float
    east: float


class GridNode(BaseModel):
    """A grid node and where it sits in the source dataset.

    The identity shared by every way of referencing a node -- whether it
    was chosen as a place's nearest node (:class:`GridCell`) or covered
    by a polygon footprint (:class:`FootprintCell`).

    Attributes:
        y_index: Row index of the node in the source dataset's ``y``.
        x_index: Column index of the node in the source dataset's ``x``.
        grid_lat: Latitude of the node, decimal degrees.
        grid_lon: Longitude of the node, decimal degrees.
    """

    model_config = ConfigDict(frozen=True)

    y_index: int
    x_index: int
    grid_lat: float
    grid_lon: float

    @property
    def key(self) -> tuple[int, int]:
        """The (y, x) node identifier, for grouping places by cell."""
        return (self.y_index, self.x_index)


class GridCell(GridNode):
    """A grid node standing in for a place.

    Attributes:
        distance_km: Great-circle distance from the place to the node.
    """

    distance_km: float


class FootprintCell(GridNode):
    """A grid node covered by a place's polygon footprint.

    Attributes:
        weight: Share of the footprint this node represents. Weights
            across one footprint sum to 1.0.
    """

    weight: float


class Grid:
    """A latitude/longitude lookup over the analysis grid.

    Build from a dataset's 2-D lat/lon coordinates with
    :meth:`from_dataset`, which is also how tests construct a small
    synthetic grid.
    """

    def __init__(
        self,
        latitude: np.ndarray,
        longitude: np.ndarray,
        *,
        name: str = "grid",
        max_snap_factor: float = MAX_SNAP_FACTOR,
        batch_nodes: int = BATCH_NODES,
    ) -> None:
        lat = np.asarray(latitude, dtype=np.float64)
        lon = np.asarray(longitude, dtype=np.float64)
        if lat.shape != lon.shape:
            raise ValueError(
                f"latitude {lat.shape} and longitude {lon.shape} "
                "must have the same shape"
            )
        if lat.ndim != 2:
            raise ValueError(f"latitude/longitude must be 2-D (y, x), got {lat.ndim}-D")
        if max_snap_factor <= 0:
            raise ValueError(f"max_snap_factor must be positive, got {max_snap_factor}")
        if batch_nodes < 1:
            raise ValueError(f"batch_nodes must be at least 1, got {batch_nodes}")
        self._lat = lat
        self._lon = lon
        self._name = name
        self._spacing_km = _median_spacing_km(lat, lon)
        self._max_snap_factor = float(max_snap_factor)
        self._batch_nodes = int(batch_nodes)
        self._spatial: Any | None = None

    @classmethod
    def from_dataset(
        cls,
        ds: xr.Dataset,
        *,
        name: str = "grid",
        max_snap_factor: float = MAX_SNAP_FACTOR,
        batch_nodes: int = BATCH_NODES,
    ) -> Grid:
        """Build a Grid from a dataset's 2-D lat/lon coordinates."""
        missing = [
            coord
            for coord in (LATITUDE_COORD, LONGITUDE_COORD)
            if coord not in ds.coords
        ]
        if missing:
            raise KeyError(
                f"dataset has no {'/'.join(missing)} coordinate(s); "
                "a 2-D (y, x) coordinate pair is required"
            )
        return cls(
            ds[LATITUDE_COORD].values,
            ds[LONGITUDE_COORD].values,
            name=name,
            max_snap_factor=max_snap_factor,
            batch_nodes=batch_nodes,
        )

    @property
    def shape(self) -> tuple[int, int]:
        """Grid shape as (y, x)."""
        return (self._lat.shape[0], self._lat.shape[1])

    @property
    def spacing_km(self) -> float:
        """Median great-circle spacing between neighbouring nodes, km."""
        return self._spacing_km

    @property
    def max_snap_km(self) -> float:
        """Furthest a point may sit from its node and still be trusted."""
        return self._max_snap_factor * self._spacing_km * float(np.sqrt(2.0))

    def bounds(self) -> Bounds:
        """Geographic bounds of the grid."""
        return Bounds(
            south=float(np.nanmin(self._lat)),
            west=float(np.nanmin(self._lon)),
            north=float(np.nanmax(self._lat)),
            east=float(np.nanmax(self._lon)),
        )

    def locate(self, lat: float, lon: float) -> GridCell:
        """Find the grid node nearest a single latitude and longitude."""
        return self._locate(
            np.array([lat], dtype=np.float64),
            np.array([lon], dtype=np.float64),
            ["point"],
        )[0]

    def locate_places(self, places: Sequence[Place]) -> list[GridCell]:
        """Find the nearest grid node for each place.

        The search is windowed to a region covering all the places, so
        cost scales with the region of interest rather than the domain.

        Args:
            places: Places to locate.

        Returns:
            One :class:`GridCell` per place, in the same order.

        Raises:
            PlaceOffGridError: If any place is further than
                :attr:`max_snap_km` from its nearest node.
        """
        if not places:
            return []
        return self._locate(
            np.array([p.lat for p in places], dtype=np.float64),
            np.array([p.lon for p in places], dtype=np.float64),
            [p.display_name for p in places],
        )

    def cover_cells(self, geometry_wkb: bytes) -> list[FootprintCell]:
        """Return grid nodes whose centers fall inside a polygon footprint.

        A place's OSM footprint (WKB, WGS84) is sampled by averaging the
        grid nodes it covers, weighting each covered node equally. This is
        the polygon alternative to :meth:`locate`, which snaps to a single
        nearest node.

        Args:
            geometry_wkb: A WKB ``POLYGON`` or ``MULTIPOLYGON`` in WGS84,
                as stored by the catalog builders. ``POINT`` footprints are
                accepted but almost never cover a node center.

        Returns:
            Covered nodes with equal ``weight`` summing to 1.0, ordered by
            ``(y_index, x_index)``. Empty when no node center falls inside
            the footprint (a small or narrow polygon between nodes, or a
            point feature); callers should fall back to :meth:`locate`.
        """
        if not geometry_wkb:
            return []
        rows = (
            self._spatial_connection().execute(_COVER_QUERY, [geometry_wkb]).fetchall()
        )
        if not rows:
            return []
        weight = 1.0 / len(rows)
        return [
            FootprintCell(
                y_index=int(y),
                x_index=int(x),
                grid_lat=float(lat),
                grid_lon=float(lon),
                weight=weight,
            )
            for y, x, lon, lat in rows
        ]

    def _spatial_connection(self) -> Any:
        """A DuckDB connection with the grid's finite cell centers loaded.

        Built once and cached; the ``spatial`` extension provides the
        point-in-polygon test. Non-finite coordinates are excluded so a
        NaN node can never be returned as covered.

        The centers are registered as a dict of NumPy arrays, which DuckDB
        takes directly -- avoiding a DataFrame dependency for what is
        four parallel columns.
        """
        if self._spatial is None:
            import duckdb

            connection = duckdb.connect()
            connection.execute("INSTALL spatial; LOAD spatial;")
            finite = np.isfinite(self._lat) & np.isfinite(self._lon)
            y_index, x_index = np.nonzero(finite)
            connection.register(
                "centers",
                {
                    "y_index": y_index,
                    "x_index": x_index,
                    "lon": self._lon[finite],
                    "lat": self._lat[finite],
                },
            )
            self._spatial = connection
        return self._spatial

    def _locate(
        self, place_lat: np.ndarray, place_lon: np.ndarray, labels: list[str]
    ) -> list[GridCell]:
        """Nearest-node search over a window covering all the points."""
        row_slice, col_slice = self._window(place_lat, place_lon)
        sub_lat = self._lat[row_slice, col_slice]
        sub_lon = self._lon[row_slice, col_slice]
        n_cols = sub_lat.shape[1]
        flat_lat = sub_lat.ravel()
        flat_lon = sub_lon.ravel()

        # Non-finite coordinates must never win an argmin.
        safe_lat = np.where(np.isfinite(flat_lat), flat_lat, np.inf)
        safe_lon = np.where(np.isfinite(flat_lon), flat_lon, np.inf)

        count = len(place_lat)
        best_flat = np.empty(count, dtype=np.int64)
        batch = max(1, self._batch_nodes // max(flat_lat.size, 1))
        for start in range(0, count, batch):
            chunk = slice(start, start + batch)
            p_lat = place_lat[chunk][None, :]
            p_lon = place_lon[chunk][None, :]
            # Longitude degrees shrink toward the poles; scale so the
            # search minimises ground distance, not degree distance.
            cos_lat = np.cos(np.radians(p_lat))
            d_lat = safe_lat[:, None] - p_lat
            d_lon = (safe_lon[:, None] - p_lon) * cos_lat
            d2 = d_lat * d_lat + d_lon * d_lon
            best_flat[chunk] = np.argmin(d2, axis=0)

        cells: list[GridCell] = []
        for lat, lon, label, flat in zip(
            place_lat, place_lon, labels, best_flat, strict=True
        ):
            sub_row, sub_col = divmod(int(flat), n_cols)
            y_index = row_slice.start + sub_row
            x_index = col_slice.start + sub_col
            grid_lat = float(self._lat[y_index, x_index])
            grid_lon = float(self._lon[y_index, x_index])
            distance = float(great_circle_km(lat, lon, grid_lat, grid_lon))
            if not np.isfinite(distance) or distance > self.max_snap_km:
                raise PlaceOffGridError(
                    f"{label} at ({lat:.4f}, {lon:.4f}) is {distance:.2f} km "
                    f"from the nearest grid node, beyond the "
                    f"{self.max_snap_km:.2f} km limit; it is probably "
                    "outside the analysis domain"
                )
            cells.append(
                GridCell(
                    y_index=y_index,
                    x_index=x_index,
                    grid_lat=grid_lat,
                    grid_lon=grid_lon,
                    distance_km=distance,
                )
            )
        return cells

    def _window(
        self, place_lat: np.ndarray, place_lon: np.ndarray
    ) -> tuple[slice, slice]:
        """Row/column slices covering every point, plus a margin of slack.

        The slack is :attr:`max_snap_km` expressed in degrees, so that any
        node a point is close enough to accept is also inside the window.
        The longitude margin is widened by ``1 / cos(lat)`` because a
        degree of longitude covers less ground away from the equator --
        the same correction :meth:`_locate` makes when it ranks nodes.
        """
        lat_margin = self.max_snap_km * 1000.0 / METRES_PER_DEGREE
        cos_lat = np.clip(np.cos(np.radians(place_lat)), MIN_COS_LAT, 1.0)
        # The largest margin across the places, so a window sized for the
        # most poleward point still holds every other point's nearest node.
        lon_margin = float(
            (self.max_snap_km * 1000.0 / (METRES_PER_DEGREE * cos_lat)).max()
        )
        rows = np.nonzero(
            (
                (self._lat >= place_lat.min() - lat_margin)
                & (self._lat <= place_lat.max() + lat_margin)
            ).any(axis=1)
        )[0]
        cols = np.nonzero(
            (
                (self._lon >= place_lon.min() - lon_margin)
                & (self._lon <= place_lon.max() + lon_margin)
            ).any(axis=0)
        )[0]

        if rows.size == 0 or cols.size == 0:
            # Nothing in range: search the whole grid so the caller gets
            # a proper PlaceOffGridError instead of an index error.
            n_rows, n_cols = self.shape
            return slice(0, n_rows), slice(0, n_cols)
        return (
            slice(int(rows[0]), int(rows[-1]) + 1),
            slice(int(cols[0]), int(cols[-1]) + 1),
        )


def _median_spacing_km(lat: np.ndarray, lon: np.ndarray) -> float:
    """Median great-circle distance between neighbouring nodes, km."""
    spacings: list[float] = []
    with np.errstate(invalid="ignore"):
        if lat.shape[0] > 1:
            spacings.append(
                float(
                    np.nanmedian(
                        great_circle_km(
                            lat[:-1, :], lon[:-1, :], lat[1:, :], lon[1:, :]
                        )
                    )
                )
            )
        if lat.shape[1] > 1:
            spacings.append(
                float(
                    np.nanmedian(
                        great_circle_km(
                            lat[:, :-1], lon[:, :-1], lat[:, 1:], lon[:, 1:]
                        )
                    )
                )
            )
    positive = [s for s in spacings if s and np.isfinite(s) and s > 0]
    if not positive:
        raise ValueError("grid has no measurable spacing between nodes")
    return max(positive)

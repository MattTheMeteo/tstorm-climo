"""Tests for Grid, using a synthetic lat/lon grid instead of the network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from tstorm_climo.grid import (
    BATCH_NODES,
    MAX_SNAP_FACTOR,
    FootprintCell,
    Grid,
    GridCell,
    GridNode,
    PlaceOffGridError,
)
from tstorm_climo.places import Place, great_circle_km


def wkb(wkt: str) -> bytes:
    """Encode WKT as WKB using DuckDB's spatial extension."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        row = con.execute("SELECT ST_AsWKB(ST_GeomFromText(?))", [wkt]).fetchone()
        assert row is not None
        return bytes(row[0])
    finally:
        con.close()


def box_wkt(grid: Grid, ys: list[int], xs: list[int], margin: float = 0.005) -> str:
    """A WKT polygon box around the given nodes, with a small margin."""
    lats = [float(grid._lat[y, x]) for y, x in zip(ys, xs, strict=True)]
    lons = [float(grid._lon[y, x]) for y, x in zip(ys, xs, strict=True)]
    south, north = min(lats) - margin, max(lats) + margin
    west, east = min(lons) - margin, max(lons) + margin
    return (
        f"POLYGON(({west} {north}, {east} {north}, {east} {south}, "
        f"{west} {south}, {west} {north}))"
    )


def make_grid() -> Grid:
    """A 3 km-ish synthetic grid, deliberately not regular in lon.

    Rows step south; columns step east but with a shrinking degree step,
    mimicking a Lambert Conformal projection where degree spacing
    changes across the domain.
    """
    lat = 42.0 + np.arange(10)[:, None] * -0.027
    steps = np.cumsum(0.036 * np.linspace(1.0, 0.98, 12)) - (0.036 * 1.0)
    lon = -71.0 + steps[None, :]
    return Grid(np.broadcast_to(lat, (10, 12)), np.broadcast_to(lon, (10, 12)))


def make_grid_with(
    *,
    max_snap_factor: float = MAX_SNAP_FACTOR,
    batch_nodes: int = BATCH_NODES,
) -> Grid:
    """The standard test grid, built with explicit tolerances."""
    grid = make_grid()
    return Grid(
        grid._lat, grid._lon, max_snap_factor=max_snap_factor, batch_nodes=batch_nodes
    )


def converging_grid(shrink: float = 0.06) -> Grid:
    """A grid whose columns converge toward a row, as an LCC grid does.

    At 48N a degree of longitude is ~0.67 of a degree of latitude in
    ground distance, and the convergence makes the longitude margin in
    :meth:`Grid._window` the binding constraint.
    """
    n = 20
    lat = np.array([[48.0 - y * 0.027 for _ in range(n)] for y in range(n)])
    step = (0.027 / np.cos(np.radians(48.0))) * (
        1.0 - shrink * np.abs(np.arange(n) - n // 2)
    )
    lon = np.array(
        [[-90.0 + (x - n / 2) * step[y] for x in range(n)] for y in range(n)]
    )
    return Grid(lat, lon)


def brute_force(grid: Grid, lat: float, lon: float) -> tuple[tuple[int, int], float]:
    """Nearest node and distance by scanning every node, with no window."""
    best_key, best_distance = (0, 0), float("inf")
    for y in range(grid.shape[0]):
        for x in range(grid.shape[1]):
            distance = float(
                great_circle_km(
                    lat, lon, float(grid._lat[y, x]), float(grid._lon[y, x])
                )
            )
            if distance < best_distance:
                best_key, best_distance = (y, x), distance
    return best_key, best_distance


def fake_dataset(lat: np.ndarray, lon: np.ndarray) -> xr.Dataset:
    """Wrap 2-D lat/lon arrays in a dataset like the HRRR archive."""
    return xr.Dataset(
        {"precipitation_surface": (("time", "y", "x"), np.zeros((1, 10, 12)))},
        coords={
            "time": pd.to_datetime(["2025-07-15T17"]),
            "y": np.arange(10),
            "x": np.arange(12),
            "latitude": (("y", "x"), lat),
            "longitude": (("y", "x"), lon),
        },
    )


def a_place(lat: float, lon: float, name: str = "Test Course") -> Place:
    return Place(
        osm_type="way", osm_id=1, name=name, kind="golf_course", lat=lat, lon=lon
    )


def test_bounds_are_ordered() -> None:
    """Guards against swapping lat/lon when reporting grid extent."""
    b = make_grid().bounds()
    assert b.south < b.north
    assert b.west < b.east
    assert b.south == pytest.approx(41.757, abs=1e-3)
    assert b.west == pytest.approx(-71.0, abs=1e-6)
    assert b.north == pytest.approx(42.0, abs=1e-6)
    assert b.east < -70.0


def test_spacing_is_about_three_kilometres() -> None:
    assert make_grid().spacing_km == pytest.approx(3.0, abs=0.2)


def test_locate_exact_node_snaps_to_itself() -> None:
    grid = make_grid()
    cell = grid.locate(42.0, -71.0)
    assert (cell.y_index, cell.x_index) == (0, 0)
    assert cell.distance_km == pytest.approx(0.0, abs=1e-6)


def test_locate_midpoint_picks_the_nearer_node() -> None:
    """A point between two nodes belongs to the closer one."""
    grid = make_grid()
    target_lat = 42.0
    # Shift most of the way toward the node at (0, 1).
    lon = grid._lon[0, 1]
    cell = grid.locate(target_lat, float(lon) - 0.001)
    assert cell.x_index == 1


def test_locate_places_returns_one_cell_each_in_order() -> None:
    grid = make_grid()
    near = a_place(42.0, -71.0, "Near")
    far = a_place(41.78, -70.63, "Far")
    cells = grid.locate_places([near, far])
    assert len(cells) == 2
    assert (cells[0].y_index, cells[0].x_index) == (0, 0)
    assert cells[1].y_index > cells[0].y_index


def test_locate_places_empty_input() -> None:
    assert make_grid().locate_places([]) == []


def test_distance_is_under_half_a_cell_diagonal() -> None:
    """Any point inside the grid sits within half a cell diagonal."""
    grid = make_grid()
    # Halfway between two nodes is the worst case for a real location.
    mid_lat = (grid._lat[0, 0] + grid._lat[1, 0]) / 2
    mid_lon = (grid._lon[0, 0] + grid._lon[0, 1]) / 2
    cell = grid.locate(float(mid_lat), float(mid_lon))
    assert cell.distance_km < grid.spacing_km * 2**0.5 / 2


def test_colocated_places_share_a_cell() -> None:
    """Two places in one cell get the same node: the resolution limit.

    This is the behaviour Phase 1 reports on -- a 3 km grid cannot
    separate courses that share a cell.
    """
    grid = make_grid()
    lon = float(grid._lon[0, 0])
    a = grid.locate_places(
        [a_place(42.0, lon, "Essex County Club"), a_place(42.002, lon, "Rockport GC")]
    )
    assert a[0].key == a[1].key


def test_out_of_domain_place_is_rejected() -> None:
    """A far-away place must raise, not silently snap to an edge node."""
    grid = make_grid()
    with pytest.raises(PlaceOffGridError, match="outside the analysis domain"):
        grid.locate_places([a_place(30.0, -90.0, "Somewhere Else")])


def test_far_distance_is_reported_in_the_error() -> None:
    grid = make_grid()
    with pytest.raises(PlaceOffGridError, match="km from the nearest grid node"):
        grid.locate_places([a_place(42.0, -50.0, "Far East")])


def test_from_dataset_round_trips() -> None:
    grid = make_grid()
    ds = fake_dataset(grid._lat, grid._lon)
    rebuilt = Grid.from_dataset(ds)
    assert rebuilt.shape == grid.shape
    assert rebuilt.locate(42.0, -71.0).key == grid.locate(42.0, -71.0).key


def test_from_dataset_requires_lat_lon() -> None:
    ds = xr.Dataset({"t": ("x", np.zeros(3))}, coords={"x": np.arange(3)})
    with pytest.raises(KeyError, match="latitude/longitude"):
        Grid.from_dataset(ds)


def test_mismatched_shapes_rejected() -> None:
    with pytest.raises(ValueError, match="same shape"):
        Grid(np.zeros((3, 3)), np.zeros((3, 4)))


def test_one_dimensional_coords_rejected() -> None:
    with pytest.raises(ValueError, match="2-D"):
        Grid(np.zeros(5), np.zeros(5))


def test_nan_coordinates_are_never_selected() -> None:
    """A NaN node must not win the argmin."""
    lat = np.array([[42.0, np.nan], [41.97, 41.94]])
    lon = np.array([[-71.0, -70.96], [-71.04, -70.92]])
    grid = Grid(lat, lon)
    cell = grid.locate(41.99, -70.99)
    assert np.isfinite(cell.distance_km)
    assert (cell.y_index, cell.x_index) == (0, 0)


def test_cell_key_groups_by_node() -> None:
    grid = make_grid()
    cells = grid.locate_places([a_place(42.0, -71.0, "A"), a_place(41.78, -70.6, "B")])
    assert cells[0].key == (cells[0].y_index, cells[0].x_index)
    assert cells[0].key != cells[1].key


def test_batched_search_matches_unbatched() -> None:
    """Batching to bound memory must not change the answer."""
    base = make_grid()
    rng = np.random.default_rng(0)
    places = [
        a_place(
            float(41.76 + rng.random() * 0.24),
            float(-70.99 + rng.random() * 0.38),
            f"P{i}",
        )
        for i in range(40)
    ]
    unbatched = make_grid_with(batch_nodes=10**9).locate_places(places)
    batched = make_grid_with(batch_nodes=1).locate_places(places)
    assert [c.key for c in unbatched] == [c.key for c in batched]
    assert base.shape == (10, 12)


def test_batch_nodes_must_be_positive() -> None:
    lat = np.array([[42.0, 42.0], [41.973, 41.973]])
    lon = np.array([[-71.0, -70.96], [-71.0, -70.96]])
    with pytest.raises(ValueError, match="batch_nodes"):
        Grid(lat, lon, batch_nodes=0)


def test_max_snap_factor_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_snap_factor"):
        make_grid_with(max_snap_factor=0.0)


def test_max_snap_factor_controls_the_tolerance() -> None:
    """A looser factor accepts points the default would reject."""
    grid = make_grid_with()
    loose = make_grid_with(max_snap_factor=3.0)
    # ~11 km north of the top row: past the default limit of
    # 0.75 * sqrt(2) cells, but inside a factor of 3.
    lat = float(grid._lat[0, 0]) + 0.10
    lon = float(grid._lon[0, 0])
    with pytest.raises(PlaceOffGridError):
        grid.locate(lat, lon)
    cell = loose.locate(lat, lon)
    assert grid.max_snap_km < cell.distance_km < loose.max_snap_km


def test_windowed_search_agrees_with_brute_force() -> None:
    """The search window must never clip a node the point could accept.

    Checked against an exhaustive full-grid scan on a converging
    LCC-like grid, where a degree of longitude covers less ground than a
    degree of latitude and the window margin is easiest to get wrong.
    """
    grid = converging_grid()
    rng = np.random.default_rng(11)
    checked = 0
    for _ in range(150):
        y = int(rng.integers(1, 19))
        x = int(rng.integers(1, 19))
        step = float(grid._lon[y, x + 1] - grid._lon[y, x])
        lat = float(grid._lat[y, 0]) - rng.uniform(0.05, 0.95) * 0.027
        lon = float(grid._lon[y, x]) + rng.uniform(-0.45, 0.45) * step
        expected, distance = brute_force(grid, lat, lon)
        if distance > grid.max_snap_km:
            continue  # genuinely off-grid; raising is the correct answer
        checked += 1
        assert grid.locate(lat, lon).key == expected
    assert checked > 100  # the sample really did exercise the search


def test_cell_types_share_the_grid_node_identity() -> None:
    """GridCell and FootprintCell differ only in their extra field."""
    assert issubclass(GridCell, GridNode)
    assert issubclass(FootprintCell, GridNode)
    node_fields = set(GridNode.model_fields)
    assert set(GridCell.model_fields) - node_fields == {"distance_km"}
    assert set(FootprintCell.model_fields) - node_fields == {"weight"}
    # Both are frozen, and both expose the same grouping key.
    assert GridCell.model_config["frozen"] is True
    assert FootprintCell.model_config["frozen"] is True


def test_cover_cells_returns_nodes_inside_polygon() -> None:
    grid = make_grid()
    cells = grid.cover_cells(wkb(box_wkt(grid, [0, 0, 1, 1], [0, 1, 0, 1])))
    assert [c.key for c in cells] == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_cover_cells_weights_are_uniform_and_sum_to_one() -> None:
    grid = make_grid()
    cells = grid.cover_cells(wkb(box_wkt(grid, [0, 0, 1, 1], [0, 1, 0, 1])))
    assert len(cells) == 4
    assert [c.weight for c in cells] == pytest.approx([0.25, 0.25, 0.25, 0.25])
    assert sum(c.weight for c in cells) == pytest.approx(1.0)


def test_cover_cells_single_node_weights_one() -> None:
    grid = make_grid()
    cells = grid.cover_cells(wkb(box_wkt(grid, [0], [0])))
    assert [c.key for c in cells] == [(0, 0)]
    assert cells[0].weight == pytest.approx(1.0)


def test_cover_cells_empty_when_no_center_inside() -> None:
    """A tiny polygon between nodes covers none: the point-sampling fallback."""
    grid = make_grid()
    mid_lat = (float(grid._lat[0, 0]) + float(grid._lat[1, 0])) / 2
    mid_lon = (float(grid._lon[0, 0]) + float(grid._lon[0, 1])) / 2
    d = 0.001
    wkt = (
        f"POLYGON(({mid_lon - d} {mid_lat + d}, {mid_lon + d} {mid_lat + d}, "
        f"{mid_lon + d} {mid_lat - d}, {mid_lon - d} {mid_lat - d}, "
        f"{mid_lon - d} {mid_lat + d}))"
    )
    assert grid.cover_cells(wkb(wkt)) == []


def test_cover_cells_handles_multipolygon() -> None:
    grid = make_grid()
    a = box_wkt(grid, [0], [0])[8:-1]  # strip "POLYGON(" and trailing ")"
    b = box_wkt(grid, [3], [4])[8:-1]
    cells = grid.cover_cells(wkb(f"MULTIPOLYGON(({a}), ({b}))"))
    assert [c.key for c in cells] == [(0, 0), (3, 4)]


def test_cover_cells_point_coincident_with_node() -> None:
    grid = make_grid()
    lon, lat = float(grid._lon[0, 0]), float(grid._lat[0, 0])
    cells = grid.cover_cells(wkb(f"POINT({lon} {lat})"))
    assert [c.key for c in cells] == [(0, 0)]


def test_cover_cells_point_off_node_is_empty() -> None:
    """A point between nodes matches no center: fall back to ``locate``."""
    grid = make_grid()
    mid_lat = (float(grid._lat[0, 0]) + float(grid._lat[1, 0])) / 2
    mid_lon = (float(grid._lon[0, 0]) + float(grid._lon[0, 1])) / 2
    assert grid.cover_cells(wkb(f"POINT({mid_lon} {mid_lat})")) == []


def test_cover_cells_ignores_nan_nodes() -> None:
    lat = np.array([[42.0, 42.0], [41.97, 41.97]])
    lon = np.array([[-71.0, -70.96], [-71.0, np.nan]])
    grid = Grid(lat, lon)
    # Box over both (0, 1) and (1, 1); (1, 1) has a NaN lon and is skipped.
    wkt = (
        "POLYGON((-70.98 42.02, -70.94 42.02, -70.94 41.95, "
        "-70.98 41.95, -70.98 42.02))"
    )
    assert [c.key for c in grid.cover_cells(wkb(wkt))] == [(0, 1)]

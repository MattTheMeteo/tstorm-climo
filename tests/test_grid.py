"""Tests for Grid, using a synthetic lat/lon grid instead of the network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from tstorm_climo.grid import Grid, PlaceOffGridError
from tstorm_climo.places import Place


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
    cells = grid.locate_places(
        [a_place(42.0, -71.0, "A"), a_place(41.78, -70.6, "B")]
    )
    assert cells[0].key == (cells[0].y_index, cells[0].x_index)
    assert cells[0].key != cells[1].key


def test_batched_search_matches_unbatched() -> None:
    """Batching to bound memory must not change the answer."""
    grid = make_grid()
    rng = np.random.default_rng(0)
    places = [
        a_place(
            float(41.76 + rng.random() * 0.24),
            float(-70.99 + rng.random() * 0.38),
            f"P{i}",
        )
        for i in range(40)
    ]
    import tstorm_climo.grid as grid_module

    original = grid_module._BATCH_NODES
    try:
        grid_module._BATCH_NODES = 10**9
        unbatched = grid.locate_places(places)
        grid_module._BATCH_NODES = 1
        batched = grid.locate_places(places)
    finally:
        grid_module._BATCH_NODES = original
    assert [c.key for c in unbatched] == [c.key for c in batched]
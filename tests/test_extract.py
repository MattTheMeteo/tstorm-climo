"""Tests for point extraction, using a synthetic dataset, not the network."""

from __future__ import annotations

from datetime import UTC
from typing import cast

import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr

from tstorm_climo.extract import (
    FOOTPRINT_SAMPLING,
    N_CELLS_COLUMN,
    POINT_SAMPLING,
    RATE_COLUMN,
    SAMPLING_COLUMN,
    SAMPLING_KEY_COLUMN,
    SCHEMA,
    TIME_COLUMN,
    VALID_COLUMN,
    SamplingCell,
    extract_series,
    sampling_cells,
    sampling_frame,
    sampling_key,
)
from tstorm_climo.grid import Grid, PlaceOffGridError
from tstorm_climo.places import Place

N_Y, N_X = 6, 8


def make_coords() -> tuple[np.ndarray, np.ndarray]:
    """A 3 km-ish lat/lon block, shrinking in longitude eastward."""
    lat = 42.0 + np.arange(N_Y)[:, None] * -0.027
    steps = np.cumsum(0.036 * np.linspace(1.0, 0.98, N_X)) - 0.036
    lon = -71.0 + steps[None, :]
    return (
        np.broadcast_to(lat, (N_Y, N_X)).copy(),
        np.broadcast_to(lon, (N_Y, N_X)).copy(),
    )


def make_opener(rate: np.ndarray, times: list[str]):
    """An opener stub returning a dataset with known rates."""
    lat, lon = make_coords()

    def opener(dataset_id, **kwargs):
        assert dataset_id == "noaa-hrrr-analysis"
        assert kwargs.get("chunks") is None
        return xr.Dataset(
            {"precipitation_surface": (("time", "y", "x"), rate)},
            coords={
                "time": pd.to_datetime(times),
                "y": np.arange(N_Y),
                "x": np.arange(N_X),
                "latitude": (("y", "x"), lat),
                "longitude": (("y", "x"), lon),
            },
        )

    return opener


def a_place(lat: float, lon: float, name: str = "Course", osm_id: int = 1) -> Place:
    return Place(
        osm_type="way",
        osm_id=osm_id,
        name=name,
        kind="golf_course",
        lat=lat,
        lon=lon,
    )


def one_place() -> Place:
    lat, lon = make_coords()
    return a_place(float(lat[0, 0]), float(lon[0, 0]))


def flat_opener(value: float, n_times: int = 3):
    rate = np.full((n_times, N_Y, N_X), value)
    times = [f"2025-07-15T{h:02d}" for h in range(n_times)]
    return make_opener(rate, times)


def test_empty_places_returns_empty_frame_with_schema() -> None:
    out = extract_series([], "2025-07-01", "2025-08-01", opener=flat_opener(0.0))
    assert out.height == 0
    assert out.columns == list(SCHEMA)
    assert out.schema == SCHEMA


def test_duplicate_place_keys_are_rejected_before_opening_data() -> None:
    place = one_place()
    with pytest.raises(ValueError, match="unique place_key"):
        extract_series(
            [place, place],
            "2025-07-01",
            "2025-08-01",
            opener=lambda *_args, **_kwargs: pytest.fail("should not open data"),
        )


def test_long_format_is_one_row_per_place_hour() -> None:
    out = extract_series(
        [one_place()], "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 3)
    )
    assert out.height == 3
    assert out["place_key"].unique().to_list() == ["osm:way:1"]
    assert out[TIME_COLUMN].n_unique() == 3


def test_multiple_places_each_get_every_hour() -> None:
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A", osm_id=1),
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B", osm_id=2),
    ]
    out = extract_series(places, "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 4))
    assert out.height == 8
    assert sorted(out["place_key"].unique().to_list()) == [
        "osm:way:1",
        "osm:way:2",
    ]
    assert out.group_by("place_key").len()["len"].to_list() == [4, 4]


def test_rate_is_converted_to_mm_per_hour() -> None:
    """kg m-2 s-1 -> mm/h is a factor of 3600."""
    out = extract_series(
        [one_place()], "2025-07-01", "2025-08-01", opener=flat_opener(1e-5, 2)
    )
    assert out[RATE_COLUMN].to_list() == pytest.approx([0.036, 0.036])


def test_values_are_read_from_the_right_grid_node() -> None:
    """A distinctive rate per node proves the gather maps correctly."""
    rate = np.zeros((1, N_Y, N_X))
    rate[0, 2, 3] = 1e-5
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A", osm_id=1),
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B", osm_id=2),
    ]
    out = extract_series(
        places, "2025-07-01", "2025-08-01", opener=make_opener(rate, ["2025-07-15T00"])
    )
    by_id = out.group_by("place_key").agg(pl.col(RATE_COLUMN).first())
    rates = dict(
        zip(
            by_id["place_key"].to_list(),
            by_id[RATE_COLUMN].to_list(),
            strict=True,
        )
    )
    assert rates["osm:way:1"] == pytest.approx(0.0)
    assert rates["osm:way:2"] == pytest.approx(0.036)
    assert set(rates) == {"osm:way:1", "osm:way:2"}


def test_nan_is_flagged_invalid_not_treated_as_dry() -> None:
    """Missing data must be distinguishable from zero rain."""
    rate = np.full((2, N_Y, N_X), 0.0)
    rate[1, 0, 0] = np.nan
    out = extract_series(
        [one_place()],
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(rate, ["2025-07-15T00", "2025-07-15T01"]),
    )
    assert out[VALID_COLUMN].to_list() == [True, False]
    assert out[RATE_COLUMN][1] != out[RATE_COLUMN][1]  # NaN, not 0.0
    # Crucially, the invalid row is *not* zero.
    assert out[RATE_COLUMN][0] == 0.0


def test_grid_node_columns_accompany_each_row() -> None:
    lat, lon = make_coords()
    out = extract_series(
        [one_place()], "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 2)
    )
    assert out["y_index"].to_list() == [0, 0]
    assert out["x_index"].to_list() == [0, 0]
    assert cast(float, out["snap_distance_km"].max()) < 0.01
    assert out["grid_lat"].to_list() == pytest.approx([lat[0, 0]] * 2)


def test_injected_grid_is_used() -> None:
    """Passing a Grid avoids relying on the dataset's own coords."""
    lat, lon = make_coords()
    grid = Grid(lat, lon)
    out = extract_series(
        [one_place()],
        "2025-07-01",
        "2025-08-01",
        grid=grid,
        opener=flat_opener(0.0, 1),
    )
    assert out.height == 1


def test_pre_2018_range_is_clamped_away() -> None:
    """A wholly pre-2018 request yields no rows, not pre-2018 data."""
    times = ["2017-06-01T00", "2017-06-01T01"]
    rate = np.full((2, N_Y, N_X), 1e-5)
    out = extract_series(
        [one_place()],
        "2015-01-01",
        "2017-12-31",
        opener=make_opener(rate, times),
    )
    assert out.height == 0
    assert out.columns == list(SCHEMA)
    assert out.schema == SCHEMA


def test_pre_2018_rows_are_dropped_but_later_ones_kept() -> None:
    times = ["2017-06-01T00", "2018-01-01T00", "2025-07-15T00"]
    rate = np.full((3, N_Y, N_X), 1e-5)
    out = extract_series(
        [one_place()],
        "2014-10-01",
        "2025-08-01",
        opener=make_opener(rate, times),
    )
    assert out[TIME_COLUMN].to_list() == [
        pd.Timestamp("2018-01-01", tz="UTC"),
        pd.Timestamp("2025-07-15", tz="UTC"),
    ]


def test_off_grid_place_raises() -> None:
    with pytest.raises(PlaceOffGridError):
        extract_series(
            [a_place(30.0, -90.0, "Nowhere")],
            "2025-07-01",
            "2025-08-01",
            opener=flat_opener(0.0),
        )


def test_output_is_sorted_by_place_then_time() -> None:
    lat, lon = make_coords()
    places = [
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B", osm_id=2),
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A", osm_id=1),
    ]
    times = [f"2025-07-15T{h:02d}" for h in range(3)]
    out = extract_series(
        places,
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(np.zeros((3, N_Y, N_X)), times),
    )
    assert out["place_key"].to_list() == [
        "osm:way:2",
        "osm:way:2",
        "osm:way:2",
        "osm:way:1",
        "osm:way:1",
        "osm:way:1",
    ]
    # Long format: sorted by place, then by time *within* each place.
    # The time column itself resets per place, so it is not globally
    # sorted and must not be assumed to be.
    for place_key in ("osm:way:1", "osm:way:2"):
        time_col = out.filter(pl.col("place_key") == place_key)[TIME_COLUMN]
        assert time_col.is_sorted()


def test_colocated_places_get_identical_series() -> None:
    """The documented resolution limit: same cell, same numbers."""
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A", osm_id=1),
        a_place(float(lat[0, 0]) + 0.001, float(lon[0, 0]), "B", osm_id=2),
    ]
    times = ["2025-07-15T00", "2025-07-15T01"]
    rate = np.full((2, N_Y, N_X), 2e-5)
    out = extract_series(
        places, "2025-07-01", "2025-08-01", opener=make_opener(rate, times)
    )
    by_key = out.group_by("place_key").agg(pl.col(RATE_COLUMN))
    series = dict(
        zip(by_key["place_key"].to_list(), by_key[RATE_COLUMN].to_list(), strict=True)
    )
    assert series["osm:way:1"] == series["osm:way:2"]
    assert out["y_index"].n_unique() == 1
    assert out["x_index"].n_unique() == 1


def test_timezone_is_carried_through() -> None:
    """Each row carries the place's zone for the summary layer."""
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A", osm_id=1),
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B", osm_id=2),
    ]
    places[0] = places[0].model_copy(update={"timezone": "America/New_York"})
    places[1] = places[1].model_copy(update={"timezone": "America/Denver"})
    out = extract_series(places, "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 2))
    zones = out.group_by("place_key").agg(pl.col("tz_name").first())
    # Keyed lookup, not positional: group_by row order is not guaranteed.
    assert dict(
        zip(zones["place_key"].to_list(), zones["tz_name"].to_list(), strict=True)
    ) == {"osm:way:1": "America/New_York", "osm:way:2": "America/Denver"}


def test_mixed_timezones_can_be_partitioned_and_converted() -> None:
    """convert_time_zone takes a string, so mixed zones need a group-by.

    This is the pattern the summary layer must use; a fixed offset
    would be wrong for half the year.
    """
    from datetime import datetime

    frame = pl.DataFrame(
        {
            "time": [
                datetime(2025, 1, 15, 17, tzinfo=UTC),
                datetime(2025, 7, 15, 17, tzinfo=UTC),
            ],
            "tz_name": ["America/New_York"] * 2,
        }
    )
    converted = pl.concat(
        part.with_columns(local=pl.col("time").dt.convert_time_zone(zone))
        for (zone,), part in frame.partition_by("tz_name", as_dict=True).items()
    )
    assert sorted(converted["local"].dt.hour().to_list()) == [12, 13]


def test_unit_conversion_happens_after_slicing(monkeypatch) -> None:
    """Slicing must precede the mm/h conversion.

    The archive read is lazy, so converting the full variable before
    slicing forces materialization of the entire array — 748 GiB when
    pointed at the live archive. Synthetic datasets are already in
    memory, so this cannot be caught by asserting on values; instead we
    record the shape the converter is handed and require it to be the
    requested slice, not the whole variable.
    """
    import tstorm_climo.extract as extract_module

    lat, lon = make_coords()
    # Deliberately many more steps in the source than are requested.
    n_source = 50
    times = [
        (pd.Timestamp("2025-07-14T00:00") + pd.Timedelta(hours=h)).isoformat()
        for h in range(n_source)
    ]
    seen: list[tuple[int, ...]] = []
    real = extract_module.to_mm_per_hour

    def recording(da):
        seen.append(tuple(da.shape))
        return real(da)

    monkeypatch.setattr(extract_module, "to_mm_per_hour", recording)
    rate = np.zeros((n_source, N_Y, N_X), dtype=np.float32)
    out = extract_series(
        [one_place()],
        "2025-07-15T00",
        "2025-07-15T03",
        opener=make_opener(rate, times),
    )
    assert out.height == 4
    assert seen, "converter was never called"
    # Converter must see only the requested hours, never all 50.
    assert all(shape[0] <= 4 for shape in seen), seen
    assert out.height <= 4


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


def box_wkt(
    lat: np.ndarray,
    lon: np.ndarray,
    ys: list[int],
    xs: list[int],
    margin: float = 0.005,
) -> str:
    """A WKT polygon box around the given nodes, with a small margin."""
    lats = [float(lat[y, x]) for y, x in zip(ys, xs, strict=True)]
    lons = [float(lon[y, x]) for y, x in zip(ys, xs, strict=True)]
    south, north = min(lats) - margin, max(lats) + margin
    west, east = min(lons) - margin, max(lons) + margin
    return (
        f"POLYGON(({west} {north}, {east} {north}, {east} {south}, "
        f"{west} {south}, {west} {north}))"
    )


def test_footprint_sampling_averages_covered_nodes() -> None:
    """A polygon covering two nodes yields their equal-weighted mean."""
    lat, lon = make_coords()
    place = a_place(float(lat[2, 3]), float(lon[2, 3]), "Footprint", osm_id=1)
    place = place.model_copy(
        update={"geometry_wkb": wkb(box_wkt(lat, lon, [2, 2], [3, 4]))}
    )
    rate = np.zeros((1, N_Y, N_X))
    rate[0, 2, 3] = 1e-5  # 0.036 mm/h
    rate[0, 2, 4] = 3e-5  # 0.108 mm/h
    out = extract_series(
        [place],
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(rate, ["2025-07-15T00"]),
    )
    assert out[RATE_COLUMN].to_list() == pytest.approx([(0.036 + 0.108) / 2])


def test_footprint_sampling_falls_back_when_polygon_covers_no_node() -> None:
    """A polygon between nodes falls back to the nearest-node sample."""
    lat, lon = make_coords()
    mid_lat = (float(lat[2, 3]) + float(lat[3, 3])) / 2
    mid_lon = (float(lon[2, 3]) + float(lon[2, 4])) / 2
    d = 0.001
    wkt = (
        f"POLYGON(({mid_lon - d} {mid_lat + d}, {mid_lon + d} {mid_lat + d}, "
        f"{mid_lon + d} {mid_lat - d}, {mid_lon - d} {mid_lat - d}, "
        f"{mid_lon - d} {mid_lat + d}))"
    )
    place = a_place(float(lat[2, 3]), float(lon[2, 3]), "Tiny", osm_id=1)
    place = place.model_copy(update={"geometry_wkb": wkb(wkt)})
    rate = np.zeros((1, N_Y, N_X))
    rate[0, 2, 3] = 1e-5
    out = extract_series(
        [place],
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(rate, ["2025-07-15T00"]),
    )
    assert out[RATE_COLUMN].to_list() == pytest.approx([0.036])
    assert out["y_index"].to_list() == [2]
    assert out["x_index"].to_list() == [3]


def _cell(y: int, x: int, weight: float = 0.5) -> SamplingCell:
    return SamplingCell(
        y_index=y,
        x_index=x,
        grid_lat=42.0,
        grid_lon=-71.0,
        weight=weight,
        sampling=FOOTPRINT_SAMPLING,
    )


def test_sampling_key_is_order_independent() -> None:
    """Two places covering the same nodes must key identically."""
    assert sampling_key([_cell(2, 3), _cell(2, 4)]) == sampling_key(
        [_cell(2, 4), _cell(2, 3)]
    )


def test_sampling_key_distinguishes_different_sets() -> None:
    assert sampling_key([_cell(2, 3)]) != sampling_key([_cell(2, 4)])
    assert sampling_key([_cell(2, 3), _cell(2, 4)]) != sampling_key(
        [_cell(2, 3), _cell(2, 5)]
    )


def test_sampling_cells_keeps_anchor_and_footprint_apart() -> None:
    """The anchor is the nearest node; cells are what gets averaged."""
    lat, lon = make_coords()
    grid = Grid(lat, lon)
    poly = wkb(box_wkt(lat, lon, [2, 2], [3, 4]))
    footprint = a_place(float(lat[2, 3]), float(lon[2, 3]), "Footprint", osm_id=1)
    footprint = footprint.model_copy(update={"geometry_wkb": poly})
    point = a_place(float(lat[2, 3]), float(lon[2, 3]), "Point", osm_id=2)

    entries = sampling_cells(grid, [footprint, point])

    assert entries[0].sampling == FOOTPRINT_SAMPLING
    assert entries[0].n_cells == 2
    assert entries[0].anchor.key == (2, 3)
    assert {c.key for c in entries[0].cells} == {(2, 3), (2, 4)}

    assert entries[1].sampling == POINT_SAMPLING
    assert entries[1].n_cells == 1
    assert entries[1].cells[0].key == entries[1].anchor.key


def test_sampling_frame_records_every_node() -> None:
    lat, lon = make_coords()
    grid = Grid(lat, lon)
    poly = wkb(box_wkt(lat, lon, [2, 2], [3, 4]))
    footprint = a_place(float(lat[2, 3]), float(lon[2, 3]), "Footprint", osm_id=1)
    footprint = footprint.model_copy(update={"geometry_wkb": poly})

    frame = sampling_frame([footprint], sampling_cells(grid, [footprint]))

    assert frame.height == 2
    assert frame["sampling"].unique().to_list() == [FOOTPRINT_SAMPLING]
    assert frame["n_cells"].unique().to_list() == [2]
    assert frame["sampling_key"].n_unique() == 1
    assert frame["weight"].sum() == pytest.approx(1.0)
    assert set(frame["place_key"].to_list()) == {footprint.place_key}


def test_series_carries_sampling_columns_for_footprints() -> None:
    """A multi-node place is self-describing on every hourly row."""
    lat, lon = make_coords()
    poly = wkb(box_wkt(lat, lon, [2, 2], [3, 4]))
    place = a_place(float(lat[2, 3]), float(lon[2, 3]), "Footprint", osm_id=1)
    place = place.model_copy(update={"geometry_wkb": poly})
    rate = np.zeros((1, N_Y, N_X))
    out = extract_series(
        [place],
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(rate, ["2025-07-15T00"]),
    )
    assert out[SAMPLING_COLUMN].unique().to_list() == [FOOTPRINT_SAMPLING]
    assert out[N_CELLS_COLUMN].to_list() == [2]
    assert out[SAMPLING_KEY_COLUMN].n_unique() == 1


def test_anchor_columns_survive_alongside_footprint_columns() -> None:
    """The anchor keeps its old meaning; n_cells reveals the difference."""
    lat, lon = make_coords()
    poly = wkb(box_wkt(lat, lon, [2, 2], [3, 4]))
    place = a_place(float(lat[2, 3]), float(lon[2, 3]), "Footprint", osm_id=1)
    place = place.model_copy(update={"geometry_wkb": poly})
    rate = np.zeros((1, N_Y, N_X))
    out = extract_series(
        [place],
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(rate, ["2025-07-15T00"]),
    )
    # The place sits exactly on node (2,3), so the anchor is exact...
    assert out["y_index"].to_list() == [2]
    assert out["x_index"].to_list() == [3]
    assert out["snap_distance_km"].to_list() == pytest.approx([0.0])
    # ...but the value averaged two nodes, which the old columns hid.
    assert out[N_CELLS_COLUMN].to_list() == [2]

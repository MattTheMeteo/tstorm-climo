"""Tests for point extraction, using a synthetic dataset, not the network."""

from __future__ import annotations

from datetime import UTC

import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr

from tstorm_climo.extract import (
    RATE_COLUMN,
    SCHEMA,
    TIME_COLUMN,
    VALID_COLUMN,
    extract_series,
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


def a_place(lat: float, lon: float, name: str = "Course") -> Place:
    return Place(
        osm_type="way", osm_id=1, name=name, kind="golf_course", lat=lat, lon=lon
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


def test_long_format_is_one_row_per_place_hour() -> None:
    out = extract_series(
        [one_place()], "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 3)
    )
    assert out.height == 3
    assert out["place_id"].n_unique() == 1
    assert out[TIME_COLUMN].n_unique() == 3


def test_multiple_places_each_get_every_hour() -> None:
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A"),
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B"),
    ]
    out = extract_series(
        places, "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 4)
    )
    assert out.height == 8
    assert sorted(out["place_id"].unique().to_list()) == [0, 1]
    assert out.group_by("place_id").len()["len"].to_list() == [4, 4]


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
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A"),
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B"),
    ]
    out = extract_series(
        places, "2025-07-01", "2025-08-01", opener=make_opener(rate, ["2025-07-15T00"])
    )
    by_id = out.group_by("place_id").agg(pl.col(RATE_COLUMN).first())
    rates = dict(
        zip(
            by_id["place_id"].to_list(),
            by_id[RATE_COLUMN].to_list(),
            strict=True,
        )
    )
    assert rates[0] == pytest.approx(0.0)
    assert rates[1] == pytest.approx(0.036)


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
    assert out["snap_distance_km"].max() < 0.01
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
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B"),
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A"),
    ]
    times = [f"2025-07-15T{h:02d}" for h in range(3)]
    out = extract_series(
        places,
        "2025-07-01",
        "2025-08-01",
        opener=make_opener(np.zeros((3, N_Y, N_X)), times),
    )
    assert out["place_id"].to_list() == [0, 0, 0, 1, 1, 1]
    # Long format: sorted by place, then by time *within* each place.
    # The time column itself resets per place, so it is not globally
    # sorted and must not be assumed to be.
    for pid in (0, 1):
        times = out.filter(pl.col("place_id") == pid)[TIME_COLUMN]
        assert times.is_sorted()


def test_colocated_places_get_identical_series() -> None:
    """The documented resolution limit: same cell, same numbers."""
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A"),
        a_place(float(lat[0, 0]) + 0.001, float(lon[0, 0]), "B"),
    ]
    times = ["2025-07-15T00", "2025-07-15T01"]
    rate = np.full((2, N_Y, N_X), 2e-5)
    out = extract_series(
        places, "2025-07-01", "2025-08-01", opener=make_opener(rate, times)
    )
    by_id = out.group_by("place_id").agg(pl.col(RATE_COLUMN))
    assert by_id[RATE_COLUMN][0].to_list() == by_id[RATE_COLUMN][1].to_list()
    assert out["y_index"].n_unique() == 1
    assert out["x_index"].n_unique() == 1


def test_timezone_is_carried_through() -> None:
    """Each row carries the place's zone for the summary layer."""
    lat, lon = make_coords()
    places = [
        a_place(float(lat[0, 0]), float(lon[0, 0]), "A"),
        a_place(float(lat[2, 3]), float(lon[2, 3]), "B"),
    ]
    places[0] = places[0].model_copy(
        update={"timezone": "America/New_York"}
    )
    places[1] = places[1].model_copy(
        update={"timezone": "America/Denver"}
    )
    out = extract_series(
        places, "2025-07-01", "2025-08-01", opener=flat_opener(0.0, 2)
    )
    zones = out.group_by("place_id").agg(pl.col("tz_name").first())
    assert zones["tz_name"].to_list() == ["America/New_York", "America/Denver"]


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
        for (zone,), part in frame.partition_by(
            "tz_name", as_dict=True
        ).items()
    )
    assert sorted(converted["local"].dt.hour().to_list()) == [12, 13]

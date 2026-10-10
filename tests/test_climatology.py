"""Tests for climatology summaries using synthetic data, not the network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from tstorm_climo.climatology import summarize_grid
from tstorm_climo.hrrr import (
    PRECIPITATION_MM_PER_HOUR,
    load_precipitation,
)
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR


def fake_precipitation(rate: np.ndarray, times: list[str]) -> xr.Dataset:
    """Build a synthetic dataset mimicking the dynamical.org HRRR archive."""
    return xr.Dataset(
        {
            "precipitation_surface": (
                ("time", "y", "x"),
                rate,
                {"units": "kg m-2 s-1"},
            )
        },
        coords={"time": pd.to_datetime(times)},
    )


def make_opener(ds: xr.Dataset):
    """Build an opener stub that returns the full synthetic dataset."""

    def opener(dataset_id, **kwargs):
        assert dataset_id == "noaa-hrrr-analysis"
        assert kwargs.get("chunks") is None
        return ds

    return opener


def test_summarize_counts_wet_grid_points() -> None:
    times = ["2025-07-15T17", "2025-07-15T18"]
    rate = np.array(
        [
            [[0.0, 1e-4], [0.0, 0.0]],  # 1 wet point at 17Z
            [[0.5, 0.0], [0.1, 0.2]],  # 3 wet points at 18Z
        ]
    )
    ds = fake_precipitation(rate, times)
    precipitation = load_precipitation(
        "2025-07-15T17", "2025-07-15T18", opener=make_opener(ds)
    )
    events = summarize_grid(precipitation)
    assert events == {
        "2025-07-15T17:00:00": 1,
        "2025-07-15T18:00:00": 3,
    }


def test_load_precipitation_slices_time() -> None:
    times = ["2025-07-15T17", "2025-07-15T18", "2025-07-15T19"]
    rate = np.zeros((3, 2, 2))
    ds = fake_precipitation(rate, times)
    sliced = load_precipitation(
        "2025-07-15T18", "2025-07-15T18", opener=make_opener(ds)
    )
    assert sliced["time"].values == pd.to_datetime(["2025-07-15T18"])


def test_load_precipitation_drops_pre_2018_data() -> None:
    """Pre-2018 data is sparse; it must not reach the climatology.

    Missing data arrives as NaN, which fails "precipitation > threshold"
    and so scores as dry. Clamping at MIN_VALID_DATE avoids that.
    """
    times = ["2017-06-01T12", "2018-01-01T00", "2025-07-15T18"]
    ds = fake_precipitation(np.zeros((3, 2, 2)), times)
    sliced = load_precipitation(
        "2014-10-01", "2025-07-15T18", opener=make_opener(ds)
    )
    assert list(sliced["time"].values) == list(
        pd.to_datetime(["2018-01-01T00", "2025-07-15T18"])
    )


def test_load_precipitation_is_empty_for_entirely_pre_2018_range() -> None:
    times = ["2017-06-01T12", "2017-06-01T13", "2017-06-01T14"]
    ds = fake_precipitation(np.zeros((3, 2, 2)), times)
    sliced = load_precipitation(
        "2015-01-01", "2017-12-31", opener=make_opener(ds)
    )
    assert sliced.sizes["time"] == 0


def test_default_threshold_is_in_mm_per_hour() -> None:
    """0.05 mm/h must be converted to kg m-2 s-1 before comparing.

    Guards the unit trap: 0.05 mm/h is 1.39e-5 kg m-2 s-1, not 0.05.
    A cell at exactly 0.05 kg m-2 s-1 (i.e. 180 mm/h) must still count.
    """
    assert DEFAULT_WET_THRESHOLD_MM_PER_HOUR == 0.05
    times = ["2025-07-15T17"]
    # 0.05 kg m-2 s-1 = 180 mm/h: well above 0.05 mm/h, so wet.
    # 1e-5 kg m-2 s-1 = 0.036 mm/h: below 0.05 mm/h, so dry.
    rate = np.array([[[0.05, 1e-5]]])
    events = summarize_grid(
        load_precipitation("2025-07-15T17", "2025-07-15T17",
                           opener=make_opener(fake_precipitation(rate, times)))
    )
    assert events == {"2025-07-15T17:00:00": 1}


def test_threshold_is_exclusive() -> None:
    """A cell exactly at the threshold is not counted as wet."""
    times = ["2025-07-15T17"]
    exactly = 0.05 / 3600.0  # kg m-2 s-1 == 0.05 mm/h
    rate = np.array([[[exactly, exactly]]])
    events = summarize_grid(
        load_precipitation("2025-07-15T17", "2025-07-15T17",
                           opener=make_opener(fake_precipitation(rate, times)))
    )
    assert events == {"2025-07-15T17:00:00": 0}


def test_custom_threshold_is_honored() -> None:
    times = ["2025-07-15T17"]
    # 1 mm/h == 2.78e-4 kg m-2 s-1
    rate = np.array([[[0.5e-3, 0.1e-3]]])  # 1.8 mm/h, 0.36 mm/h
    events = summarize_grid(
        load_precipitation("2025-07-15T17", "2025-07-15T17",
                           opener=make_opener(fake_precipitation(rate, times))),
        threshold_mm_per_hour=1.0,
    )
    assert events == {"2025-07-15T17:00:00": 1}


def test_load_precipitation_returns_mm_per_hour() -> None:
    """The unit conversion happens once, at read, in hrrr.to_mm_per_hour.

    The dataset must already be mm/h, so a downstream threshold needs no
    conversion of its own.
    """
    times = ["2025-07-15T17"]
    rate = np.array([[[1.0]]], dtype=np.float32)  # kg m-2 s-1 -> 3600 mm/h
    ds = fake_precipitation(rate, times)
    loaded = load_precipitation(
        "2025-07-15T17", "2025-07-15T17", opener=make_opener(ds)
    )
    assert loaded[PRECIPITATION_MM_PER_HOUR][0, 0, 0].item() == pytest.approx(
        3600.0
    )
    assert loaded[PRECIPITATION_MM_PER_HOUR].attrs["units"] == "mm h-1"

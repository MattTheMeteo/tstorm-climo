"""Tests for Climatology, using a synthetic dataset instead of the network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from tstorm_climo.climatology import Climatology
from tstorm_climo.hrrr import load_precipitation


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
    climo = Climatology()
    precipitation = load_precipitation(
        "2025-07-15T17", "2025-07-15T18", opener=make_opener(ds)
    )
    events = climo.summarize(precipitation)
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

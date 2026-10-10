"""Accumulation of precipitation events into a climatology."""

from __future__ import annotations

import numpy as np
import xarray as xr

from tstorm_climo.hrrr import PRECIPITATION_MM_PER_HOUR
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR


def summarize_grid(
    precipitation: xr.Dataset,
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> dict[str, int]:
    """Count wet grid points in an already-loaded precipitation dataset.

    A grid point "received rain" during a step when the hourly-average
    precipitation rate exceeds a threshold. Takes an already-loaded
    dataset: opening the archive is :mod:`tstorm_climo.hrrr`'s job, which
    keeps this function free of I/O.
    """
    wet = precipitation[PRECIPITATION_MM_PER_HOUR] > threshold_mm_per_hour
    counts = wet.sum(dim=("y", "x"))
    return {
        np.datetime_as_string(time, unit="s"): int(count)
        for time, count in zip(counts["time"].values, counts.values, strict=True)
    }

"""Accumulation of precipitation events into a climatology."""

from __future__ import annotations

import numpy as np
import xarray as xr

from tstorm_climo.hrrr import PRECIPITATION_MM_PER_HOUR

# Default wet threshold, in mm/h. Sits above the archive's numerical
# dust. Higher thresholds rank places far more reliably (see
# `summarize.split_half_reliability`).
DEFAULT_WET_THRESHOLD_MM_PER_HOUR = 0.05


class Climatology:
    """Accumulates precipitation events into a climatology.

    A grid point "received rain" during a step when the hourly-average
    precipitation rate exceeds a threshold. Takes an already-loaded
    dataset: opening the archive is :mod:`tstorm_climo.hrrr`'s job, which
    keeps this class free of I/O.
    """

    def summarize(
        self,
        precipitation: xr.Dataset,
        *,
        threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
    ) -> dict[str, int]:
        """Count wet grid points in an already-loaded precipitation dataset.

        Args:
            precipitation: Dataset containing a
                ``precipitation_mm_per_hour`` variable, such as the
                result of :func:`tstorm_climo.hrrr.load_precipitation`.
            threshold_mm_per_hour: Rate above which a grid point counts
                as wet, in mm/h. The dataset is already in mm/h, so this
                is used directly.

        Returns:
            Mapping of ISO valid time to the number of wet grid points.
        """
        wet = precipitation[PRECIPITATION_MM_PER_HOUR] > threshold_mm_per_hour
        counts = wet.sum(dim=("y", "x"))
        return {
            np.datetime_as_string(time, unit="s"): int(count)
            for time, count in zip(
                counts["time"].values, counts.values, strict=True
            )
        }

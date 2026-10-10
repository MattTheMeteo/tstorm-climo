"""Accumulation of precipitation events into a climatology."""

from __future__ import annotations

import numpy as np
import xarray as xr

# Default wet threshold, in mm/h. Matches the 0.05 mm/h used by the
# per-place layer's callers and sits above the archive's numerical dust.
# Higher thresholds rank places far more reliably (see
# `summarize.split_half_reliability`).
DEFAULT_WET_THRESHOLD_MM_PER_HOUR = 0.05

# kg m-2 s-1 -> mm/h is a factor of 3600 (1 kg m-2 of liquid water is
# 1 mm deep, so 1 kg m-2 s-1 is 1 mm/s). The archive stores the former,
# so thresholds must be converted before comparison.
SECONDS_PER_HOUR = 3600.0


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
            precipitation: Dataset containing a ``precipitation`` variable
                in kg m-2 s-1, such as the result of
                :func:`tstorm_climo.hrrr.load_precipitation`.
            threshold_mm_per_hour: Rate above which a grid point counts
                as wet, given in **mm/h** and converted internally.

        Returns:
            Mapping of ISO valid time to the number of wet grid points.
        """
        threshold = threshold_mm_per_hour / SECONDS_PER_HOUR
        wet = precipitation["precipitation"] > threshold
        counts = wet.sum(dim=("y", "x"))
        return {
            np.datetime_as_string(time, unit="s"): int(count)
            for time, count in zip(
                counts["time"].values, counts.values, strict=True
            )
        }

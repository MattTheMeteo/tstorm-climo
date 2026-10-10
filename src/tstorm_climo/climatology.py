"""Accumulation of precipitation events into a climatology."""

from __future__ import annotations

import numpy as np
import xarray as xr


class Climatology:
    """Accumulates precipitation events into a climatology.

    A grid point "received rain" during a step when the hourly-average
    precipitation rate is positive.
    """

    def summarize(
        self,
        precipitation: xr.Dataset,
    ) -> dict[str, int]:
        """Count wet grid points in an already-loaded precipitation dataset.

        Args:
            precipitation: Dataset containing a ``precipitation`` variable,
                such as the result of :func:`tstorm_climo.hrrr.load_precipitation`.

        Returns:
            Mapping of ISO valid time to the number of grid points with
            precipitation.
        """
        wet = precipitation["precipitation"] > 0
        counts = wet.sum(dim=("y", "x"))
        return {
            np.datetime_as_string(time, unit="s"): int(count)
            for time, count in zip(
                counts["time"].values, counts.values, strict=True
            )
        }

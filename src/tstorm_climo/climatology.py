"""Accumulation of precipitation events into a climatology."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from tstorm_climo.hrrr import Opener, load_precipitation


class Climatology:
    """Accumulates precipitation events into a climatology.

    A grid point "received rain" during a step when the hourly-average
    precipitation rate is positive.
    """

    def summarize(
        self,
        start: str,
        end: str,
        files: Iterable[str] | None = None,
        *,
        opener: Opener | None = None,
    ) -> dict[str, int]:
        """Count precipitation grid points for each hourly analysis step.

        Args:
            start: Inclusive start of the time range.
            end: Inclusive end of the time range.
            files: Deprecated file list; accepted for backwards
                compatibility and ignored.
            opener: Override for ``dynamical_catalog.open`` (for tests).

        Returns:
            Mapping of ISO valid time to the number of grid points with
            precipitation.
        """
        del files  # unused; kept only for backwards compatibility
        ds = load_precipitation(start, end, opener=opener)
        wet = ds["precipitation"] > 0
        counts = wet.sum(dim=("y", "x"))
        return {
            np.datetime_as_string(time, unit="s"): int(count)
            for time, count in zip(
                counts["time"].values, counts.values, strict=True
            )
        }

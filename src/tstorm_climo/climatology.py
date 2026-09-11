"""Accumulation of precipitation events into a climatology."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from tstorm_climo.hrrr import load_precipitation


@dataclass
class Climatology:
    """Accumulates precipitation events into a climatology.

    For now this tracks when rain has fallen; counts are keyed by the
    valid time of each HRRR analysis step.
    """

    # valid time (ISO string) -> number of grid points with precipitation
    events: dict[str, int] = field(default_factory=dict)

    def add_precipitation_events(self, files: Iterable[str]) -> dict[str, int]:
        """Count precipitation grid points for each given HRRR analysis file."""
        for path in files:
            ds = load_precipitation(path)
            valid_time = str(ds.attrs["valid_time"])
            # Precipitation in HRRR surface analyses is accumulated; a grid
            # point "received rain" if the accumulated value is positive.
            wet = ds["precipitation"] > 0
            self.events[valid_time] = int(wet.sum())
        return self.events

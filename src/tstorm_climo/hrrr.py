"""Access HRRR analysis data from dynamical.org (Icechunk Zarr)."""

from __future__ import annotations

from collections.abc import Callable

import dynamical_catalog
import xarray as xr

# dynamical.org's HRRR analysis archive (2014-10-01 to present, hourly).
HRRR_ANALYSIS_DATASET_ID = "noaa-hrrr-analysis"

# Start of the usable analysis window. The archive is sparse before 2018
# and has gaps through 2020; missing data arrives as NaN, and NaN fails
# any "precipitation > threshold" test, so a pre-2018 gap would score as
# *dry* and bias a climatology toward "it never rained". Clamping reads
# to this date keeps that from happening.
MIN_VALID_DATE = "2018-01-01"

# Average precipitation rate since the previous step, kg m-2 s-1.
PRECIPITATION_VARIABLE = "precipitation_surface"

# The archive stores precipitation as a rate in kg m-2 s-1, where 1 unit
# is 1 mm/s, which is 3600 mm/h. That unit is easy to misread by three
# orders of magnitude, so everything above this module works in mm/h and
# the conversion happens exactly once, here.
SECONDS_PER_HOUR = 3600.0

# Name of the rate after conversion, used by load_precipitation and
# extract so no downstream module has to know the archive's units.
PRECIPITATION_MM_PER_HOUR = "precipitation_mm_per_hour"

# Factory for opening the HRRR dataset; swappable in tests.
type Opener = Callable[..., xr.Dataset]

_open: Opener = dynamical_catalog.open


def open_analysis(
    *,
    dataset_id: str = HRRR_ANALYSIS_DATASET_ID,
    opener: Opener | None = None,
) -> xr.Dataset:
    """Open the raw analysis dataset, including its lat/lon coordinates.

    Use this when the 2-D ``latitude``/``longitude`` arrays are needed
    (to build a :class:`~tstorm_climo.grid.Grid`); otherwise prefer
    :func:`load_precipitation`, which slices time and renames the
    variable.
    """
    open_fn = opener or _open
    return open_fn(dataset_id, chunks=None)


def load_precipitation(
    start: str,
    end: str,
    *,
    dataset_id: str = HRRR_ANALYSIS_DATASET_ID,
    opener: Opener | None = None,
) -> xr.Dataset:
    """Load hourly precipitation rate from the HRRR analysis archive.

    Args:
        start: Inclusive start of the time range (anything xarray's
            ``.sel(time=slice(...))`` accepts, e.g. ``"2025-07-01"``).
        end: Inclusive end of the time range.
        dataset_id: dynamical.org dataset to read from.
        opener: Override for ``dynamical_catalog.open`` (for tests).

    Returns:
        Dataset with a ``precipitation_mm_per_hour`` variable over
        ``time × y × x``. The rate is converted from the archive's
        kg m-2 s-1 here, once, so callers never handle that unit.
    """
    da = open_analysis(dataset_id=dataset_id, opener=opener)[
        PRECIPITATION_VARIABLE
    ]
    da = da.sel(time=slice(max(start, MIN_VALID_DATE), end))
    return to_mm_per_hour(da).to_dataset(name=PRECIPITATION_MM_PER_HOUR)


def to_mm_per_hour(da: xr.DataArray) -> xr.DataArray:
    """Convert a kg m-2 s-1 precipitation rate to mm/h.

    Args:
        da: DataArray of the archive's ``precipitation_surface``, or an
            equivalent slice of it.

    Returns:
        The same values scaled by :data:`SECONDS_PER_HOUR`, with the
        ``units`` attribute updated. NaN stays NaN, and zero stays zero.
    """
    converted = da * SECONDS_PER_HOUR
    converted.attrs = {
        **da.attrs,
        "units": "mm h-1",
        "long_name": "Hourly precipitation rate",
        "comment": (
            "Average precipitation rate over the previous hour, "
            "converted from the archive's kg m-2 s-1."
        ),
    }
    return converted

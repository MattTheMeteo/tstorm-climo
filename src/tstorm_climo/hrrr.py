"""Access HRRR analysis data from dynamical.org (Icechunk Zarr)."""

from __future__ import annotations

from collections.abc import Callable

import dynamical_catalog
import xarray as xr

# dynamical.org's HRRR analysis archive (2014-10-01 to present, hourly).
HRRR_ANALYSIS_DATASET_ID = "noaa-hrrr-analysis"

# Average precipitation rate since the previous step, kg m-2 s-1.
PRECIPITATION_VARIABLE = "precipitation_surface"

# Factory for opening the HRRR dataset; swappable in tests.
type Opener = Callable[..., xr.Dataset]

_open: Opener = dynamical_catalog.open


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
        Dataset with a ``precipitation`` variable (renamed from the
        native ``precipitation_surface``) over ``time × y × x``.
    """
    open_fn = opener or _open
    da = open_fn(dataset_id, chunks=None)[PRECIPITATION_VARIABLE]
    da = da.sel(time=slice(start, end))
    return da.to_dataset(name="precipitation")

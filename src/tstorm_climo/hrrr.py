"""Reading precipitation fields from HRRR analysis files."""

from __future__ import annotations

import xarray as xr


def load_precipitation(path: str) -> xr.Dataset:
    """Load the accumulated precipitation field from an HRRR analysis file.

    Uses HRRR's total precipitation from the surface analysis (APCP).
    """
    ds = xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={"indexpath": ""},
        filter_by_keys={"shortName": "apcp"},
    )
    ds = ds.rename({"tp": "precipitation"}) if "tp" in ds else ds
    return ds

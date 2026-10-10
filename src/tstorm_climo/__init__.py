"""tstorm-climo: build a precipitation climatology from HRRR analysis data.

Data is read from dynamical.org's Icechunk Zarr archive of HRRR analyses
via the dynamical-catalog package.
"""

from __future__ import annotations

from tstorm_climo.climatology import summarize
from tstorm_climo.grid import Grid, GridCell, PlaceOffGridError
from tstorm_climo.hrrr import load_precipitation

__all__ = [
    "Grid",
    "GridCell",
    "PlaceOffGridError",
    "load_precipitation",
    "summarize",
]


def main() -> None:
    """Entry point for the console script."""
    from tstorm_climo.cli import main as cli_main

    raise SystemExit(cli_main())

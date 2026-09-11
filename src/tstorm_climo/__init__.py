"""tstorm-climo: build a precipitation climatology from HRRR analysis data."""

from __future__ import annotations

from tstorm_climo.climatology import Climatology
from tstorm_climo.hrrr import load_precipitation

__all__ = ["Climatology", "load_precipitation"]


def main() -> None:
    """Entry point for the console script."""
    from tstorm_climo.cli import main as cli_main

    raise SystemExit(cli_main())

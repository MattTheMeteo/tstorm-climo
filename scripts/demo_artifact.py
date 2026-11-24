"""Build demo artifacts using real course coordinates and synthetic rainfall.

The demo uses the committed OpenStreetMap course locations and a synthetic
30-day precipitation archive (~15% of hours wet, one noon gap), so it runs
with no network and no HRRR reads. It writes both artifact formats:

- ``artifacts/demo-course-climatology/observations`` — the hourly artifact the
  app's tables and charts read.
- ``artifacts/demo-course-climatology/gridded`` — a gridded store for API
  development. The course map uses original course coordinates, not these
  grid-node coordinates.

Run and follow the printed export lines:

    uv run python scripts/demo_artifact.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from tstorm_climo.artifacts import build_hourly_observation_artifact
from tstorm_climo.gridded import build_gridded_artifact
from tstorm_climo.places import Place, load_places
from tstorm_climo.progress import configure_logging

START = "2026-09-01"
END = "2026-10-01"
HOURS = 24 * 30
DEMO_DIR = Path("artifacts") / "demo-course-climatology"

# Rows (y) run south→north; columns (x) west→east.
ROWS, COLS = 12, 12
GRID_LAT = np.broadcast_to(42.30 + np.arange(ROWS)[:, None] * 0.06, (ROWS, COLS)).copy()
GRID_LON = np.broadcast_to(
    -71.24 + np.arange(COLS)[None, :] * 0.06, (ROWS, COLS)
).copy()


def make_opener():
    """A 30-day synthetic HRRR block, rates in kg m-2 s-1 as the archive."""
    rng = np.random.default_rng(42)
    rates = rng.gamma(shape=0.3, scale=2.0, size=(HOURS, ROWS, COLS))
    rates[(rng.random(size=rates.shape) < 0.85)] = 0.0
    rates[300:312, :, :] = np.nan  # one missing half-day, archive-wide

    def opener(dataset_id: str, **kwargs):
        return xr.Dataset(
            {"precipitation_surface": (("time", "y", "x"), rates / 3600.0)},
            coords={
                "time": pd.date_range(f"{START}T00", periods=HOURS, freq="h"),
                "y": np.arange(ROWS),
                "x": np.arange(COLS),
                "latitude": (("y", "x"), GRID_LAT),
                "longitude": (("y", "x"), GRID_LON),
            },
        )

    return opener


def demo_places() -> list[Place]:
    """Load real course points; only their precipitation values are synthetic."""
    return [
        place
        for place in load_places("places_north_shore.csv")
        if place.kind == "golf_course"
    ]


def main() -> None:
    opener = make_opener()
    places = demo_places()
    observations = DEMO_DIR / "observations"
    gridded = DEMO_DIR / "gridded"

    for directory in (observations, gridded):
        if directory.exists():
            raise SystemExit(
                f"{directory} exists - delete it first to rebuild the demo."
            )

    build_hourly_observation_artifact(places, START, END, observations, opener=opener)
    build_gridded_artifact(places, START, END, gridded, opener=opener, chunk_hours=168)

    print(
        "Demo artifacts built with real course locations. Point the app at them with:"
    )
    print(f'  export TSTORM_CLIMO_ARTIFACT="{observations}"')
    print(f'  export TSTORM_CLIMO_GRIDDED="{gridded}"')


if __name__ == "__main__":
    configure_logging()
    main()

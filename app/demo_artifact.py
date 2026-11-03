"""Build small demo artifacts for local development of the marimo app.

The demo archive is synthetic (30 days of hourly precipitation over a
10x12 grid near the North Shore, ~15% of hours wet, one noon gap) so
this runs with no network and no HRRR reads. It writes both formats the
app reads:

- ``artifacts/demo/observations`` — the hourly observation artifact the
  app's tables and charts read.
- ``artifacts/demo/gridded`` — the gridded display store the app's map
  view reads.

Run and follow the printed export lines:

    uv run --group app python app/demo_artifact.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from tstorm_climo.artifacts import build_hourly_observation_artifact
from tstorm_climo.gridded import build_gridded_artifact
from tstorm_climo.places import Place

START = "2026-09-01"
END = "2026-10-01"
HOURS = 24 * 30
DEMO_DIR = Path("artifacts") / "demo"

# Rows (y) run south→north; columns (x) west→east.
ROWS, COLS = 10, 12
GRID_LAT = np.broadcast_to(42.35 + np.arange(ROWS)[:, None] * 0.06, (ROWS, COLS)).copy()
GRID_LON = np.broadcast_to(-71.2 + np.arange(COLS)[None, :] * 0.06, (ROWS, COLS)).copy()

COURSES = ("Turfway Downs", "Salem Links", "Ipswich Bluffs", "Cape Ann CC")
BEACHES = ("Devereux Beach", "Singing Beach", "Revere Sands")


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


def place_at(osm_id: int, name: str, kind: str, row: int, col: int) -> Place:
    return Place(
        osm_type="node",
        osm_id=osm_id,
        name=name,
        kind=kind,
        lat=float(GRID_LAT[row, col]),
        lon=float(GRID_LON[row, col]),
        timezone="America/New_York",
    )


def demo_places() -> list[Place]:
    places = [
        place_at(1000 + i, name, "golf_course", 2, 2 * i)
        for i, name in enumerate(COURSES)
    ]
    # Two courses on one node, so the app shows the tie treatment.
    places.append(place_at(1999, "Turfway Par-3", "golf_course", 2, 4))
    places.extend(
        place_at(2000 + i, name, "beach", 7, 3 * i) for i, name in enumerate(BEACHES)
    )
    return places


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

    print("Demo artifacts built. Point the app at them with:")
    print(f'  export TSTORM_CLIMO_ARTIFACT="{observations}"')
    print(f'  export TSTORM_CLIMO_GRIDDED="{gridded}"')


if __name__ == "__main__":
    main()

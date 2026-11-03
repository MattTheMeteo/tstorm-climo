"""Tests for reporting from a built artifact rather than the archive.

The artifact path is what a hosted service uses: prepared data, read
locally, no archive access. These tests use a synthetic archive to
build the artifact, then report from it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr

from tstorm_climo.artifacts import build_hourly_observation_artifact
from tstorm_climo.cli import build_parser, main
from tstorm_climo.places import Place, save_csv

GRID_LAT = np.broadcast_to(42.0 - np.arange(3)[:, None] * 0.027, (3, 3)).copy()
GRID_LON = np.broadcast_to(-71.0 + np.arange(3)[None, :] * 0.036, (3, 3)).copy()

HOURS = 6
START = "2025-07-15"

# mm/h per node. The first course is wet in three hours, the second in
# one, and the beach never.
MM_PER_HOUR = {
    (1, 1): [0.20, 0.00, 0.30, 0.00, 0.10, 0.00],
    (2, 2): [0.00, 0.40, 0.00, 0.00, 0.00, 0.00],
    (1, 0): [0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
}


def make_opener():
    values = np.zeros((HOURS, 3, 3), dtype=np.float32)
    for (row, col), series in MM_PER_HOUR.items():
        values[:, row, col] = np.array(series) / 3600.0

    def opener(dataset_id, **kwargs):
        return xr.Dataset(
            {"precipitation_surface": (("time", "y", "x"), values)},
            coords={
                "time": pd.date_range(f"{START}T00", periods=HOURS, freq="h"),
                "y": np.arange(3),
                "x": np.arange(3),
                "latitude": (("y", "x"), GRID_LAT),
                "longitude": (("y", "x"), GRID_LON),
            },
        )

    return opener


def place(osm_id: int, name: str, kind: str, row: int, col: int) -> Place:
    return Place(
        osm_type="way",
        osm_id=osm_id,
        name=name,
        kind=kind,
        lat=float(GRID_LAT[row, col]),
        lon=float(GRID_LON[row, col]),
        timezone="America/New_York",
    )


PLACES = [
    place(1, "Wet Course", "golf_course", 1, 1),
    place(2, "Dry Course", "golf_course", 2, 2),
    place(3, "A Beach", "beach", 1, 0),
]


@pytest.fixture
def artifact_dir(tmp_path: Path) -> str:
    output = tmp_path / "rain-artifact"
    build_hourly_observation_artifact(
        PLACES, START, "2025-07-15", output, opener=make_opener()
    )
    return str(output)


@pytest.fixture
def places_csv(tmp_path: Path) -> str:
    path = tmp_path / "places.csv"
    save_csv(PLACES, str(path))
    return str(path)


def test_rain_report_reads_an_artifact_without_the_archive(
    artifact_dir: str, capsys
) -> None:
    assert (
        main(
            [
                "rain-report",
                "--artifact",
                artifact_dir,
                "--kind",
                "golf_course",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Wet Course" in out
    assert "Dry Course" in out
    assert "driest first" in out
    # The beach was filtered out.
    assert "A Beach" not in out


def test_rain_report_from_an_artifact_includes_the_beach_by_default(
    artifact_dir: str, capsys
) -> None:
    assert main(["rain-report", "--artifact", artifact_dir]) == 0
    out = capsys.readouterr().out
    assert "A Beach" in out


def test_rain_report_from_an_artifact_shows_reliability(
    artifact_dir: str, capsys
) -> None:
    """A leaderboard without its reliability figure is misleading."""
    main(["rain-report", "--artifact", artifact_dir, "--kind", "golf_course"])
    out = capsys.readouterr().out
    assert "Ranking reliability" in out
    assert "Caveats:" in out


def test_rain_report_from_an_artifact_records_its_source(
    artifact_dir: str, tmp_path: Path
) -> None:
    """The provenance sidecar must say where the numbers came from."""
    path = tmp_path / "report.parquet"
    main(["rain-report", "--artifact", artifact_dir, "--output", str(path)])

    metadata = json.loads(Path(f"{path}.meta.json").read_text())
    assert metadata["report"]["source"] == f"artifact:{artifact_dir}"
    assert metadata["place_count"] == 3


def test_rain_report_from_an_artifact_records_the_covered_range(
    artifact_dir: str, tmp_path: Path
) -> None:
    """With no --start/--end, the provenance records the artifact's span."""
    path = tmp_path / "report.parquet"
    main(["rain-report", "--artifact", artifact_dir, "--output", str(path)])

    metadata = json.loads(Path(f"{path}.meta.json").read_text())
    assert metadata["date_range"]["requested_start"] == "2025-07-15T00:00:00+00:00"
    assert metadata["date_range"]["requested_end"] == "2025-07-15T05:00:00+00:00"


def test_rain_report_from_an_artifact_honours_a_date_range(
    artifact_dir: str, capsys
) -> None:
    """A narrower range must change the answer."""
    assert (
        main(
            [
                "rain-report",
                "--artifact",
                artifact_dir,
                "--kind",
                "golf_course",
                "--start",
                "2025-07-15T00",
                "--end",
                "2025-07-15T00",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    # Only hour zero is in range: the first course was wet, the second dry.
    assert "Wet Course" in out
    assert "Dry Course" in out


def test_rain_report_from_an_artifact_can_slice_a_month(
    artifact_dir: str, capsys
) -> None:
    assert (
        main(
            [
                "rain-report",
                "--artifact",
                artifact_dir,
                "--month",
                "7",
                "--kind",
                "golf_course",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "month 07" in out
    assert "Wet Course" in out


def test_rain_report_rejects_a_missing_artifact(tmp_path: Path, capsys) -> None:
    assert main(["rain-report", "--artifact", str(tmp_path / "nope")]) == 1
    assert "not an hourly observation artifact" in capsys.readouterr().err


def test_rain_report_needs_dates_when_reading_the_archive(
    places_csv: str, capsys
) -> None:
    """--places still needs an explicit range; --artifact does not."""
    assert main(["rain-report", "--places", places_csv]) == 1
    assert "--start and --end" in capsys.readouterr().err


def test_places_and_artifact_are_mutually_exclusive(artifact_dir: str) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "rain-report",
                "--places",
                "p.csv",
                "--artifact",
                artifact_dir,
                "--start",
                "a",
                "--end",
                "b",
            ]
        )


def test_rain_report_from_an_artifact_writes_a_table(
    artifact_dir: str, tmp_path: Path
) -> None:
    path = tmp_path / "report.parquet"
    main(["rain-report", "--artifact", artifact_dir, "--output", str(path)])

    table = pl.read_parquet(path)
    assert table.height == 3
    assert "wet_fraction" in table.columns
    assert "wet_hours" in table.columns

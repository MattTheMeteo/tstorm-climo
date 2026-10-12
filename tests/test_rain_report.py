"""Tests for the rain-report command and its provenance output.

Uses a synthetic extraction so no test touches the network.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import polars as pl
import pytest

from tstorm_climo.cli import build_parser, main
from tstorm_climo.extract import SCHEMA
from tstorm_climo.places import Place, save_csv
from tstorm_climo.provenance import (
    ARCHIVE_ATTRIBUTION,
    build_metadata,
    format_caveats,
    write_metadata,
)

PLACES = [
    Place(
        osm_type="way",
        osm_id=1,
        name="Dry Course",
        kind="golf_course",
        lat=42.0,
        lon=-70.9,
        timezone="America/New_York",
    ),
    Place(
        osm_type="way",
        osm_id=2,
        name="Wet Course",
        kind="golf_course",
        lat=41.9,
        lon=-70.8,
        timezone="America/New_York",
    ),
    Place(
        osm_type="way",
        osm_id=3,
        name="A Beach",
        kind="beach",
        lat=42.1,
        lon=-71.0,
        timezone="America/New_York",
    ),
]


@pytest.fixture
def places_csv(tmp_path: Path) -> str:
    path = tmp_path / "places.csv"
    save_csv(PLACES, str(path))
    return str(path)


def make_series(n_hours: int = 48, places=PLACES) -> pl.DataFrame:
    """A long-format series: place 0 dry, place 1 wet, place 2 medium."""
    from datetime import datetime, timedelta

    base = datetime(2025, 7, 15, tzinfo=UTC)
    patterns = {0: 0.0, 1: 1.0, 2: 0.5}
    rows: dict[str, list] = {k: [] for k in SCHEMA}
    for place in places:
        index = PLACES.index(place)
        pattern = patterns[index]
        for hour in range(n_hours):
            rows["place_key"].append(place.place_key)
            rows["time"].append(base + timedelta(hours=hour))
            rows["precipitation_mm_per_hour"].append(pattern)
            rows["valid"].append(True)
            rows["y_index"].append(index)
            rows["x_index"].append(0)
            rows["grid_lat"].append(42.0)
            rows["grid_lon"].append(-70.9)
            rows["snap_distance_km"].append(1.0)
            rows["tz_name"].append("America/New_York")
    return pl.DataFrame(rows, schema=SCHEMA)


@pytest.fixture
def fake_extract(monkeypatch):
    """Replace the network read with a synthetic series."""
    import tstorm_climo.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "extract_series",
        lambda places, start, end, **kwargs: make_series(places=places),
    )


def test_rain_report_parses_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "rain-report",
            "--places",
            "p.csv",
            "--start",
            "2025-06-01",
            "--end",
            "2025-07-01",
            "--threshold",
            "0.25",
            "--kind",
            "golf_course",
            "--month",
            "6",
        ]
    )
    assert args.command == "rain-report"
    assert args.threshold == 0.25
    assert args.kind == ["golf_course"]
    assert args.month == 6


def test_rain_report_defaults_to_point_zero_five_mm_per_hour() -> None:
    args = build_parser().parse_args(
        ["rain-report", "--places", "p.csv", "--start", "a", "--end", "b"]
    )
    assert args.threshold == 0.05


def test_rain_report_requires_places_and_dates() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["rain-report"])


def test_rain_report_prints_table(places_csv: str, fake_extract, capsys) -> None:
    assert (
        main(
            [
                "rain-report",
                "--places",
                places_csv,
                "--start",
                "2025-07-01",
                "--end",
                "2025-08-01",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Dry Course" in out
    assert "Wet Course" in out
    assert "driest first" in out


def test_rain_report_filters_kind(places_csv, fake_extract, capsys) -> None:
    assert (
        main(
            [
                "rain-report",
                "--places",
                places_csv,
                "--start",
                "2025-07-01",
                "--end",
                "2025-08-01",
                "--kind",
                "golf_course",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Dry Course" in out
    assert "A Beach" not in out


def test_rain_report_non_golf_kind_is_not_dropped(
    places_csv, fake_extract, capsys
) -> None:
    """A non-golf --kind must not be re-filtered back to golf courses.

    Regression: scorecard had a hardcoded golf_course default, so any
    other kind (or a mixed --kind list) was filtered to nothing.
    """
    assert (
        main(
            [
                "rain-report",
                "--places",
                places_csv,
                "--start",
                "2025-07-01",
                "--end",
                "2025-08-01",
                "--kind",
                "beach",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "A Beach" in out
    assert "Dry Course" not in out


def test_rain_report_shows_reliability_and_caveats(
    places_csv, fake_extract, capsys
) -> None:
    """A leaderboard without its reliability figure is misleading."""
    main(
        [
            "rain-report",
            "--places",
            places_csv,
            "--start",
            "2025-07-01",
            "--end",
            "2025-08-01",
        ]
    )
    out = capsys.readouterr().out
    assert "Ranking reliability" in out
    assert "Caveats:" in out
    assert ARCHIVE_ATTRIBUTION in out


def test_rain_report_writes_parquet_and_sidecar(
    places_csv, fake_extract, tmp_path, capsys
) -> None:
    path = tmp_path / "report.parquet"
    main(
        [
            "rain-report",
            "--places",
            places_csv,
            "--start",
            "2025-07-01",
            "--end",
            "2025-08-01",
            "--output",
            str(path),
        ]
    )
    capsys.readouterr()
    assert path.exists()
    table = pl.read_parquet(path)
    assert table.height == 3
    assert "place_key" in table.columns
    metadata = json.loads(Path(f"{path}.meta.json").read_text())
    assert metadata["threshold_mm_per_hour"] == 0.05
    assert metadata["place_count"] == 3
    assert "ranking_reliability" in metadata


def test_rain_report_writes_csv_when_extension_says_so(
    places_csv, fake_extract, tmp_path
) -> None:
    path = tmp_path / "report.csv"
    main(
        [
            "rain-report",
            "--places",
            places_csv,
            "--start",
            "2025-07-01",
            "--end",
            "2025-08-01",
            "--output",
            str(path),
        ]
    )
    assert pl.read_csv(path).height == 3


def test_rain_report_month_slices_locally(places_csv, fake_extract, capsys) -> None:
    main(
        [
            "rain-report",
            "--places",
            places_csv,
            "--start",
            "2025-07-01",
            "--end",
            "2025-08-01",
            "--month",
            "7",
        ]
    )
    out = capsys.readouterr().out
    assert "month 07" in out


def test_rain_report_errors_when_no_places_match(
    places_csv, fake_extract, capsys
) -> None:
    assert (
        main(
            [
                "rain-report",
                "--places",
                places_csv,
                "--start",
                "2025-07-01",
                "--end",
                "2025-08-01",
                "--kind",
                "swimming_pool",
            ]
        )
        == 1
    )
    assert "No places" in capsys.readouterr().err


def test_metadata_records_threshold_and_licences() -> None:
    metadata = build_metadata(
        start="2025-01-01",
        end="2025-12-31",
        threshold_mm_per_hour=0.5,
        place_count=10,
        reliability=0.41,
    )
    assert metadata["threshold_mm_per_hour"] == 0.5
    assert metadata["ranking_reliability"] == 0.41
    assert metadata["attribution"]["archive_license"] == "CC-BY-4.0"
    assert metadata["attribution"]["places_license"] == "ODbL"
    assert metadata["units"]["precipitation_mm_per_hour"] == "mm h-1"


def test_metadata_omits_reliability_when_absent() -> None:
    metadata = build_metadata(
        start="a", end="b", threshold_mm_per_hour=0.5, place_count=1
    )
    assert "ranking_reliability" not in metadata


def test_write_metadata_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "out.parquet"
    sidecar = write_metadata(path, {"k": "v"})
    assert sidecar == Path(f"{path}.meta.json")
    assert json.loads(sidecar.read_text()) == {"k": "v"}


def test_format_caveats_includes_reliability_when_present() -> None:
    text = format_caveats({"ranking_reliability": 0.41, "caveats": ["grid is coarse"]})
    assert "0.41" in text
    assert "grid is coarse" in text

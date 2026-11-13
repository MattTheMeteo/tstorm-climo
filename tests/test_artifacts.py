"""Tests for the v3 observation artifact: build, load, and error handling."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr

from tstorm_climo.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    ARTIFACT_TYPE,
    OBSERVATIONS_DIR,
    SLIM_COLUMNS,
    STATIC_COLUMNS,
    build_hourly_observation_artifact,
    load_observation_artifact,
)
from tstorm_climo.places import PLACE_KEY_COLUMN, Place

# The 3x3 grid the synthetic archive installs. Node (row, col).
GRID_LAT = np.broadcast_to(42.0 - np.arange(3)[:, None] * 0.027, (3, 3)).copy()
GRID_LON = np.broadcast_to(-71.0 + np.arange(3)[None, :] * 0.036, (3, 3)).copy()

HOURS = 6
START = "2025-07-15"

MM_PER_HOUR = {
    (1, 1): [0.20, 0.00, 0.30, 0.00, 0.10, 0.00],
    (2, 2): [0.00, 0.40, 0.00, 0.00, 0.00, 0.00],
    (1, 0): [0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
}


def make_opener():
    values = np.zeros((HOURS, 3, 3), dtype=np.float32)
    for (row, col), series in MM_PER_HOUR.items():
        values[:, row, col] = np.array(series) / 3600.0

    def opener(dataset_id: str, **kwargs):
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


def course(osm_id: int, row: int, col: int) -> Place:
    return Place(
        osm_type="way",
        osm_id=osm_id,
        name=f"Course {osm_id}",
        kind="golf_course",
        lat=float(GRID_LAT[row, col]),
        lon=float(GRID_LON[row, col]),
        timezone="America/New_York",
    )


PLACES = [
    course(1, 1, 1),
    course(2, 2, 2),
    Place(
        osm_type="node",
        osm_id=99,
        name="A Beach",
        kind="beach",
        lat=float(GRID_LAT[1, 0]),
        lon=float(GRID_LON[1, 0]),
        timezone="America/New_York",
    ),
]


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    output = tmp_path / "artifact"
    build_hourly_observation_artifact(
        PLACES, START, "2025-07-15", output, opener=make_opener(), shard_count=2
    )
    return output


def test_build_writes_the_v3_layout(artifact_dir: Path) -> None:
    assert set(path.name for path in artifact_dir.iterdir()) == {
        "observations",
        "places.parquet",
        "manifest.json",
    }
    shards = sorted((artifact_dir / OBSERVATIONS_DIR).glob("*.parquet"))
    assert len(shards) == 2


def test_build_logs_stage_boundaries(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="tstorm_climo.artifacts")

    build_hourly_observation_artifact(
        PLACES,
        START,
        "2025-07-15",
        tmp_path / "artifact",
        opener=make_opener(),
        shard_count=2,
    )

    assert "Building observation artifact" in caplog.text
    assert "Writing 2 observation shards..." in caplog.text
    assert "Wrote shard-0000.parquet" in caplog.text
    assert "Observation artifact ready" in caplog.text


def test_slim_observations_have_only_the_dynamic_columns(artifact_dir: Path) -> None:
    for shard in (artifact_dir / OBSERVATIONS_DIR).glob("*.parquet"):
        assert pl.read_parquet(shard).columns == list(SLIM_COLUMNS)


def test_places_parquet_carries_the_static_columns(artifact_dir: Path) -> None:
    places = pl.read_parquet(artifact_dir / "places.parquet")
    for column in STATIC_COLUMNS:
        assert column in places.columns
    assert set(places[PLACE_KEY_COLUMN].to_list()) == {p.place_key for p in PLACES}


def test_load_round_trips_to_the_same_places(artifact_dir: Path) -> None:
    artifact = load_observation_artifact(artifact_dir)

    assert [place.place_key for place in artifact.places] == [
        p.place_key for p in sorted(PLACES, key=lambda p: p.place_key)
    ]
    assert artifact.manifest["artifact_type"] == ARTIFACT_TYPE
    assert artifact.manifest["schema_version"] == ARTIFACT_SCHEMA_VERSION
    # The observations stay lazy: no collect has happened.
    assert isinstance(artifact.observations, pl.LazyFrame)


def test_manifest_records_the_observed_coverage(artifact_dir: Path) -> None:
    artifact = load_observation_artifact(artifact_dir)

    assert artifact.observed_start is not None
    assert artifact.observed_end is not None
    assert artifact.observed_start.isoformat() == "2025-07-15T00:00:00+00:00"
    assert artifact.observed_end.isoformat() == "2025-07-15T05:00:00+00:00"
    assert artifact.manifest["row_count"] == len(PLACES) * HOURS
    assert artifact.manifest["place_count"] == len(PLACES)


def test_the_manifest_on_disk_matches_the_returned_one(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    manifest = build_hourly_observation_artifact(
        PLACES, START, "2025-07-15", output, opener=make_opener()
    )
    on_disk = json.loads((output / "manifest.json").read_text())
    assert on_disk == manifest


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    output.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        build_hourly_observation_artifact(
            PLACES,
            START,
            "2025-07-15",
            output,
            opener=lambda *_args, **_kwargs: pytest.fail("should not read archive"),
        )


def test_empty_result_raises_and_leaves_no_output(tmp_path: Path) -> None:
    output = tmp_path / "artifact"
    with pytest.raises(ValueError, match="no observations"):
        build_hourly_observation_artifact(
            PLACES, "2017-01-01", "2017-01-02", output, opener=make_opener()
        )
    assert not output.exists()


def test_a_foreign_manifest_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "not-an-artifact"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps({"artifact_type": "place_catalog"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="not an hourly observation artifact"):
        load_observation_artifact(directory)


def test_an_unreadable_schema_version_is_rejected(artifact_dir: Path) -> None:
    manifest_path = artifact_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = ARTIFACT_SCHEMA_VERSION + 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported artifact schema version"):
        load_observation_artifact(artifact_dir)


def test_load_forwards_storage_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "artifact"
    build_hourly_observation_artifact(
        PLACES, START, "2025-07-15", output, opener=make_opener()
    )

    seen: dict[str, tuple[str, object]] = {}
    original_scan = pl.scan_parquet
    original_read = pl.read_parquet

    def spy_scan(path, **kwargs):
        seen["scan"] = (str(path), kwargs.get("storage_options"))
        return original_scan(path)

    def spy_read(path, **kwargs):
        seen["read"] = (str(path), kwargs.get("storage_options"))
        return original_read(path)

    monkeypatch.setattr(pl, "scan_parquet", spy_scan)
    monkeypatch.setattr(pl, "read_parquet", spy_read)

    options = {"aws_region": "us-east-1"}
    load_observation_artifact(str(output), storage_options=options)

    assert seen["scan"][1] == options
    assert seen["read"][1] == options
    assert seen["scan"][0].endswith("observations/*.parquet")


def test_read_text_reads_a_file_url(tmp_path: Path) -> None:
    from tstorm_climo.artifacts import _read_text

    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version": 3}', encoding="utf-8")
    assert _read_text(path.as_uri()) == '{"schema_version": 3}'


def test_loading_from_s3_names_the_missing_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """boto3 is an optional extra, so a core install cannot read a remote
    artifact. The error has to name the extra, or the user is left
    guessing how a plain `uv sync` missed it.
    """
    # A None entry in sys.modules makes ``import boto3`` fail the way an
    # absent package does (see also test_catalog_storage.py).
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(RuntimeError, match=r"tstorm-climo\[catalog-s3\]"):
        load_observation_artifact("s3://bucket/north-shore/rain-2018-2025")

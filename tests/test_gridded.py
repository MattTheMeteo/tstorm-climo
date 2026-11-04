"""Tests for the gridded display artifact: build, load, and wet hours."""

from __future__ import annotations

import json
from pathlib import Path

import icechunk
import numpy as np
import pandas as pd
import pytest
import xarray as xr
import zarr

from tstorm_climo.gridded import (
    GRIDDED_ARTIFACT_TYPE,
    GRIDDED_SCHEMA_VERSION,
    MANIFEST_FILE,
    PRECIPITATION_MM_PER_HOUR,
    build_gridded_artifact,
    load_gridded_artifact,
    valid_hours,
    wet_hours,
)
from tstorm_climo.places import Place

# The 3x3 grid the synthetic archive installs. Node (row, col).
GRID_LAT = np.broadcast_to(42.0 - np.arange(3)[:, None] * 0.027, (3, 3)).copy()
GRID_LON = np.broadcast_to(-71.0 + np.arange(3)[None, :] * 0.036, (3, 3)).copy()

HOURS = 6
START = "2025-07-15"

# Hourly rates in mm/h for the nodes that matter; every other node is
# dry. Hour 3 is missing archive-wide (NaN), so it is neither wet nor a
# valid observation for anything.
MM_PER_HOUR = {
    (1, 1): [0.20, 0.00, 0.30, np.nan, 0.10, 0.00],
    (2, 2): [0.00, 0.00, 0.00, np.nan, 0.00, 0.60],
}


def make_opener():
    values = np.zeros((HOURS, 3, 3), dtype=np.float32)
    values[3, :, :] = np.nan
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
        osm_type="way",  # polygon over node
        osm_id=osm_id,
        name=f"Course {osm_id}",
        kind="golf_course",
        lat=float(GRID_LAT[row, col]),
        lon=float(GRID_LON[row, col]),
        timezone="America/New_York",
    )


# Three places spread row 1..2, col 0..2, so the block holds six nodes.
PLACES = [course(1, 1, 1), course(2, 2, 2), course(3, 2, 0)]


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    output = tmp_path / "gridded"
    build_gridded_artifact(PLACES, START, "2025-07-15", output, opener=make_opener())
    return output


def loaded(artifact_dir: Path):
    return load_gridded_artifact(artifact_dir)


def test_build_writes_manifest_and_readable_store(artifact_dir: Path) -> None:
    assert (artifact_dir / MANIFEST_FILE).is_file()
    artifact = loaded(artifact_dir)
    assert artifact.dataset[PRECIPITATION_MM_PER_HOUR].shape == (HOURS, 2, 3)


def test_manifest_records_block_and_covered_range(artifact_dir: Path) -> None:
    manifest = json.loads((artifact_dir / MANIFEST_FILE).read_text("utf-8"))

    assert manifest["artifact_type"] == GRIDDED_ARTIFACT_TYPE
    assert manifest["schema_version"] == GRIDDED_SCHEMA_VERSION
    assert manifest["grid"] == {
        "n_rows": 2,
        "n_cols": 3,
        "y_index_start": 1,
        "x_index_start": 0,
        "time_chunk_hours": 6,
        "description": manifest["grid"]["description"],
    }
    assert manifest["date_range"]["observed_start"] == "2025-07-15T00:00:00+00:00"
    assert manifest["date_range"]["observed_end"] == "2025-07-15T05:00:00+00:00"


def test_block_coordinates_carry_archive_indexes(artifact_dir: Path) -> None:
    artifact = loaded(artifact_dir)
    ds = artifact.dataset

    assert ds["y"].values.tolist() == [1, 2]
    assert ds["x"].values.tolist() == [0, 1, 2]


def test_wet_hours_counts_per_node(artifact_dir: Path) -> None:
    artifact = loaded(artifact_dir)
    counts = wet_hours(artifact.dataset)

    # Block nodes are rows 1..2 x cols 0..2. Only (1,1) -- one big wet
    # stretch -- and (2,2) -- one heavy shower -- carry wet hours; hour 3
    # is missing and never counts as wet.
    expected = np.array([[0, 3, 0], [0, 0, 1]], dtype=np.int64)
    assert counts.values.tolist() == expected.tolist()
    assert counts.attrs["threshold_mm_per_hour"] == 0.05


def test_wet_hours_honors_threshold(artifact_dir: Path) -> None:
    artifact = loaded(artifact_dir)
    counts = wet_hours(artifact.dataset, threshold=0.25)

    # (1,1): only 0.30 qualifies; (2,2): only 0.60.
    expected = np.array([[0, 1, 0], [0, 0, 1]], dtype=np.int64)
    assert counts.values.tolist() == expected.tolist()


def test_valid_hours_exposes_missing_data(artifact_dir: Path) -> None:
    artifact = loaded(artifact_dir)
    counts = valid_hours(artifact.dataset)

    # Every hour except the NaN hour 3 is valid.
    assert counts.values.tolist() == [[HOURS - 1] * 3, [HOURS - 1] * 3]


def test_store_chunks_time_across_the_requested_size(tmp_path: Path) -> None:
    output = tmp_path / "chunked"
    build_gridded_artifact(
        PLACES, START, "2025-07-15", output, opener=make_opener(), chunk_hours=2
    )

    storage = icechunk.local_filesystem_storage(str(output))
    session = icechunk.Repository.open(storage).readonly_session(branch="main")
    node = zarr.open_group(session.store, mode="r")["precipitation_mm_per_hour"]
    assert isinstance(node, zarr.Array)
    assert node.chunks == (2, 2, 3)


def test_load_refuses_unknown_schema_version(artifact_dir: Path) -> None:
    manifest = json.loads((artifact_dir / MANIFEST_FILE).read_text("utf-8"))
    manifest["schema_version"] = 99
    (artifact_dir / MANIFEST_FILE).write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="schema version"):
        loaded(artifact_dir)


def test_load_refuses_foreign_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "foreign"
    directory.mkdir()
    (directory / MANIFEST_FILE).write_text(
        json.dumps({"artifact_type": "hourly_observations", "schema_version": 3})
    )

    with pytest.raises(ValueError, match="not a gridded display artifact"):
        loaded(directory)


def test_load_refuses_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="manifest"):
        loaded(tmp_path / "nothing-here")


def test_build_refuses_existing_output(artifact_dir: Path) -> None:
    with pytest.raises(FileExistsError):
        build_gridded_artifact(
            PLACES, START, "2025-07-15", artifact_dir, opener=make_opener()
        )


def test_build_refuses_no_places(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no places"):
        build_gridded_artifact(
            [], START, "2025-07-15", tmp_path / "empty", opener=make_opener()
        )


def test_build_refuses_window_outside_the_archive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no analysis steps"):
        build_gridded_artifact(
            PLACES,
            "2030-01-01",
            "2030-01-01",
            tmp_path / "late",
            opener=make_opener(),
        )


def test_build_refuses_non_positive_chunk_hours(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="chunk_hours"):
        build_gridded_artifact(
            PLACES,
            START,
            "2025-07-15",
            tmp_path / "zero",
            opener=make_opener(),
            chunk_hours=0,
        )

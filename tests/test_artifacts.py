"""Tests for persisted hourly observation artifacts, without network access."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import polars as pl
import pytest
import xarray as xr

from tstorm_climo.artifacts import (
    OBSERVATION_COLUMNS,
    RAIN_RATE_VARIABLE,
    build_hourly_observation_artifact,
)
from tstorm_climo.places import Place


def make_opener():
    lat = 42.0 - np.arange(3)[:, None] * 0.027
    lon = -71.0 + np.arange(3)[None, :] * 0.036
    lat = np.broadcast_to(lat, (3, 3)).copy()
    lon = np.broadcast_to(lon, (3, 3)).copy()
    rate = np.zeros((2, 3, 3), dtype=np.float32)
    rate[:, 1, 1] = [1e-5, np.nan]

    def opener(dataset_id, **kwargs):
        assert dataset_id == "noaa-hrrr-analysis"
        assert kwargs.get("chunks") is None
        return xr.Dataset(
            {"precipitation_surface": (("time", "y", "x"), rate)},
            coords={
                "time": pd.to_datetime(["2025-07-15T00", "2025-07-15T01"]),
                "y": np.arange(3),
                "x": np.arange(3),
                "latitude": (("y", "x"), lat),
                "longitude": (("y", "x"), lon),
            },
        )

    return opener


def a_place(osm_id: int = 1, *, lon: float = -70.964) -> Place:
    return Place(
        osm_type="way",
        osm_id=osm_id,
        name=f"Course {osm_id}",
        kind="golf_course",
        lat=41.973,
        lon=lon,
        timezone="America/New_York",
    )


def test_build_writes_observations_places_and_manifest(tmp_path) -> None:
    output = tmp_path / "rain-artifact"
    manifest = build_hourly_observation_artifact(
        [a_place()],
        "2025-07-15",
        "2025-07-15",
        output,
        opener=make_opener(),
    )

    assert set(path.name for path in output.iterdir()) == {
        "observations.parquet",
        "places.parquet",
        "manifest.json",
    }
    observations = pl.read_parquet(output / "observations.parquet")
    assert observations.columns == list(OBSERVATION_COLUMNS)
    assert observations["place_key"].unique().to_list() == ["osm:way:1"]
    assert observations["variable"].unique().to_list() == [RAIN_RATE_VARIABLE]
    assert observations["value"].to_list()[0] == pytest.approx(0.036)
    assert observations["valid"].to_list() == [True, False]
    assert observations["time_utc"].dtype == pl.Datetime("us", "UTC")

    places = pl.read_parquet(output / "places.parquet")
    assert places["place_key"].to_list() == ["osm:way:1"]
    assert places["name"].to_list() == ["Course 1"]

    on_disk = json.loads((output / "manifest.json").read_text())
    assert on_disk == manifest
    assert manifest["variable"]["units"] == "mm h-1"
    assert manifest["variable"]["threshold_applied"] is None
    assert manifest["date_range"]["observed_start"] == "2025-07-15T00:00:00+00:00"
    assert manifest["observations"]["row_count"] == 2
    assert manifest["observations"]["valid_row_count"] == 1
    assert manifest["observations"]["invalid_row_count"] == 1
    assert manifest["observations"]["valid_fraction"] == 0.5
    assert len(manifest["place_catalog"]["content_sha256"]) == 64


def test_place_catalog_hash_does_not_depend_on_input_order(tmp_path) -> None:
    first = build_hourly_observation_artifact(
        [a_place(1), a_place(2)],
        "2025-07-15",
        "2025-07-15",
        tmp_path / "first",
        opener=make_opener(),
    )
    second = build_hourly_observation_artifact(
        [a_place(2), a_place(1)],
        "2025-07-15",
        "2025-07-15",
        tmp_path / "second",
        opener=make_opener(),
    )
    assert (
        first["place_catalog"]["content_sha256"]
        == second["place_catalog"]["content_sha256"]
    )


def test_existing_output_is_not_overwritten(tmp_path) -> None:
    output = tmp_path / "already-there"
    output.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        build_hourly_observation_artifact(
            [a_place()],
            "2025-07-15",
            "2025-07-15",
            output,
            opener=lambda *_args, **_kwargs: pytest.fail("should not read archive"),
        )


def test_empty_result_does_not_publish_partial_artifact(tmp_path) -> None:
    output = tmp_path / "empty"
    with pytest.raises(ValueError, match="no observations"):
        build_hourly_observation_artifact(
            [a_place()],
            "2017-01-01",
            "2017-01-02",
            output,
            opener=make_opener(),
        )
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []

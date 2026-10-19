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


def _wkb(wkt: str) -> bytes:
    """Encode WKT as WKB using DuckDB's spatial extension."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        row = con.execute("SELECT ST_AsWKB(ST_GeomFromText(?))", [wkt]).fetchone()
        assert row is not None
        return bytes(row[0])
    finally:
        con.close()


def _test_grid_coords() -> tuple[np.ndarray, np.ndarray]:
    """The 3x3 lat/lon arrays :func:`make_opener` installs."""
    lat = 42.0 - np.arange(3)[:, None] * 0.027
    lon = -71.0 + np.arange(3)[None, :] * 0.036
    return (
        np.broadcast_to(lat, (3, 3)).copy(),
        np.broadcast_to(lon, (3, 3)).copy(),
    )


def _box_wkb(ys: list[int], xs: list[int], margin: float = 0.005) -> bytes:
    """A WKT box around the given nodes of the 3x3 test grid."""
    lat, lon = _test_grid_coords()
    lats = [float(lat[y, x]) for y, x in zip(ys, xs, strict=True)]
    lons = [float(lon[y, x]) for y, x in zip(ys, xs, strict=True)]
    south, north = min(lats) - margin, max(lats) + margin
    west, east = min(lons) - margin, max(lons) + margin
    return _wkb(
        f"POLYGON(({west} {north}, {east} {north}, {east} {south}, "
        f"{west} {south}, {west} {north}))"
    )


# Covers nodes (1,0) and (1,1): the place sits on (1,1) exactly, so the
# anchor is a dead-on node hit while the footprint spans two.
WIDE_BOX = _box_wkb([1, 1], [0, 1])


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
        "sampling.parquet",
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

    sampling = pl.read_parquet(output / "sampling.parquet")
    assert sampling["place_key"].unique().to_list() == ["osm:way:1"]
    assert sampling["n_cells"].unique().to_list() == [1]

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


def test_sampling_table_records_every_node_a_footprint_was_averaged_over(
    tmp_path,
) -> None:
    """The footprint survives into the artifact, not just the anchor."""
    place = a_place()
    place = place.model_copy(update={"geometry_wkb": WIDE_BOX})
    output = tmp_path / "footprint-artifact"
    manifest = build_hourly_observation_artifact(
        [place],
        "2025-07-15",
        "2025-07-15",
        output,
        opener=make_opener(),
    )

    sampling = pl.read_parquet(output / "sampling.parquet")
    assert sampling["sampling"].unique().to_list() == ["footprint"]
    assert sampling.height == int(sampling["n_cells"][0])
    assert sampling["weight"].sum() == pytest.approx(1.0)

    observations = pl.read_parquet(output / "observations.parquet")
    assert observations["sampling"].unique().to_list() == ["footprint"]
    assert observations["n_cells"].unique().to_list() == [sampling.height]
    # sampling_key identifies the node set, so it is constant per place.
    assert observations["sampling_key"].n_unique() == 1

    assert manifest["sampling_cells"]["footprint_places"] == 1
    assert manifest["sampling_cells"]["point_places"] == 0
    assert manifest["sampling_cells"]["row_count"] == sampling.height
    assert manifest["sampling_cells"]["file"] == "sampling.parquet"


def test_anchor_columns_do_not_silently_describe_a_footprint(tmp_path) -> None:
    """A multi-node place must not look like a single exact node hit.

    The anchor columns keep their historical meaning, so this pins the
    thing that would otherwise be invisible: a footprint place whose
    anchor is dead-on still reports n_cells > 1.
    """
    place = a_place()
    place = place.model_copy(update={"geometry_wkb": WIDE_BOX})
    output = tmp_path / "anchor-artifact"
    build_hourly_observation_artifact(
        [place],
        "2025-07-15",
        "2025-07-15",
        output,
        opener=make_opener(),
    )
    observations = pl.read_parquet(output / "observations.parquet")
    assert observations["snap_distance_km"][0] == pytest.approx(0.0)
    assert observations["n_cells"][0] > 1


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


def test_places_parquet_preserves_geometry(tmp_path) -> None:
    wkb = _wkb("POINT(-70.964 41.973)")
    place = a_place().model_copy(update={"geometry_wkb": wkb})
    output = tmp_path / "rain"
    build_hourly_observation_artifact(
        [place], "2025-07-15", "2025-07-15", output, opener=make_opener()
    )
    places = pl.read_parquet(output / "places.parquet")
    assert places["geometry_wkb"].dtype == pl.Binary
    assert places["geometry_wkb"][0] == wkb


def test_places_parquet_geometry_column_is_binary_even_when_empty(tmp_path) -> None:
    output = tmp_path / "rain"
    build_hourly_observation_artifact(
        [a_place()], "2025-07-15", "2025-07-15", output, opener=make_opener()
    )
    places = pl.read_parquet(output / "places.parquet")
    assert places["geometry_wkb"].dtype == pl.Binary
    assert places["geometry_wkb"][0] is None

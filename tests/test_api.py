"""Tests for the FastAPI layer over the observation and gridded artifacts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from fastapi.testclient import TestClient

from apps.web.backend.app import create_app
from apps.web.backend.config import Settings
from tstorm_climo.artifacts import build_hourly_observation_artifact
from tstorm_climo.gridded import build_gridded_artifact
from tstorm_climo.places import Place

# The 3x3 grid the synthetic archive installs. Node (row, col).
GRID_LAT = np.broadcast_to(42.0 - np.arange(3)[:, None] * 0.027, (3, 3)).copy()
GRID_LON = np.broadcast_to(-71.0 + np.arange(3)[None, :] * 0.036, (3, 3)).copy()

HOURS = 6
START = "2025-07-15"

# mm/h per node. Course 1 is wet in three hours, course 2 in one, the
# beach never.
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
def client(tmp_path: Path) -> Iterator[TestClient]:
    observations = tmp_path / "observations"
    gridded = tmp_path / "gridded"
    build_hourly_observation_artifact(
        PLACES, START, "2025-07-15", observations, opener=make_opener(), shard_count=2
    )
    build_gridded_artifact(PLACES, START, "2025-07-15", gridded, opener=make_opener())
    app = create_app(Settings(artifact_dir=str(observations), gridded_dir=str(gridded)))
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_coverage_reports_the_artifact(client: TestClient) -> None:
    body = client.get("/api/coverage").json()

    assert body["place_count"] == len(PLACES)
    assert body["kinds"] == ["beach", "golf_course"]
    assert body["variable"] == "rain_rate"
    assert body["units"] == "mm h-1"
    assert body["gridded"] is True


def test_places_filters_by_kind(client: TestClient) -> None:
    body = client.get("/api/places", params={"kind": "golf_course"}).json()

    places = body["places"]
    assert [place["kind"] for place in places] == ["golf_course", "golf_course"]
    first = places[0]
    assert first["geometry"] == {
        "type": "Point",
        "coordinates": [first["lon"], first["lat"]],
    }


def test_report_returns_the_leaderboard(client: TestClient) -> None:
    body = client.post(
        "/api/report", json={"place_keys": ["osm:way:1", "osm:way:2"]}
    ).json()

    # Drier first: course 2 (1 wet hour) before course 1 (3 wet hours).
    assert [row["name"] for row in body["summary"]] == ["Course 2", "Course 1"]
    assert body["reliability"] is not None
    assert body["monthly"]
    assert body["hourly"]
    assert body["start"] and body["end"]


def test_report_rejects_too_many_places(client: TestClient) -> None:
    keys = [f"osm:way:{osm_id}" for osm_id in range(21)]
    response = client.post("/api/report", json={"place_keys": keys})

    assert response.status_code == 422


def test_report_rejects_unknown_places(client: TestClient) -> None:
    response = client.post("/api/report", json={"place_keys": ["osm:way:999"]})

    assert response.status_code == 404


def test_wetness_returns_geojson(client: TestClient) -> None:
    body = client.get("/api/wetness").json()

    assert body["type"] == "FeatureCollection"
    # The block covers rows 1..2 x cols 0..2 = six nodes.
    assert len(body["features"]) == 6
    # Course 1 (3 wet hours) + course 2 (1 wet hour); every hour is valid.
    assert sum(f["properties"]["wet_hours"] for f in body["features"]) == 4
    assert all(f["properties"]["valid_hours"] == HOURS for f in body["features"])

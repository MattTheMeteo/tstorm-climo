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
from apps.web.backend.config import DEFAULT_CORS_ORIGINS, Settings
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
    values[:, 1, 2] = np.nan
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


def test_data_endpoints_report_unconfigured_artifacts_as_unavailable() -> None:
    with TestClient(
        create_app(Settings(artifact_dir=None, gridded_dir=None))
    ) as client:
        assert client.get("/api/coverage").status_code == 503
        assert client.get("/api/places").status_code == 503
        assert (
            client.post("/api/report", json={"place_keys": ["osm:way:1"]}).status_code
            == 503
        )
        assert client.get("/api/wetness").status_code == 503


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
    assert "reliability" in body
    assert body["monthly"]
    assert body["hourly"]
    assert body["start"] and body["end"]
    assert "timezone" not in body["summary"][0]


def test_report_rejects_too_many_places(client: TestClient) -> None:
    keys = [f"osm:way:{osm_id}" for osm_id in range(21)]
    response = client.post("/api/report", json={"place_keys": keys})

    assert response.status_code == 422


def test_report_rejects_unknown_places(client: TestClient) -> None:
    response = client.post("/api/report", json={"place_keys": ["osm:way:999"]})

    assert response.status_code == 404


def test_report_rejects_a_mixed_known_and_unknown_selection(client: TestClient) -> None:
    response = client.post(
        "/api/report", json={"place_keys": ["osm:way:1", "osm:way:999"]}
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"place_keys": ["osm:way:1"], "start": "not-a-date"},
        {"place_keys": ["osm:way:1"], "start": "2025-07-16", "end": START},
        {"place_keys": ["osm:way:1"], "threshold_mm_per_hour": -0.1},
        {"place_keys": ["osm:way:1"], "threshold_mm_per_hour": "NaN"},
        {"place_keys": ["osm:way:1"], "unexpected": True},
    ],
)
def test_report_rejects_invalid_request_values(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/api/report", json=body)

    assert response.status_code == 422


def test_wetness_returns_geojson(client: TestClient) -> None:
    body = client.get("/api/wetness").json()

    assert body["type"] == "FeatureCollection"
    # The block covers rows 1..2 x cols 0..2 = six nodes.
    assert len(body["features"]) == 6
    # Course 1 (3 wet hours) + course 2 (1 wet hour); every hour is valid.
    assert sum(f["properties"]["wet_hours"] for f in body["features"]) == 4
    missing = next(
        feature
        for feature in body["features"]
        if feature["geometry"]["coordinates"]
        == [float(GRID_LON[1, 2]), float(GRID_LAT[1, 2])]
    )
    assert missing["properties"] == {
        "wet_hours": 0,
        "valid_hours": 0,
        "wet_fraction": None,
    }
    valid = [feature for feature in body["features"] if feature is not missing]
    assert all(feature["properties"]["valid_hours"] == HOURS for feature in valid)
    assert all(feature["properties"]["wet_fraction"] is not None for feature in valid)


def test_wetness_rejects_invalid_ranges_and_thresholds(client: TestClient) -> None:
    reversed_dates = client.get(
        "/api/wetness", params={"start": "2025-07-16", "end": START}
    )
    negative_threshold = client.get("/api/wetness", params={"threshold": "-1"})
    infinite_threshold = client.get("/api/wetness", params={"threshold": "inf"})

    assert reversed_dates.status_code == 422
    assert negative_threshold.status_code == 422
    assert infinite_threshold.status_code == 422


def test_wetness_clamps_selected_dates_to_coverage(client: TestClient) -> None:
    body = client.get(
        "/api/wetness",
        params={"start": START, "end": START, "threshold": 0.05},
    ).json()

    assert (
        sum(feature["properties"]["valid_hours"] for feature in body["features"])
        == 5 * HOURS
    )


def test_config_defaults_to_local_frontend_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TSTORM_CLIMO_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("TSTORM_CLIMO_ARTIFACT", raising=False)
    monkeypatch.delenv("TSTORM_CLIMO_GRIDDED", raising=False)

    settings = Settings.from_env()

    assert settings.cors_origins == DEFAULT_CORS_ORIGINS
    assert "*" not in settings.cors_origins


def test_config_reads_trimmed_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTORM_CLIMO_ARTIFACT", "/tmp/observations")
    monkeypatch.setenv("TSTORM_CLIMO_GRIDDED", "/tmp/gridded")
    monkeypatch.setenv(
        "TSTORM_CLIMO_CORS_ORIGINS", " https://app.example, http://localhost:3000 "
    )

    settings = Settings.from_env()

    assert settings.artifact_dir == "/tmp/observations"
    assert settings.gridded_dir == "/tmp/gridded"
    assert settings.cors_origins == ("https://app.example", "http://localhost:3000")

"""Tests for the v3 query layer over a built observation artifact."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from tstorm_climo.artifacts import (
    MAX_SELECTED_PLACES,
    OBSERVATIONS_DIR,
    build_hourly_observation_artifact,
    load_observation_artifact,
)
from tstorm_climo.extract import RATE_COLUMN, TIME_COLUMN
from tstorm_climo.places import PLACE_KEY_COLUMN, Place
from tstorm_climo.query import (
    artifact_report,
    build_wetness_summary,
    matching_row_groups,
    select_all_observations,
    select_observations,
    selected_places,
)
from tstorm_climo.summarize import place_summary

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
        osm_type="way",  # polygon over node
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
def artifact(tmp_path: Path):
    output = tmp_path / "artifact"
    build_hourly_observation_artifact(
        PLACES, START, "2025-07-15", output, opener=make_opener(), shard_count=2
    )
    return load_observation_artifact(output)


def test_select_observations_returns_only_the_selected_places(artifact) -> None:
    keys = ["osm:way:1", "osm:way:2"]
    frame = select_observations(artifact, keys)

    assert set(frame[PLACE_KEY_COLUMN].unique().to_list()) == set(keys)
    assert frame.height == 2 * HOURS
    # The static columns are joined back for the summary layer.
    assert RATE_COLUMN in frame.columns
    assert "tz_name" in frame.columns
    assert "sampling_key" in frame.columns
    assert "value" not in frame.columns


def test_select_observations_enforces_the_place_cap(artifact) -> None:
    too_many = [f"osm:way:{osm_id}" for osm_id in range(MAX_SELECTED_PLACES + 1)]
    with pytest.raises(ValueError, match="at most"):
        select_observations(artifact, too_many)


def test_select_observations_requires_at_least_one_place(artifact) -> None:
    with pytest.raises(ValueError, match="at least one"):
        select_observations(artifact, [])


def test_select_observations_can_filter_to_a_time_window(artifact) -> None:
    start = datetime.fromisoformat("2025-07-15T00:00:00+00:00")
    end = datetime.fromisoformat("2025-07-15T01:00:00+00:00")
    frame = select_observations(artifact, ["osm:way:1"], start=start, end=end)

    assert frame.height == 2
    assert frame[TIME_COLUMN].dt.hour().to_list() == [0, 1]


def test_the_served_selection_feeds_the_summary_layer(artifact) -> None:
    """The slim write + join-back round-trips to the same climatology."""
    keys = ["osm:way:1", "osm:way:2"]
    frame = select_observations(artifact, keys)
    summary = place_summary(frame, selected_places(artifact, keys))

    # Drier first: course 2 (1 wet hour) before course 1 (3 wet hours).
    assert summary["name"].to_list() == ["Course 2", "Course 1"]
    assert summary["wet_fraction"].to_list() == pytest.approx([1 / HOURS, 3 / HOURS])


def test_select_all_observations_filters_by_kind(artifact) -> None:
    frame = select_all_observations(artifact, kinds=["golf_course"])

    assert set(frame[PLACE_KEY_COLUMN].unique().to_list()) == {
        "osm:way:1",
        "osm:way:2",
    }
    assert frame.height == 2 * HOURS


def test_artifact_report_ranks_the_wetter_course_first(artifact) -> None:
    report = artifact_report(artifact, ["osm:way:1", "osm:way:2"])

    assert report.summary["name"].to_list() == ["Course 2", "Course 1"]
    assert report.summary["wet_fraction"].to_list() == pytest.approx(
        [1 / HOURS, 3 / HOURS]
    )
    assert report.monthly.height == 2
    assert report.reliability is not None


def test_wetness_summary_is_one_row_per_place_with_coordinates(artifact) -> None:
    summary = build_wetness_summary(artifact)

    assert summary.height == len(PLACES)
    assert "lat" in summary.columns
    assert "lon" in summary.columns
    assert set(summary[PLACE_KEY_COLUMN].to_list()) == {p.place_key for p in PLACES}
    # The beach is driest, then course 2, then course 1.
    assert summary["name"].to_list() == ["A Beach", "Course 2", "Course 1"]


def test_wetness_summary_can_restrict_to_a_trailing_window(artifact) -> None:
    # A two-hour trailing window ends at hour 5, where course 1 was wet
    # once (hour 4) and course 2 not at all.
    summary = build_wetness_summary(artifact, trailing=timedelta(hours=2))
    by_name = dict(
        zip(summary["name"].to_list(), summary["wet_hours"].to_list(), strict=True)
    )
    assert by_name["Course 1"] == 1
    assert by_name["Course 2"] == 0


def test_pushdown_reads_only_the_selected_places_row_groups(tmp_path: Path) -> None:
    """A small selection must not touch the rest of the row groups."""
    many = [course(osm_id, 1, 1) for osm_id in range(1, 41)]  # 40 places, one node
    output = tmp_path / "artifact"
    build_hourly_observation_artifact(
        many,
        START,
        "2025-07-15",
        output,
        opener=make_opener(),
        shard_count=8,
        row_group_size=HOURS,
    )

    selected = ["osm:way:3", "osm:way:17", "osm:way:29"]
    report = matching_row_groups(output / OBSERVATIONS_DIR, selected)

    # 40 places -> 40 row groups (one per place); only the 3 overlap.
    assert report.total_row_groups == 40
    assert report.matched_row_groups == 3
    assert report.group_fraction == pytest.approx(3 / 40)
    assert report.matched_bytes < report.total_bytes

"""Tests for the per-place climatology summary, on synthetic data."""

from __future__ import annotations

from datetime import UTC

import numpy as np
import polars as pl
import pytest

from tstorm_climo.extract import RATE_COLUMN, TIME_COLUMN, VALID_COLUMN
from tstorm_climo.places import Place
from tstorm_climo.summarize import (
    DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
    add_local_time,
    by_local_hour,
    by_local_month,
    place_summary,
    scorecard,
    split_half_reliability,
    wet_rows,
)

# Hours in a day, so synthetic series are easy to reason about.
H = 24


def place(idx: int, tz: str = "America/New_York", kind: str = "golf_course") -> Place:
    return Place(
        osm_type="way",
        osm_id=idx,
        name=f"Course {idx}",
        kind=kind,
        lat=42.0 - 0.03 * idx,
        lon=-70.9,
        timezone=tz,
    )


def make_series(
    rates: list[list[float]],
    *,
    valid: list[list[bool]] | None = None,
    zones: list[str] | None = None,
    start_hour: int = 0,
    day: str = "2025-07-15",
) -> pl.DataFrame:
    """Build a long-format series: rates[place][hour] in mm/h."""
    n_places = len(rates)
    n_hours = len(rates[0])
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(f"{day}T00:00").replace(tzinfo=UTC)
    times = [
        base + timedelta(hours=h) for h in range(start_hour, start_hour + n_hours)
    ]
    zone_list = zones or ["America/New_York"] * n_places

    rows = {
        "place_id": [],
        TIME_COLUMN: [],
        RATE_COLUMN: [],
        VALID_COLUMN: [],
        "y_index": [],
        "x_index": [],
        "grid_lat": [],
        "grid_lon": [],
        "snap_distance_km": [],
        "tz_name": [],
    }
    for p in range(n_places):
        for h in range(n_hours):
            rows["place_id"].append(p)
            rows[TIME_COLUMN].append(times[h])
            rows[RATE_COLUMN].append(rates[p][h])
            rows[VALID_COLUMN].append(
                True if valid is None else valid[p][h]
            )
            rows["y_index"].append(p)
            rows["x_index"].append(0)
            rows["grid_lat"].append(42.0)
            rows["grid_lon"].append(-70.9)
            rows["snap_distance_km"].append(1.0)
            rows["tz_name"].append(zone_list[p])
    return pl.DataFrame(
        rows,
        schema={
            "place_id": pl.UInt32,
            TIME_COLUMN: pl.Datetime("us", "UTC"),
            RATE_COLUMN: pl.Float64,
            VALID_COLUMN: pl.Boolean,
            "y_index": pl.Int64,
            "x_index": pl.Int64,
            "grid_lat": pl.Float64,
            "grid_lon": pl.Float64,
            "snap_distance_km": pl.Float64,
            "tz_name": pl.String,
        },
    )


def test_wet_rows_drops_invalid_and_marks_wet() -> None:
    series = make_series([[0.0, 1.0]], valid=[[True, False]])
    wet = wet_rows(series, 0.5)
    assert wet.height == 1
    assert wet["wet"].to_list() == [False]


def test_wet_threshold_is_exclusive() -> None:
    series = make_series([[0.5, 0.51]])
    wet = wet_rows(series, 0.5)
    assert wet["wet"].to_list() == [False, True]


def test_default_threshold_is_half_mm_per_hour() -> None:
    assert DEFAULT_WET_THRESHOLD_MM_PER_HOUR == 0.5


def test_place_summary_counts_wet_hours_and_fraction() -> None:
    series = make_series([[0.0, 1.0, 2.0, 0.0]])
    out = place_summary(series, [place(0)])
    row = out.row(0, named=True)
    assert row["valid_hours"] == 4
    assert row["wet_hours"] == 2
    assert row["wet_fraction"] == pytest.approx(0.5)


def test_invalid_hours_are_excluded_from_the_denominator() -> None:
    """A gap must shrink valid_hours, not become a dry hour."""
    series = make_series(
        [[0.0, 1.0, 2.0, 0.0]], valid=[[True, True, False, True]]
    )
    row = place_summary(series, [place(0)]).row(0, named=True)
    assert row["steps"] == 4
    assert row["invalid_hours"] == 1
    assert row["valid_hours"] == 3
    assert row["wet_fraction"] == pytest.approx(1 / 3)


def test_coverage_reports_data_quality() -> None:
    series = make_series([[0.0, 0.0]], valid=[[True, False]])
    row = place_summary(series, [place(0)]).row(0, named=True)
    assert row["coverage"] == pytest.approx(0.5)


def test_rain_mm_sums_rates_as_depth() -> None:
    """Each step is one hour, so mm/h sums to mm."""
    series = make_series([[1.0, 2.0, 0.0]])
    row = place_summary(series, [place(0)]).row(0, named=True)
    assert row["rain_mm"] == pytest.approx(3.0)


def test_drier_places_rank_first() -> None:
    series = make_series(
        [
            [0.0] * 4,  # dry
            [1.0, 1.0, 0.0, 0.0],  # half wet
            [1.0] * 4,  # always wet
        ]
    )
    out = place_summary(series, [place(i) for i in range(3)])
    order = out["name"].to_list()
    assert order == ["Course 0", "Course 1", "Course 2"]
    assert out["rank"].to_list() == [1, 2, 3]


def test_shared_grid_node_is_flagged_as_tied() -> None:
    """Colocated places get identical numbers and must be marked."""
    series = make_series([[1.0, 0.0], [1.0, 0.0]])
    # Force both places onto the same node, as locate_places would.
    series = series.with_columns(pl.lit(7).alias("y_index"))
    out = place_summary(series, [place(0), place(1)])
    assert out["tied"].to_list() == [True, True]
    assert out["cell_places"].to_list() == [2, 2]
    assert out["wet_hours"].to_list() == [1, 1]


def test_distinct_nodes_are_not_tied() -> None:
    series = make_series([[1.0, 0.0], [1.0, 0.0]])
    out = place_summary(series, [place(0), place(1)])
    assert out["tied"].to_list() == [False, False]
    assert out["cell_places"].to_list() == [1, 1]


def test_identical_wet_fraction_shares_a_dense_rank() -> None:
    series = make_series([[1.0, 0.0], [1.0, 0.0]])
    out = place_summary(series, [place(0), place(1)])
    assert out["rank"].to_list() == [1, 1]


def test_local_time_converts_from_utc() -> None:
    series = make_series([[0.0] * H])
    local = add_local_time(series)
    # 00Z is 20:00 the previous day in New York (EDT, UTC-4).
    assert local["local_hour"].to_list()[:4] == [20, 21, 22, 23]
    assert local["local_month"].to_list()[0] == 7
    assert str(local["local_day"][0]) == "2025-07-14"


def test_local_time_handles_mixed_zones() -> None:
    series = make_series(
        [[0.0] * H, [0.0] * H],
        zones=["America/New_York", "America/Los_Angeles"],
    )
    local = add_local_time(series)
    ny = local.filter(pl.col("place_id") == 0)["local_hour"].to_list()[0]
    la = local.filter(pl.col("place_id") == 1)["local_hour"].to_list()[0]
    assert ny == 20  # UTC-4
    assert la == 17  # UTC-7


def test_local_time_respects_daylight_saving() -> None:
    """Same UTC hour, different local hour across a DST boundary."""
    winter = add_local_time(make_series([[0.0] * 2], day="2025-01-15"))
    summer = add_local_time(make_series([[0.0] * 2], day="2025-07-15"))
    assert winter["local_hour"].to_list() == [19, 20]  # EST, UTC-5
    assert summer["local_hour"].to_list() == [20, 21]  # EDT, UTC-4


def test_by_local_month_slices_by_calendar_month() -> None:
    june = make_series([[1.0, 0.0]], day="2025-06-15")
    july = make_series([[1.0, 0.0]], day="2025-07-15")
    combined = pl.concat([june, july])
    out = by_local_month(combined, [place(0)])
    assert out["local_month"].to_list() == [6, 7]
    assert out["wet_fraction"].to_list() == pytest.approx([0.5, 0.5])


def test_by_local_hour_normalizes_by_observations() -> None:
    """wet_fraction must divide by hours observed, not by 24."""
    series = make_series([[1.0] * 6 + [0.0] * 18])
    out = by_local_hour(series, [place(0)])
    total = out["hours_observed"].sum()
    assert total == H
    assert out["wet_hours"].sum() == 6
    # Six hours each have fraction 1.0; the other eighteen have 0.0.
    # Each hour is observed once, so per-hour fraction equals its wet
    # indicator here -- the point is that the denominator is
    # hours_observed, so the fractions sum to wet_hours, not to 1.
    assert out["wet_fraction"].sum() == pytest.approx(6.0)
    assert out["wet_fraction"].max() == pytest.approx(1.0)


def test_by_local_hour_fraction_respects_uneven_observations() -> None:
    """A local hour seen 12 times and wet 6 times is 0.5, not 1.0."""
    series = pl.concat(
        [
            make_series([[1.0, 0.0, 0.0, 0.0]], day="2025-07-15"),
            make_series([[0.0] * 4], day="2025-07-16"),
        ]
    )
    out = by_local_hour(series, [place(0)])
    assert out["hours_observed"].to_list() == [2, 2, 2, 2]
    assert out["wet_hours"].to_list() == [1, 0, 0, 0]
    assert out["wet_fraction"].to_list() == pytest.approx([0.5, 0.0, 0.0, 0.0])


def test_by_local_hour_covers_all_twenty_four_hours() -> None:
    out = by_local_hour(make_series([[0.0] * H]), [place(0)])
    assert sorted(out["local_hour"].to_list()) == list(range(24))


def test_scorecard_filters_kind_and_ranks_driest_first() -> None:
    places = [place(0), place(1, kind="beach"), place(2)]
    series = make_series([[1.0] * 4, [1.0] * 4, [0.0] * 4])
    summary = place_summary(series, places)
    card = scorecard(summary)
    assert card["name"].to_list() == ["Course 2", "Course 0"]


def test_scorecard_can_filter_to_one_month() -> None:
    places = [place(0)]
    series = pl.concat(
        [
            make_series([[1.0, 1.0]], day="2025-06-15"),
            make_series([[0.0, 0.0]], day="2025-07-15"),
        ]
    )
    monthly = by_local_month(series, places)
    assert scorecard(monthly, month=7)["wet_fraction"].to_list() == [0.0]
    assert scorecard(monthly, month=6)["wet_fraction"].to_list() == [1.0]


def test_empty_series_produces_empty_summary() -> None:
    series = make_series([[0.0] * 2]).filter(pl.col("place_id") > 99)
    out = place_summary(series, [place(0)])
    assert out.height == 0

def test_split_half_reliability_high_for_a_real_signal() -> None:
    """Places that genuinely differ must reproduce across halves."""
    rates = [[1.0] * 12] * 5 + [[0.0] * 12] * 5
    series = make_series(rates)
    assert split_half_reliability(series) == pytest.approx(1.0, abs=1e-9)


def test_split_half_reliability_low_for_pure_noise() -> None:
    """Coin-flip rain must not produce a reproducible ordering."""
    import random

    rng = random.Random(0)
    rates = [[rng.choice([0.0, 1.0]) for _ in range(12)] for _ in range(40)]
    series = make_series(rates)
    r = split_half_reliability(series)
    assert abs(r) < 0.6


def test_split_half_reliability_is_nan_without_variation() -> None:
    """All places identical leaves nothing to correlate."""
    series = make_series([[0.0] * 12] * 6)
    assert np.isnan(split_half_reliability(series))


def test_split_half_reliability_nan_for_a_single_place() -> None:
    series = make_series([[0.0, 1.0, 0.0, 1.0]])
    assert np.isnan(split_half_reliability(series))

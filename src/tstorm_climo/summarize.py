"""Per-place precipitation climatology.

Turns the long-format series from :mod:`tstorm_climo.extract` into the
per-place tables that answer "how often does it rain here, and when".

Three things this layer is careful about:

- **Archive gaps are not dry weather.** Rows are filtered on the
  ``valid`` flag before counting, and the valid fraction is reported
  alongside every result so a thin period is visible rather than
  quietly biasing a wet-hour count low.
- **Local time, by name.** A golf round happens on the clock, not on
  UTC, and each place carries its own IANA zone. Zones are converted by
  partitioning on ``tz_name`` and converting each group, because polars'
  ``convert_time_zone`` takes a static string rather than a column. Local
  hour and month come out as plain integers so the groups can be
  concatenated without a timezone-dtype conflict.
- **Co-located places are marked.** Places that share a 3 km grid node
  receive identical numbers; they are flagged rather than silently ranked
  against each other.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from tstorm_climo.extract import RATE_COLUMN, TIME_COLUMN, VALID_COLUMN
from tstorm_climo.places import UTC_TIMEZONE, Place

# Default "it actually rained on the course" threshold, in mm/h. The
# archive's `> 0` marks drizzle and any floating-point residue; 0.5 mm/h
# is a defensible "measurable rain" floor. Phase 2's sensitivity study
# is meant to confirm or revise this.
DEFAULT_WET_THRESHOLD_MM_PER_HOUR = 0.5


def add_local_time(series: pl.DataFrame) -> pl.DataFrame:
    """Attach local hour, month and day, converting per timezone group.

    Adds ``local_hour`` (0-23), ``local_month`` (1-12) and ``local_day``
    (a date) derived from each place's own IANA zone. Daylight saving is
    handled by the zone names, so a summer hour and a winter hour
    holding the same UTC timestamp land in different local hours.

    A daylight-saving transition makes one local day 23 hours and
    another 25; that is correct, and the slot counts below reflect it.
    """
    if series.height == 0:
        return series.with_columns(
            local_hour=pl.Series([], dtype=pl.Int64),
            local_month=pl.Series([], dtype=pl.Int64),
            local_day=pl.Series([], dtype=pl.Date),
        )
    zones = series.with_columns(pl.col("tz_name").fill_null(UTC_TIMEZONE))
    parts = []
    for key, part in zones.partition_by("tz_name", as_dict=True).items():
        zone = key[0] if isinstance(key, tuple) else key
        local = pl.col(TIME_COLUMN).dt.convert_time_zone(zone)
        parts.append(
            part.with_columns(
                local_hour=local.dt.hour().cast(pl.Int64),
                local_month=local.dt.month().cast(pl.Int64),
                local_day=local.dt.date(),
            )
        )
    return pl.concat(parts)


def wet_rows(
    series: pl.DataFrame, threshold_mm_per_hour: float
) -> pl.DataFrame:
    """Drop invalid rows and mark wet ones against a mm/h threshold."""
    return series.filter(pl.col(VALID_COLUMN)).with_columns(
        wet=pl.col(RATE_COLUMN) > threshold_mm_per_hour
    )


def place_summary(
    series: pl.DataFrame,
    places: Sequence[Place],
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> pl.DataFrame:
    """Summarize rain frequency and depth for every place.

    Args:
        series: Long-format extraction output.
        places: The places, in the same order as ``series.place_id``.
        threshold_mm_per_hour: Rate above which an hour counts as wet.

    Returns:
        One row per place, with ``valid_hours``/``coverage`` (data
        quality), ``wet_hours``/``wet_fraction`` (rain frequency),
        ``rain_mm`` (accumulated depth; each step is one hour, so mm/h
        sums to mm), ``tied``/``cell_places`` (grid-resolution
        collisions) and a dense ``rank`` where 1 is driest.
    """
    keyed = _with_places(series, places)
    wet = wet_rows(keyed, threshold_mm_per_hour)

    totals = keyed.group_by("place_id").agg(
        steps=pl.len(), invalid_hours=pl.col(VALID_COLUMN).not_().sum()
    )
    stats = wet.group_by("place_id").agg(
        valid_hours=pl.len(),
        wet_hours=pl.col("wet").sum(),
        rain_mm=pl.col(RATE_COLUMN).sum(),
    )

    out = (
        keyed.group_by("place_id")
        .agg(
            pl.col("name").first(),
            pl.col("kind").first(),
            pl.col("timezone").first(),
            pl.col("y_index").first(),
            pl.col("x_index").first(),
            pl.col("snap_distance_km").first(),
        )
        .join(totals, on="place_id")
        .join(stats, on="place_id")
        .with_columns(
            wet_fraction=pl.col("wet_hours") / pl.col("valid_hours"),
            coverage=pl.col("valid_hours") / pl.col("steps"),
        )
    )

    out = out.with_columns(
        cell_places=pl.len().over("y_index", "x_index")
    ).with_columns(tied=pl.col("cell_places") > 1)

    return out.with_columns(
        rank=pl.col("wet_fraction").rank("dense").cast(pl.Int64)
    ).sort("wet_fraction")


def by_local_month(
    series: pl.DataFrame,
    places: Sequence[Place],
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> pl.DataFrame:
    """Wet frequency per place per calendar month, in local time."""
    wet = wet_rows(_with_places(series, places), threshold_mm_per_hour)
    local = add_local_time(wet)
    return (
        local.group_by("place_id", "local_month")
        .agg(
            name=pl.col("name").first(),
            kind=pl.col("kind").first(),
            valid_hours=pl.len(),
            wet_hours=pl.col("wet").sum(),
            rain_mm=pl.col(RATE_COLUMN).sum(),
        )
        .with_columns(
            wet_fraction=pl.col("wet_hours") / pl.col("valid_hours")
        )
        .sort("place_id", "local_month")
    )


def by_local_hour(
    series: pl.DataFrame,
    places: Sequence[Place],
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> pl.DataFrame:
    """Wet frequency per place per local hour of day.

    ``hours_observed`` is how many times that local hour occurred in
    the window, which is not constant across months or across
    daylight-saving transitions. ``wet_fraction`` is wet hours divided
    by that, so 0.5 means "half the time this hour came round, it was
    raining" regardless of how often it came round.
    """
    wet = wet_rows(_with_places(series, places), threshold_mm_per_hour)
    local = add_local_time(wet)
    return (
        local.group_by("place_id", "local_hour")
        .agg(
            name=pl.col("name").first(),
            kind=pl.col("kind").first(),
            hours_observed=pl.len(),
            wet_hours=pl.col("wet").sum(),
            rain_mm=pl.col(RATE_COLUMN).sum(),
        )
        .with_columns(
            wet_fraction=pl.col("wet_hours") / pl.col("hours_observed")
        )
        .sort("place_id", "local_hour")
    )


def scorecard(
    summary: pl.DataFrame,
    *,
    kind: str = "golf_course",
    month: int | None = None,
) -> pl.DataFrame:
    """Rank a summary table, optionally restricted to one month.

    Args:
        summary: Output of :func:`place_summary` or
            :func:`by_local_month`.
        kind: Place kind to keep, e.g. ``golf_course``.
        month: For a month-sliced summary, the calendar month (1-12) in
            local time. Ignored when ``summary`` is not month-sliced.

    Returns:
        Places of that kind, driest first, with tied places kept
        adjacent so the shared grid node is obvious.
    """
    out = summary.filter(pl.col("kind") == kind)
    if month is not None and "local_month" in out.columns:
        out = out.filter(pl.col("local_month") == month)
    # Tied places are kept adjacent so the shared grid node is obvious,
    # but a month-sliced table has no node columns, so sort on whatever
    # is actually present.
    keys = [
        c
        for c in ("wet_fraction", "y_index", "x_index", "name")
        if c in out.columns
    ]
    return out.sort(keys)


def _with_places(
    series: pl.DataFrame, places: Sequence[Place]
) -> pl.DataFrame:
    """Attach name/kind/timezone to each row, keyed by ``place_id``."""
    table = pl.DataFrame(
        {
            "place_id": pl.Series(range(len(places)), dtype=pl.UInt32),
            "name": [p.display_name for p in places],
            "kind": [p.kind for p in places],
            "timezone": [p.timezone for p in places],
        }
    )
    # Join unconditionally, even on an empty series: a left join keeps
    # every row, so an empty input stays empty and still carries the
    # name/kind columns the aggregations below reference.
    return series.join(table, on="place_id", how="left")
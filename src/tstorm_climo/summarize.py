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
- **Co-located places are marked.** Places sampled from the *same set
  of grid nodes* receive identical numbers; they are flagged rather than
  silently ranked against each other. The identity is the sampled node
  set (``sampling_key``), not the place's anchor node -- once a
  footprint is averaged the anchor no longer describes what was read.
  Two places sharing only some nodes are coupled but distinguishable,
  so they are not marked tied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl

from tstorm_climo.extract import (
    N_CELLS_COLUMN,
    RATE_COLUMN,
    SAMPLING_COLUMN,
    SAMPLING_KEY_COLUMN,
    TIME_COLUMN,
    VALID_COLUMN,
)
from tstorm_climo.places import PLACE_KEY_COLUMN, UTC_TIMEZONE, Place
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR


@dataclass(frozen=True)
class PreparedObservations:
    """Observations prepared once for every summary that needs them.

    Joining the place metadata, converting to local time, and marking
    the wet hours are each a full pass over the frame, and a report
    wants all three. Doing them once and sharing the result is the
    difference between a report that takes a second and one that takes
    several.

    Attributes:
        keyed: Every observation row, joined to its place, with the
            sampling columns and the local hour/month/day.
        wet: The valid rows of :attr:`keyed`, with the ``wet`` flag
            against the chosen threshold.
    """

    keyed: pl.DataFrame
    wet: pl.DataFrame

    def summary(self) -> pl.DataFrame:
        """One row per place: wet frequency, depth, coverage, and ties."""
        keyed = self.keyed
        totals = keyed.group_by(PLACE_KEY_COLUMN).agg(
            steps=pl.len(), invalid_hours=pl.col(VALID_COLUMN).not_().sum()
        )
        stats = self.wet.group_by(PLACE_KEY_COLUMN).agg(
            valid_hours=pl.len(),
            wet_hours=pl.col("wet").sum(),
            rain_mm=pl.col(RATE_COLUMN).sum(),
        )

        out = (
            keyed.group_by(PLACE_KEY_COLUMN)
            .agg(
                pl.col("name").first(),
                pl.col("kind").first(),
                pl.col("timezone").first(),
                pl.col("y_index").first(),
                pl.col("x_index").first(),
                pl.col("snap_distance_km").first(),
                pl.col(SAMPLING_COLUMN).first(),
                pl.col(N_CELLS_COLUMN).first(),
                pl.col(SAMPLING_KEY_COLUMN).first(),
            )
            .join(totals, on=PLACE_KEY_COLUMN)
            .join(stats, on=PLACE_KEY_COLUMN)
            .with_columns(
                wet_fraction=pl.col("wet_hours") / pl.col("valid_hours"),
                coverage=pl.col("valid_hours") / pl.col("steps"),
            )
        )

        # Identity of the sampled set, not of the anchor node. See the Note.
        out = out.with_columns(
            cell_places=pl.len().over(SAMPLING_KEY_COLUMN)
        ).with_columns(tied=pl.col("cell_places") > 1)

        # The tie-break on place_key matters: grouping does not promise
        # a row order, so sorting on wet_fraction alone would shuffle
        # equally-wet places from one run to the next. A published
        # leaderboard has to come out the same every time.
        return out.with_columns(
            rank=pl.col("wet_fraction").rank("dense").cast(pl.Int64)
        ).sort("wet_fraction", PLACE_KEY_COLUMN)

    def monthly(self) -> pl.DataFrame:
        """Wet frequency per place per calendar month, in local time."""
        return (
            self.wet.group_by(PLACE_KEY_COLUMN, "local_month")
            .agg(
                name=pl.col("name").first(),
                kind=pl.col("kind").first(),
                valid_hours=pl.len(),
                wet_hours=pl.col("wet").sum(),
                rain_mm=pl.col(RATE_COLUMN).sum(),
            )
            .with_columns(wet_fraction=pl.col("wet_hours") / pl.col("valid_hours"))
            .sort(PLACE_KEY_COLUMN, "local_month")
        )

    def hourly(self) -> pl.DataFrame:
        """Wet frequency per place per local hour of day.

        ``hours_observed`` is how many times that local hour occurred in
        the window, which is not constant across months or across
        daylight-saving transitions. ``wet_fraction`` is wet hours divided
        by that, so 0.5 means "half the time this hour came round, it was
        raining" regardless of how often it came round.
        """
        return (
            self.wet.group_by(PLACE_KEY_COLUMN, "local_hour")
            .agg(
                name=pl.col("name").first(),
                kind=pl.col("kind").first(),
                hours_observed=pl.len(),
                wet_hours=pl.col("wet").sum(),
                rain_mm=pl.col(RATE_COLUMN).sum(),
            )
            .with_columns(wet_fraction=pl.col("wet_hours") / pl.col("hours_observed"))
            .sort(PLACE_KEY_COLUMN, "local_hour")
        )

    def reliability(self) -> float:
        """Split-half reliability of the ranking over this prepared frame."""
        return _split_half_reliability(self.wet)


def prepare_observations(
    series: pl.DataFrame,
    places: Sequence[Place],
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> PreparedObservations:
    """Join, localize, and threshold an observation series once.

    The result feeds :meth:`PreparedObservations.summary`,
    :meth:`~PreparedObservations.monthly`,
    :meth:`~PreparedObservations.hourly`, and
    :meth:`~PreparedObservations.reliability` without repeating the same
    full-frame work.

    Args:
        series: Long-format extraction output.
        places: Place records corresponding to the series' ``place_key``
            values.
        threshold_mm_per_hour: Rate above which an hour counts as wet.

    Returns:
        The prepared frame, ready for the summary functions.
    """
    keyed = add_local_time(_with_places(series, places))
    wet = keyed.filter(pl.col(VALID_COLUMN)).with_columns(
        wet=pl.col(RATE_COLUMN) > threshold_mm_per_hour
    )
    return PreparedObservations(keyed=keyed, wet=wet)


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


def wet_rows(series: pl.DataFrame, threshold_mm_per_hour: float) -> pl.DataFrame:
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
        places: Place records corresponding to the series' ``place_key``
            values. Their order does not matter.
        threshold_mm_per_hour: Rate above which an hour counts as wet.

    Returns:
        One row per place, with ``valid_hours``/``coverage`` (data
        quality), ``wet_hours``/``wet_fraction`` (rain frequency),
        ``rain_mm`` (accumulated depth; each step is one hour, so mm/h
        sums to mm), ``tied``/``cell_places`` (grid-resolution
        collisions) and a dense ``rank`` where 1 is driest.

    Note:
        ``cell_places`` counts places whose sampled node **sets** are
        identical, keyed on ``sampling_key`` rather than the anchor
        node. Two places sharing a node set receive literally the same
        number, so they cannot be ranked against each other. Places
        sharing only some nodes are *not* counted as tied -- they are
        coupled but not indistinguishable. For point-sampled places
        ``sampling_key`` is a single node and this reduces to the old
        anchor-node count exactly.
    """
    return prepare_observations(
        series, places, threshold_mm_per_hour=threshold_mm_per_hour
    ).summary()


def by_local_month(
    series: pl.DataFrame,
    places: Sequence[Place],
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> pl.DataFrame:
    """Wet frequency per place per calendar month, in local time."""
    return prepare_observations(
        series, places, threshold_mm_per_hour=threshold_mm_per_hour
    ).monthly()


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
    return prepare_observations(
        series, places, threshold_mm_per_hour=threshold_mm_per_hour
    ).hourly()


def scorecard(
    summary: pl.DataFrame,
    *,
    kind: str | None = None,
    month: int | None = None,
) -> pl.DataFrame:
    """Rank a summary table, optionally restricted to one kind or month.

    Args:
        summary: Output of :func:`place_summary` or
            :func:`by_local_month`.
        kind: Place kind to keep, e.g. ``golf_course``. ``None`` keeps
            every kind; the caller is responsible for any pre-filtering.
        month: For a month-sliced summary, the calendar month (1-12) in
            local time. Ignored when ``summary`` is not month-sliced.

    Returns:
        Places, driest first, with tied places kept adjacent so the
        shared grid node is obvious.
    """
    out = summary.filter(pl.col("kind") == kind) if kind is not None else summary
    if month is not None and "local_month" in out.columns:
        out = out.filter(pl.col("local_month") == month)
    # Tied places are kept adjacent so the shared grid node is obvious,
    # but a month-sliced table has no node columns, so sort on whatever
    # is actually present. ``place_key`` breaks any remaining tie, so
    # the same data always prints in the same order.
    keys = [
        c
        for c in ("wet_fraction", "y_index", "x_index", "name", PLACE_KEY_COLUMN)
        if c in out.columns
    ]
    return out.sort(keys)


def split_half_reliability(
    series: pl.DataFrame,
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> float:
    """How stable is the between-place ranking across the record?

    Splits the time range in half, computes each place's wet fraction in
    each half, and correlates the two vectors. A value near 1 means
    place-to-place differences are real and reproducible; near 0 means
    the ordering is sampling noise and would reorder on a different date
    range. This is the check that decides whether a leaderboard is worth
    publishing.

    Args:
        series: Long-format extraction output.
        threshold_mm_per_hour: Rate above which an hour counts as wet.

    Returns:
        Pearson correlation of the two halves, or ``nan`` if either half
        has too little variation to correlate.
    """
    wet = wet_rows(series, threshold_mm_per_hour)
    return _split_half_reliability(wet)


def _split_half_reliability(wet: pl.DataFrame) -> float:
    """Correlate each place's wet fraction in the two halves of a range."""
    if wet.height == 0:
        return float("nan")

    n_places = wet[PLACE_KEY_COLUMN].n_unique()
    if n_places < 2:
        return float("nan")

    # Split on the time value, not row position: the long format is
    # place-major, so a head/tail cut would slice mid-place and drop
    # places from one half entirely.
    midpoint = wet[TIME_COLUMN].median()
    left = _wet_fractions(wet.filter(pl.col(TIME_COLUMN) <= midpoint))
    right = _wet_fractions(wet.filter(pl.col(TIME_COLUMN) > midpoint))
    if left is None or right is None:
        return float("nan")
    aligned = left.join(right, on=PLACE_KEY_COLUMN, how="inner", suffix="_right")
    if (
        aligned.height < 2
        or aligned["wet_fraction"].n_unique() < 2
        or aligned["wet_fraction_right"].n_unique() < 2
    ):
        return float("nan")
    return float(
        np.corrcoef(
            aligned["wet_fraction"].to_numpy(),
            aligned["wet_fraction_right"].to_numpy(),
        )[0, 1]
    )


def _wet_fractions(part: pl.DataFrame) -> pl.DataFrame | None:
    """Per-place wet fractions for one time block, keyed by place_key."""
    counts = (
        part.group_by(PLACE_KEY_COLUMN)
        .agg(wet=pl.col("wet").sum(), total=pl.len())
        .with_columns(wet_fraction=pl.col("wet") / pl.col("total"))
    )
    if counts["wet_fraction"].n_unique() < 2:
        return None
    return counts.select(PLACE_KEY_COLUMN, "wet_fraction")


def _with_places(series: pl.DataFrame, places: Sequence[Place]) -> pl.DataFrame:
    """Attach place metadata to each row, keyed by stable ``place_key``."""
    table = pl.DataFrame(
        {
            PLACE_KEY_COLUMN: pl.Series(
                [place.place_key for place in places], dtype=pl.String
            ),
            "name": [p.display_name for p in places],
            "kind": [p.kind for p in places],
            "timezone": [p.timezone for p in places],
        }
    )
    if table[PLACE_KEY_COLUMN].n_unique() != len(places):
        raise ValueError("places must have unique place_key values")
    # Join unconditionally, even on an empty series, so output schema and
    # metadata behavior do not depend on whether any rows were extracted.
    joined = series.join(table, on=PLACE_KEY_COLUMN, how="left")
    if joined.height and joined["name"].null_count():
        raise ValueError("series contains place_key values missing from places")
    return joined

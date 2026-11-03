"""Answer questions about a built observation artifact (schema v3).

The interactive app has two questions, and this module serves both:

- the opening map -- :func:`build_wetness_summary` produces one row per
  place, recomputed in batch; and
- a bounded drill-in -- :func:`select_observations` reads at most
  :data:`~tstorm_climo.artifacts.MAX_SELECTED_PLACES` places' hourly rows
  through a lazy ``scan_parquet`` whose ``place_key`` predicate is pushed
  down to Parquet row-group statistics.

Nothing here opens the archive. The only work a query does is reading the
already-prepared artifact (locally or from S3), so an app session costs no
archive reads.

Date bounds are inclusive, and a date-only bound covers the whole day --
``end 2025-07-15`` means the whole of 15 July, matching the extraction
path's slice semantics. A requested range is clamped to the artifact's
observed coverage, so asking for months the artifact does not hold returns
the covered months rather than an empty table.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from tstorm_climo.artifacts import (
    MAX_SELECTED_PLACES,
    PLACE_KEY_COLUMN,
    RAIN_RATE_VARIABLE,
    TIME_UTC_COLUMN,
    VALUE_COLUMN,
    VARIABLE_COLUMN,
    VARIABLE_VALUE_COLUMNS,
    ObservationArtifact,
)
from tstorm_climo.extract import RATE_COLUMN, TIME_COLUMN
from tstorm_climo.places import Place
from tstorm_climo.summarize import place_summary, prepare_observations
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR


@dataclass(frozen=True)
class ArtifactReport:
    """The tables a viewer needs for one selection of an artifact.

    Attributes:
        summary: One row per place: wet frequency, depth, coverage, and
            the tie flags.
        monthly: Wet frequency per place per local calendar month.
        hourly: Wet frequency per place per local hour of day.
        reliability: Split-half reliability of the ranking for the
            selected range and threshold, or ``nan`` when it cannot be
            computed.
        places: The places the report covers.
        start: Effective start of the range that was summarized.
        end: Effective end of the range that was summarized.
    """

    summary: pl.DataFrame
    monthly: pl.DataFrame
    hourly: pl.DataFrame
    reliability: float
    places: list[Place]
    start: datetime | None
    end: datetime | None


def selected_places(
    artifact: ObservationArtifact, place_keys: Sequence[str]
) -> list[Place]:
    """The places of an artifact matching a set of keys, in artifact order."""
    wanted = set(place_keys)
    return [place for place in artifact.places if place.place_key in wanted]


def select_observations(
    artifact: ObservationArtifact,
    place_keys: Sequence[str],
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    variable: str = RAIN_RATE_VARIABLE,
) -> pl.DataFrame:
    """Select one variable and a bounded set of places from an artifact.

    The ``place_key`` predicate is pushed down to Parquet row groups, so
    only the shards holding the selected places are read. The static
    per-place columns are joined back so the result can be handed straight
    to :mod:`tstorm_climo.summarize`.

    Args:
        artifact: The loaded artifact.
        place_keys: Stable keys of the places to read. At most
            :data:`~tstorm_climo.artifacts.MAX_SELECTED_PLACES`.
        start: Inclusive lower bound (date, timestamp, or ``None`` for the
            artifact's start).
        end: Inclusive upper bound; a date-only value covers the whole day.
        variable: The variable to read.

    Raises:
        ValueError: If more than
            :data:`~tstorm_climo.artifacts.MAX_SELECTED_PLACES` keys are
            requested, or no keys are given.
    """
    keys = list(place_keys)
    if not keys:
        raise ValueError("select_observations needs at least one place")
    if len(keys) > MAX_SELECTED_PLACES:
        raise ValueError(
            f"at most {MAX_SELECTED_PLACES} places may be selected at once; "
            f"got {len(keys)}"
        )
    return _scan(artifact, keys, start, end, variable)


def select_all_observations(
    artifact: ObservationArtifact,
    *,
    kinds: Sequence[str] | None = None,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    variable: str = RAIN_RATE_VARIABLE,
) -> pl.DataFrame:
    """Select every matching place, uncapped -- the batch path.

    Unlike :func:`select_observations` this imposes no place limit: it is
    for batch work (e.g. the ``rain-report --artifact`` CLI), not the
    interactive drill-in.
    """
    places = artifact.places
    if kinds is not None:
        wanted = set(kinds)
        places = [place for place in places if place.kind in wanted]
    return _scan(artifact, [place.place_key for place in places], start, end, variable)


def artifact_report(
    artifact: ObservationArtifact,
    place_keys: Sequence[str],
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    variable: str = RAIN_RATE_VARIABLE,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> ArtifactReport:
    """Summarize an artifact for one selection (summary, monthly, hourly).

    The selection is bounded by :func:`select_observations`, and the
    reliability figure is computed for the selected range and threshold so
    a caller cannot forget it.
    """
    frame = select_observations(
        artifact, place_keys, start=start, end=end, variable=variable
    )
    places = selected_places(artifact, place_keys)
    window_start, window_end = _resolve_range(artifact, start, end)

    prepared = prepare_observations(
        frame, places, threshold_mm_per_hour=threshold_mm_per_hour
    )
    return ArtifactReport(
        summary=prepared.summary(),
        monthly=prepared.monthly(),
        hourly=prepared.hourly(),
        reliability=prepared.reliability(),
        places=places,
        start=window_start,
        end=window_end,
    )


def build_wetness_summary(
    artifact: ObservationArtifact,
    *,
    threshold_mm_per_hour: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
    trailing: timedelta | None = None,
) -> pl.DataFrame:
    """One row per place: wetness over the artifact, for the opening map.

    This is the batch path, so it is a full scan of the hourly table
    rather than a bounded read. ``trailing`` restricts the window to the
    most recent period, e.g. the latest year. The result carries ``lat``
    and ``lon`` so it can be rendered directly.
    """
    frame = artifact.observations.filter(pl.col(VARIABLE_COLUMN) == RAIN_RATE_VARIABLE)
    frame = frame.collect().rename(
        {TIME_UTC_COLUMN: TIME_COLUMN, VALUE_COLUMN: RATE_COLUMN}
    )
    if trailing is not None:
        latest = frame.select(pl.col(TIME_COLUMN).max()).item()
        if latest is not None:
            cutoff = latest - trailing
            frame = frame.filter(pl.col(TIME_COLUMN) >= cutoff)

    frame = frame.join(artifact.static, on=PLACE_KEY_COLUMN, how="left")
    summary = place_summary(
        frame, artifact.places, threshold_mm_per_hour=threshold_mm_per_hour
    )
    coords = pl.DataFrame(
        [
            {PLACE_KEY_COLUMN: place.place_key, "lat": place.lat, "lon": place.lon}
            for place in artifact.places
        ]
    )
    return summary.join(coords, on=PLACE_KEY_COLUMN, how="left")


@dataclass(frozen=True)
class RowGroupReport:
    """How many observation row groups a place selection must read.

    ``matched_*`` is what a reader with predicate pushdown would fetch;
    ``total_*`` is the whole layout. The fraction is the headroom the
    place-clustered layout buys.
    """

    matched_row_groups: int
    total_row_groups: int
    matched_bytes: int
    total_bytes: int

    @property
    def group_fraction(self) -> float:
        return (
            self.matched_row_groups / self.total_row_groups
            if self.total_row_groups
            else 0.0
        )

    @property
    def byte_fraction(self) -> float:
        return self.matched_bytes / self.total_bytes if self.total_bytes else 0.0


def matching_row_groups(
    observations_dir: str | Path, place_keys: Sequence[str]
) -> RowGroupReport:
    """Report the row groups a place selection must read, from Parquet stats.

    This mirrors what the reader does without reading the data: it opens
    each shard's footer and uses the ``place_key`` min/max statistics to
    decide whether a row group can be skipped. It exists to *measure* the
    pushdown, not to serve data, so ``pyarrow`` is imported lazily.
    """
    import pyarrow.parquet as pq

    wanted = set(place_keys)
    total_groups = 0
    matched_groups = 0
    matched_bytes = 0
    total_bytes = 0
    for path in sorted(Path(observations_dir).glob("*.parquet")):
        parquet = pq.ParquetFile(path)
        place_key_index = parquet.schema_arrow.names.index(PLACE_KEY_COLUMN)
        for index in range(parquet.metadata.num_row_groups):
            row_group = parquet.metadata.row_group(index)
            total_groups += 1
            total_bytes += row_group.total_byte_size
            if _row_group_matches(row_group.column(place_key_index).statistics, wanted):
                matched_groups += 1
                matched_bytes += row_group.total_byte_size
    return RowGroupReport(matched_groups, total_groups, matched_bytes, total_bytes)


def _scan(
    artifact: ObservationArtifact,
    keys: Sequence[str],
    start: str | datetime | None,
    end: str | datetime | None,
    variable: str,
) -> pl.DataFrame:
    """The shared scan: push the predicates down, collect, and join static."""
    window_start, window_end = _resolve_range(artifact, start, end)
    lazy = artifact.observations.filter(pl.col(VARIABLE_COLUMN) == variable)
    lazy = lazy.filter(pl.col(PLACE_KEY_COLUMN).is_in(keys))
    if window_start is not None:
        lazy = lazy.filter(pl.col(TIME_UTC_COLUMN) >= window_start)
    if window_end is not None:
        lazy = lazy.filter(pl.col(TIME_UTC_COLUMN) <= window_end)

    frame = lazy.collect()
    value_column = VARIABLE_VALUE_COLUMNS[variable]
    frame = frame.rename({TIME_UTC_COLUMN: TIME_COLUMN, VALUE_COLUMN: value_column})

    static = artifact.static.filter(pl.col(PLACE_KEY_COLUMN).is_in(keys))
    return frame.join(static, on=PLACE_KEY_COLUMN, how="left")


def _resolve_range(
    artifact: ObservationArtifact,
    start: str | datetime | None,
    end: str | datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Resolve a requested range against the artifact's observed coverage.

    Date-only bounds cover a whole day, and the result is clamped to the
    artifact's coverage so a request for months it does not hold returns
    the covered part instead of nothing.
    """
    coverage_start, coverage_end = artifact.observed_start, artifact.observed_end
    window_start = _parse_bound(start, is_end=False) if start else coverage_start
    window_end = _parse_bound(end, is_end=True) if end else coverage_end

    if coverage_start is not None and window_start is not None:
        window_start = max(window_start, coverage_start)
    if coverage_end is not None and window_end is not None:
        window_end = min(window_end, coverage_end)
    return window_start, window_end


def _parse_bound(value: str | datetime, *, is_end: bool) -> datetime:
    """Parse a date or timestamp bound, expanding a date to a whole day.

    ``2025-07-15`` as a start becomes midnight, and as an end becomes the
    last instant of that day, so the range covers the day inclusive.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    text = value.strip()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        day = None
    if day is not None:
        instant = datetime(day.year, day.month, day.day, tzinfo=UTC)
        if is_end:
            return instant.replace(hour=23, minute=59, second=59, microsecond=999999)
        return instant
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(
            f"cannot read {value!r} as a date or timestamp; expected "
            "something like 2025-07-15 or 2025-07-15T12:00:00"
        ) from error
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _row_group_matches(stats: Any, wanted: set[str]) -> bool:
    """Whether a row group's ``place_key`` range can contain any wanted key."""
    if stats is None or not stats.has_min_max:
        return True  # no statistics to prune on, so the reader reads it
    low = _as_key(stats.min)
    high = _as_key(stats.max)
    if low is None or high is None:
        return True
    return any(low <= key <= high for key in wanted)


def _as_key(value: Any) -> str | None:
    """Decode a parquet string statistic to a str, or ``None`` if absent."""
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)

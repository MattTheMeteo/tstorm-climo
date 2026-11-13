"""Build and read reusable hourly observation artifacts (schema v3).

An artifact is an immutable directory that serves the interactive app
without touching the HRRR archive: a place-clustered ``observations/``
directory of slimmed hourly rows, a ``places.parquet`` snapshot carrying
the per-place static columns, and a ``manifest.json`` describing all of
it.

The layout is optimized for the app's two queries -- the opening map (one
row per place) and a bounded drill-in of at most :data:`MAX_SELECTED_PLACES`
places -- so:

- the hourly table stores only the columns that change hour to hour
  (``place_key``/``time_utc``/``variable``/``value``/``valid``);
- it is sorted by place and split into shards, so a ``place_key``
  predicate skips whole row groups; and
- everything constant per place (timezone, anchor node, sampling identity)
  lives once in ``places.parquet`` and is joined back at read time.

The builder is :func:`build_hourly_observation_artifact`; the reader is
:func:`load_observation_artifact`. They live in the same module because
they share one format, and a reader that drifted from its writer would be
worse than no reader at all.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import polars as pl

from tstorm_climo.extract import (
    N_CELLS_COLUMN,
    RATE_COLUMN,
    SAMPLING_COLUMN,
    SAMPLING_KEY_COLUMN,
    TIME_COLUMN,
    VALID_COLUMN,
    extract_series,
)
from tstorm_climo.hrrr import HRRR_ANALYSIS_DATASET_ID, PRECIPITATION_VARIABLE, Opener
from tstorm_climo.places import PLACE_KEY_COLUMN, Place, places_from_frame
from tstorm_climo.progress import format_count, stage
from tstorm_climo.provenance import (
    ARCHIVE_ATTRIBUTION,
    ARCHIVE_LICENSE,
    PLACES_ATTRIBUTION,
    PLACES_LICENSE,
    isoformat_timestamp,
    parse_timestamp,
)

ARTIFACT_SCHEMA_VERSION = 3
ARTIFACT_TYPE = "hourly_observations"
MANIFEST_FILE = "manifest.json"
OBSERVATIONS_DIR = "observations"
PLACES_FILE = "places.parquet"

RAIN_RATE_VARIABLE = "rain_rate"
TIME_UTC_COLUMN = "time_utc"
VALUE_COLUMN = "value"
VARIABLE_COLUMN = "variable"

logger = logging.getLogger(__name__)

# The largest selection the app serves at once. This is an architectural
# guarantee, not just UX: it bounds the worst-case drill-in read to this
# many places' hourly rows.
MAX_SELECTED_PLACES = 20

# Columns constant per place, stored once in ``places.parquet`` and joined
# back at read time. ``grid_lat``/``grid_lon`` are dropped entirely: the
# summary layer never reads them.
STATIC_COLUMNS = (
    "tz_name",
    "y_index",
    "x_index",
    "snap_distance_km",
    SAMPLING_COLUMN,
    N_CELLS_COLUMN,
    SAMPLING_KEY_COLUMN,
)

# The only columns that change hour to hour.
SLIM_COLUMNS = (
    PLACE_KEY_COLUMN,
    TIME_UTC_COLUMN,
    VARIABLE_COLUMN,
    VALUE_COLUMN,
    VALID_COLUMN,
)

# The artifact stores every variable's reading in one ``value`` column and
# discriminates with ``variable``. The analysis layer wants a column named
# after the quantity, so the mapping is applied when one variable is
# selected. Only variables listed here can be interpreted; anything else
# is rejected rather than reported in the wrong units.
VARIABLE_VALUE_COLUMNS: dict[str, str] = {
    RAIN_RATE_VARIABLE: RATE_COLUMN,
}


@dataclass(frozen=True)
class ObservationArtifact:
    """A loaded artifact, ready for bounded lazy queries.

    Attributes:
        directory: Base path or URL the artifact was read from.
        manifest: The parsed ``manifest.json``.
        observations: A lazy scan over the sharded observations. Holding
            this is cheap (it is a query plan, not data).
        places: The place catalog, one ``Place`` per row.
        static: Per-place static columns (``place_key`` plus the columns
            in :data:`STATIC_COLUMNS`), for the read-time join-back.
    """

    directory: str | Path
    manifest: dict[str, Any]
    observations: pl.LazyFrame
    places: list[Place]
    static: pl.DataFrame

    @property
    def observed_start(self) -> datetime | None:
        """First observed timestamp, or ``None`` if the manifest lacks one."""
        return parse_timestamp(
            self.manifest.get("date_range", {}).get("observed_start")
        )

    @property
    def observed_end(self) -> datetime | None:
        """Last observed timestamp, or ``None`` if the manifest lacks one."""
        return parse_timestamp(self.manifest.get("date_range", {}).get("observed_end"))


def build_hourly_observation_artifact(
    places: Sequence[Place],
    start: str,
    end: str,
    output_dir: str | Path,
    *,
    opener: Opener | None = None,
    shard_count: int = 8,
    row_group_size: int | None = None,
) -> dict[str, Any]:
    """Extract, slim, place-cluster, and write an observation artifact.

    Args:
        places: Places to extract, with unique stable ``place_key`` values.
        start: Inclusive start of the time range.
        end: Inclusive end of the time range.
        output_dir: New directory for ``observations/``, ``places.parquet``
            and ``manifest.json``. Must not already exist.
        opener: Optional HRRR opener, for tests.
        shard_count: Upper bound on the number of observation shard files.
        row_group_size: Rows per Parquet row group. Aligning this to one
            place's worth of hours lets a ``place_key`` predicate skip whole
            row groups; ``None`` leaves Polars' default.

    Returns:
        The JSON-serializable manifest written to the artifact directory.

    Raises:
        ValueError: If no places or no observations are available.
        FileExistsError: If ``output_dir`` already exists.
    """
    if not places:
        raise ValueError("cannot build an observation artifact with no places")
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError(f"artifact output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building observation artifact for %s places, %s to %s -> %s",
        format_count(len(places)),
        start,
        end,
        destination,
    )
    series = extract_series(places, start, end, opener=opener)
    if series.height == 0:
        raise ValueError(f"no observations for {start} to {end}")
    logger.info(
        "Preparing %s hourly rows for %s places",
        format_count(series.height),
        format_count(len(places)),
    )

    # Static per-place columns, one row per place, in stable key order.
    static = (
        series.select([PLACE_KEY_COLUMN, *STATIC_COLUMNS])
        .unique(subset=[PLACE_KEY_COLUMN], keep="first")
        .sort(PLACE_KEY_COLUMN)
    )

    # The slim, place-clustered hourly table.
    slim = (
        series.rename({TIME_COLUMN: TIME_UTC_COLUMN, RATE_COLUMN: VALUE_COLUMN})
        .with_columns(pl.lit(RAIN_RATE_VARIABLE).alias(VARIABLE_COLUMN))
        .select(list(SLIM_COLUMNS))
        .sort([PLACE_KEY_COLUMN, TIME_UTC_COLUMN])
    )

    valid_rows = int(slim[VALID_COLUMN].sum())
    manifest: dict[str, Any] = {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": {
            "id": HRRR_ANALYSIS_DATASET_ID,
            "source_variable": PRECIPITATION_VARIABLE,
        },
        "variable": {
            "id": RAIN_RATE_VARIABLE,
            "value_column": VALUE_COLUMN,
            "units": "mm h-1",
        },
        "date_range": {
            "requested_start": start,
            "requested_end": end,
            "observed_start": isoformat_timestamp(
                slim.select(pl.col(TIME_UTC_COLUMN).min()).item()
            ),
            "observed_end": isoformat_timestamp(
                slim.select(pl.col(TIME_UTC_COLUMN).max()).item()
            ),
        },
        "place_count": len(places),
        "row_count": slim.height,
        "valid_fraction": valid_rows / slim.height,
        "shard_count": shard_count,
        "observations_dir": OBSERVATIONS_DIR,
        "observations_columns": list(SLIM_COLUMNS),
        "static_columns": list(STATIC_COLUMNS),
        "attribution": {
            "archive": ARCHIVE_ATTRIBUTION,
            "archive_license": ARCHIVE_LICENSE,
            "places": PLACES_ATTRIBUTION,
            "places_license": PLACES_LICENSE,
        },
    }

    with TemporaryDirectory(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        observations_dir = staging / OBSERVATIONS_DIR
        observations_dir.mkdir()
        with stage(logger, f"Writing {shard_count} observation shards"):
            _write_shards(slim, observations_dir, shard_count, row_group_size)
        with stage(logger, "Writing places snapshot and manifest"):
            _places_frame(places, static).write_parquet(staging / PLACES_FILE)
            (staging / MANIFEST_FILE).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if destination.exists():
            raise FileExistsError(f"artifact output already exists: {destination}")
        staging.rename(destination)
    logger.info("Observation artifact ready: %s", destination)
    return manifest


def load_observation_artifact(
    directory: str | Path, *, storage_options: dict[str, Any] | None = None
) -> ObservationArtifact:
    """Load an artifact, returning a lazy scan over the observations.

    The observations are scanned lazily; the manifest, places, and static
    columns are read eagerly because they are tiny (one row per place).
    ``directory`` may be a local path or an ``s3://``/``https://`` base;
    ``storage_options`` is forwarded to Polars' cloud reader for the two
    parquet reads (the manifest is fetched over HTTPS or boto3).

    Raises:
        ValueError: If the manifest is not an observation artifact, or its
            schema version is not readable by this build.
    """
    base = str(directory)
    if "://" not in base and not (Path(base) / MANIFEST_FILE).is_file():
        raise FileNotFoundError(
            f"not an hourly observation artifact directory: {directory} "
            f"(expected {MANIFEST_FILE})"
        )
    manifest = json.loads(_read_text(_join(base, MANIFEST_FILE)))
    if manifest.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            f"not an hourly observation artifact: {directory} "
            f"(artifact_type={manifest.get('artifact_type')!r})"
        )
    if manifest.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported artifact schema version "
            f"{manifest.get('schema_version')!r} in {directory}"
        )

    places_uri = _join(base, PLACES_FILE)
    places_frame = pl.read_parquet(places_uri, storage_options=storage_options)
    static = places_frame.select([PLACE_KEY_COLUMN, *STATIC_COLUMNS])

    observations = pl.scan_parquet(
        _join(base, f"{OBSERVATIONS_DIR}/*.parquet"),
        storage_options=storage_options,
    )
    logger.debug(
        "Loaded observation artifact %s: %s places, schema v%s",
        directory,
        len(places_frame),
        manifest.get("schema_version"),
    )
    return ObservationArtifact(
        directory=directory,
        manifest=manifest,
        observations=observations,
        places=places_from_frame(places_frame),
        static=static,
    )


def _write_shards(
    slim: pl.DataFrame,
    directory: Path,
    shard_count: int,
    row_group_size: int | None,
) -> None:
    """Write the place-sorted observations as contiguous place shards.

    ``slim`` is already sorted by ``place_key`` (then time), so splitting
    the unique keys into contiguous chunks gives each shard a known
    ``place_key`` range -- which is exactly what lets the reader skip whole
    shards for a ``place_key`` predicate.
    """
    keys = slim[PLACE_KEY_COLUMN].unique(maintain_order=True).to_list()
    shard_size = max(1, math.ceil(len(keys) / shard_count))
    for index in range(0, len(keys), shard_size):
        shard_keys = keys[index : index + shard_size]
        part = slim.filter(pl.col(PLACE_KEY_COLUMN).is_in(shard_keys))
        shard_path = directory / f"shard-{index // shard_size:04d}.parquet"
        part.write_parquet(shard_path, row_group_size=row_group_size)
        logger.info(
            "Wrote %s (%s places, %s rows)",
            shard_path.name,
            format_count(len(shard_keys)),
            format_count(part.height),
        )


def _places_frame(places: Sequence[Place], static: pl.DataFrame) -> pl.DataFrame:
    """One row per place: identity columns plus the per-place static ones."""
    ordered = sorted(places, key=lambda place: place.place_key)
    identity = pl.DataFrame(
        [
            {
                PLACE_KEY_COLUMN: place.place_key,
                **place.model_dump(exclude={"geometry_wkb"}),
            }
            for place in ordered
        ]
    ).with_columns(
        pl.Series(
            "geometry_wkb", [place.geometry_wkb for place in ordered], dtype=pl.Binary
        )
    )
    return identity.join(static, on=PLACE_KEY_COLUMN, how="left")


def _join(base: str | Path, relative: str) -> str:
    """Join a base directory or URL with a relative path."""
    text = str(base)
    if "://" in text:
        return f"{text.rstrip('/')}/{relative}"
    return str(Path(text) / relative)


def _read_text(path: str) -> str:
    """Read a small text file from a local path or a URL.

    The manifest is tiny, so a hosted deployment fetches it over HTTP(S)
    from the public bucket; ``s3://`` goes through the optional boto3
    extra's standard credential chain.
    """
    if path.startswith("s3://"):
        try:
            import boto3
        except ImportError as error:
            raise RuntimeError(
                "reading an s3:// manifest requires the optional dependency "
                "tstorm-climo[catalog-s3]"
            ) from error

        bucket, key = path[len("s3://") :].split("/", 1)
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"]
        return body.read().decode("utf-8")
    if "://" in path:
        import urllib.request

        with urllib.request.urlopen(path) as response:
            return response.read().decode("utf-8")
    return Path(path).read_text(encoding="utf-8")

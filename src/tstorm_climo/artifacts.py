"""Build reusable hourly observation artifacts for interactive analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import polars as pl

from tstorm_climo.extract import (
    N_CELLS_COLUMN,
    RATE_COLUMN,
    SAMPLING_COLUMN,
    SAMPLING_KEY_COLUMN,
    TIME_COLUMN,
    VALID_COLUMN,
    extract_series,
    sampling_cells,
    sampling_frame,
)
from tstorm_climo.grid import Grid
from tstorm_climo.hrrr import (
    HRRR_ANALYSIS_DATASET_ID,
    MIN_VALID_DATE,
    PRECIPITATION_VARIABLE,
    Opener,
    open_analysis,
)
from tstorm_climo.places import Place
from tstorm_climo.provenance import (
    ARCHIVE_ATTRIBUTION,
    ARCHIVE_LICENSE,
    PLACES_ATTRIBUTION,
    PLACES_LICENSE,
)

# v2 adds ``sampling``/``n_cells``/``sampling_key`` to the observations
# and a ``sampling.parquet`` table recording every node a place was
# averaged over. v1 recorded only the anchor node, which stops being the
# sampled set once a footprint applies.
ARTIFACT_SCHEMA_VERSION = 2
RAIN_RATE_VARIABLE = "rain_rate"
TIME_UTC_COLUMN = "time_utc"
VALUE_COLUMN = "value"

OBSERVATION_COLUMNS = (
    "place_key",
    TIME_UTC_COLUMN,
    "variable",
    VALUE_COLUMN,
    VALID_COLUMN,
    "y_index",
    "x_index",
    "grid_lat",
    "grid_lon",
    "snap_distance_km",
    "tz_name",
    SAMPLING_COLUMN,
    N_CELLS_COLUMN,
    SAMPLING_KEY_COLUMN,
)

SAMPLING_FILE = "sampling.parquet"


def build_hourly_observation_artifact(
    places: Sequence[Place],
    start: str,
    end: str,
    output_dir: str | Path,
    *,
    opener: Opener | None = None,
) -> dict[str, Any]:
    """Extract and save hourly rain observations plus their place catalog.

    The artifact is built in a temporary sibling directory and renamed into
    place only after all files are written. Existing output directories are
    never overwritten.

    Args:
        places: Places to extract, with unique stable ``place_key`` values.
        start: Requested inclusive start time.
        end: Requested inclusive end time.
        output_dir: New directory for ``observations.parquet``,
            ``places.parquet``, ``sampling.parquet``, and ``manifest.json``.
        opener: Optional HRRR opener, primarily for tests.

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
    # Build the grid here as well as inside extract_series so the sampling
    # table can be written without re-extracting. Opening the archive is
    # lazy, so the second open costs metadata only.
    grid = Grid.from_dataset(open_analysis(opener=opener))
    series = extract_series(places, start, end, grid=grid, opener=opener)
    if series.height == 0:
        raise ValueError(
            f"no observations for {start} to {end}; the usable archive "
            f"window starts {MIN_VALID_DATE}"
        )
    sampling = sampling_frame(places, sampling_cells(grid, places))

    observations = (
        series.rename({TIME_COLUMN: TIME_UTC_COLUMN, RATE_COLUMN: VALUE_COLUMN})
        .with_columns(pl.lit(RAIN_RATE_VARIABLE).alias("variable"))
        .select(OBSERVATION_COLUMNS)
    )
    sorted_places = sorted(places, key=lambda item: item.place_key)
    # JSON-safe records for the content hash; geometry is hex-encoded so
    # the hash stays deterministic and order-independent.
    json_records = [
        {
            "place_key": place.place_key,
            **place.model_dump(mode="json", exclude={"geometry_wkb"}),
            "geometry_wkb": place.geometry_wkb.hex() if place.geometry_wkb else None,
        }
        for place in sorted_places
    ]
    catalog_hash = _catalog_hash(json_records)
    places_frame = _places_frame(sorted_places)
    valid_rows = int(observations[VALID_COLUMN].sum())
    times = observations[TIME_UTC_COLUMN]
    manifest: dict[str, Any] = {
        "artifact_type": "hourly_observations",
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
            "description": "Hourly-average precipitation rate.",
            "threshold_applied": None,
        },
        "date_range": {
            "requested_start": start,
            "requested_end": end,
            "observed_start": _isoformat(cast(datetime | None, times.min())),
            "observed_end": _isoformat(cast(datetime | None, times.max())),
            "analysis_window_start": MIN_VALID_DATE,
        },
        "sampling": (
            "Footprint-weighted average over covered grid nodes where a "
            "place carries polygon geometry; nearest grid point otherwise."
        ),
        "sampling_cells": {
            "file": SAMPLING_FILE,
            "description": (
                "One row per (place, grid node) actually averaged. "
                "y_index/x_index/grid_lat/grid_lon in observations.parquet "
                "are the place's anchor node, not this set."
            ),
            "row_count": sampling.height,
            "columns": list(sampling.columns),
            "footprint_places": int(
                sampling.filter(pl.col(SAMPLING_COLUMN) == "footprint")
                .get_column("place_key")
                .n_unique()
            ),
            "point_places": int(
                sampling.filter(pl.col(SAMPLING_COLUMN) == "point")
                .get_column("place_key")
                .n_unique()
            ),
        },
        "place_catalog": {
            "place_count": len(places),
            "content_sha256": catalog_hash,
        },
        "observations": {
            "row_count": observations.height,
            "valid_row_count": valid_rows,
            "invalid_row_count": observations.height - valid_rows,
            "valid_fraction": valid_rows / observations.height,
            "columns": list(OBSERVATION_COLUMNS),
        },
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
        observations.write_parquet(staging / "observations.parquet")
        places_frame.write_parquet(staging / "places.parquet")
        sampling.write_parquet(staging / SAMPLING_FILE)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            raise FileExistsError(f"artifact output already exists: {destination}")
        staging.rename(destination)

    return manifest


def _places_frame(places: Sequence[Place]) -> pl.DataFrame:
    """The places table written beside the observations.

    Scalar fields mirror the CSV representation; geometry is kept as a
    binary ``geometry_wkb`` column so footprints survive the parquet
    round-trip, with a stable Binary dtype even when every place is a
    point with no footprint.
    """
    scalar = [
        {"place_key": place.place_key, **place.model_dump(exclude={"geometry_wkb"})}
        for place in places
    ]
    return pl.DataFrame(scalar).with_columns(
        pl.Series("geometry_wkb", [p.geometry_wkb for p in places], dtype=pl.Binary)
    )


def _catalog_hash(records: list[dict[str, Any]]) -> str:
    """Hash canonical place records, independent of input row order."""
    payload = json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _isoformat(value: datetime | None) -> str | None:
    """Serialize a Polars datetime scalar as an ISO timestamp."""
    return value.isoformat() if value is not None else None

"""Build and read gridded display artifacts (Zarr V3 on Icechunk).

A display artifact is an immutable local Icechunk store holding the
**whole grid block** the places sit on, hour by hour, so an app can render
map views -- wet hours per node, hour-of-day climatologies, snapshots --
without touching either the per-place Parquet artifact or the HRRR
archive.

The block is exactly the one :mod:`~tstorm_climo.extract` reads for the
places: the widget for "which nodes did the places sample" is
:func:`~tstorm_climo.extract.sampling_cells`, and the block bounds come
from the same helper. One archive read therefore produces both the
per-place series and the gridded store.

Storage decisions:

- **Zarr V3 on Icechunk, not a flat Zarr directory.** Icechunk versions
  every write, so a future refresh appends a new time range as a new
  commit and older views keep working; a plain directory Zarr would need
  an external rollback story.
- **The store holds rates, not conclusions.** A precomputed
  wet-hours-per-node array would bake one threshold and one window into
  the format. At North Shore scale (tens of nodes, ~70 000 hours) the
  wet-hour count over any window is one cheap reduce, so it is computed
  at query time (:func:`wet_hours`) against the threshold the view asks
  for.
- **Missing data is NaN, not zero.** The archive carries NaN where the
  source is missing; NaN fails any threshold comparison, so a gapped hour
  is neither wet nor silently dry, and :func:`valid_hours` exposes how
  much data a node actually received.

Storage lives in :func:`build_gridded_artifact` (writer) and
:func:`load_gridded_artifact` (reader) in one module because they share
one format, for the same reason as in :mod:`~tstorm_climo.artifacts`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import icechunk
import numpy as np
import xarray as xr

from tstorm_climo.extract import sampling_bounds, sampling_cells
from tstorm_climo.grid import Grid
from tstorm_climo.hrrr import (
    MIN_VALID_DATE,
    PRECIPITATION_MM_PER_HOUR,
    PRECIPITATION_VARIABLE,
    Opener,
    open_analysis,
    to_mm_per_hour,
)
from tstorm_climo.places import Place
from tstorm_climo.progress import format_count, stage
from tstorm_climo.provenance import (
    ARCHIVE_ATTRIBUTION,
    ARCHIVE_LICENSE,
    isoformat_timestamp,
    parse_timestamp,
)
from tstorm_climo.thresholds import DEFAULT_WET_THRESHOLD_MM_PER_HOUR

GRIDDED_ARTIFACT_TYPE = "gridded_display"
GRIDDED_SCHEMA_VERSION = 1
MANIFEST_FILE = "manifest.json"

logger = logging.getLogger(__name__)

# Chunks with time as the leading axis so a windowed view (a month, a
# year) touches only the chunk range it spans, and the full block as the
# spatial chunk: at North Shore scale the whole block is a handful of
# rows, and splitting it would multiply chunk overhead for no read win.
DEFAULT_CHUNK_HOURS = 744


@dataclass(frozen=True)
class GriddedArtifact:
    """A loaded gridded artifact, its lazy dataset and provenance."""

    directory: str | Path
    manifest: dict[str, Any]
    dataset: xr.Dataset

    @property
    def observed_start(self) -> datetime | None:
        """First hour in the store, or ``None`` if the manifest lacks one."""
        return parse_timestamp(
            self.manifest.get("date_range", {}).get("observed_start")
        )

    @property
    def observed_end(self) -> datetime | None:
        """Last hour in the store, or ``None`` if the manifest lacks one."""
        return parse_timestamp(self.manifest.get("date_range", {}).get("observed_end"))


def build_gridded_artifact(
    places: Sequence[Place],
    start: str,
    end: str,
    output_dir: str | Path,
    *,
    opener: Opener | None = None,
    chunk_hours: int = DEFAULT_CHUNK_HOURS,
) -> dict[str, Any]:
    """Extract the places' grid block and commit it as an Icechunk store.

    Args:
        places: Places that define the block. Every node they sample --
            covered footprint nodes or the nearest anchor -- is inside
            the written block.
        start: Inclusive start of the time range. Clamped to
            :data:`~tstorm_climo.hrrr.MIN_VALID_DATE`.
        end: Inclusive end of the time range.
        output_dir: New directory holding ``manifest.json`` and the
            Icechunk repository. Must not already exist.
        opener: Optional HRRR opener, for tests.
        chunk_hours: Hours per chunk along ``time``, clamped to the read
            window's length (about a month by default).

    Returns:
        The JSON-serializable manifest written to the artifact directory.

    Raises:
        ValueError: If no places, no analysis steps in the window, or a
            non-positive ``chunk_hours``.
        FileExistsError: If ``output_dir`` already exists.
    """
    if not places:
        raise ValueError("cannot build a gridded artifact with no places")
    if chunk_hours < 1:
        raise ValueError(f"chunk_hours must be positive, got {chunk_hours}")
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError(f"artifact output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building gridded artifact for %s places, %s to %s -> %s",
        format_count(len(places)),
        start,
        end,
        destination,
    )
    ds = open_analysis(opener=opener)
    with stage(logger, "Building the grid lookup"):
        grid = Grid.from_dataset(ds)
    sampling = sampling_cells(grid, places)
    row_slice, col_slice = sampling_bounds(sampling, grid)
    logger.info(
        "Selected a %sx%s node block (rows x columns) at y%s:%s, x%s:%s",
        row_slice.stop - row_slice.start,
        col_slice.stop - col_slice.start,
        row_slice.start,
        row_slice.stop,
        col_slice.start,
        col_slice.stop,
    )

    # Slice time and space *before* converting units, as everywhere above
    # hrrr.py: the multiply would otherwise materialize the whole archive.
    block = (
        ds[PRECIPITATION_VARIABLE]
        .sel(time=slice(max(start, MIN_VALID_DATE), end))
        .isel(y=row_slice, x=col_slice)
    )
    if block.sizes["time"] == 0:
        raise ValueError(f"no analysis steps for {start} to {end}")
    logger.info("Preparing %s hourly steps", format_count(block.sizes["time"]))
    with stage(logger, "Converting precipitation rates to mm/h"):
        rate = to_mm_per_hour(block)
    dataset = rate.to_dataset(name=PRECIPITATION_MM_PER_HOUR)
    time_chunk = min(chunk_hours, block.sizes["time"])

    manifest: dict[str, Any] = {
        "artifact_type": GRIDDED_ARTIFACT_TYPE,
        "schema_version": GRIDDED_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "storage": {
            "engine": "icechunk",
            "zarr_format": 3,
            "branch": "main",
        },
        "grid": {
            "n_rows": int(block.sizes["y"]),
            "n_cols": int(block.sizes["x"]),
            "y_index_start": int(row_slice.start),
            "x_index_start": int(col_slice.start),
            "time_chunk_hours": time_chunk,
            "description": (
                "The y/x dims are block-local; their coordinates carry "
                "the source archive's global node indexes, and the "
                "2-D latitude/longitude coordinates are included."
            ),
        },
        "dataset": {
            "id": "noaa-hrrr-analysis",
            "source_variable": PRECIPITATION_VARIABLE,
        },
        "variable": {
            "id": PRECIPITATION_MM_PER_HOUR,
            "units": "mm h-1",
            "description": "Hourly-average precipitation rate.",
        },
        "date_range": {
            "requested_start": start,
            "requested_end": end,
            "observed_start": isoformat_timestamp(
                _interpret_utc(block["time"].values[0])
            ),
            "observed_end": isoformat_timestamp(
                _interpret_utc(block["time"].values[-1])
            ),
        },
        "place_count": len(places),
        "attribution": {
            "archive": ARCHIVE_ATTRIBUTION,
            "archive_license": ARCHIVE_LICENSE,
        },
    }

    with TemporaryDirectory(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        storage = icechunk.local_filesystem_storage(str(staging))
        repo = icechunk.Repository.create(storage)
        session = repo.writable_session("main")
        with stage(
            logger,
            f"Writing {time_chunk}-hour Zarr chunks to the Icechunk store",
        ):
            dataset.to_zarr(
                session.store,
                zarr_format=3,
                consolidated=False,
                encoding={
                    PRECIPITATION_MM_PER_HOUR: {
                        "chunks": (time_chunk, block.sizes["y"], block.sizes["x"]),
                    }
                },
            )
            session.commit(
                f"gridded display block y{row_slice.start}:{row_slice.stop}"
                f"/x{col_slice.start}:{col_slice.stop}, {start}..{end}"
            )
        with stage(logger, "Writing manifest"):
            (staging / MANIFEST_FILE).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if destination.exists():
            raise FileExistsError(f"artifact output already exists: {destination}")
        staging.rename(destination)
    logger.info("Gridded artifact ready: %s", destination)
    return manifest


def load_gridded_artifact(directory: str | Path) -> GriddedArtifact:
    """Load a gridded artifact, opening its store lazily.

    The manifest is validated before the store is opened, so a bundle
    from a different schema version is refused without reading any data.

    Raises:
        FileNotFoundError: If ``directory`` does not hold a manifest.
        ValueError: If the manifest is not a gridded display artifact, or
            its schema version is not readable by this build.
    """
    base = Path(directory)
    if not (base / MANIFEST_FILE).is_file():
        raise FileNotFoundError(
            f"not a gridded artifact directory: {directory} (expected {MANIFEST_FILE})"
        )
    manifest = json.loads((base / MANIFEST_FILE).read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != GRIDDED_ARTIFACT_TYPE:
        raise ValueError(
            f"not a gridded display artifact: {directory} "
            f"(artifact_type={manifest.get('artifact_type')!r})"
        )
    if manifest.get("schema_version") != GRIDDED_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported gridded artifact schema version "
            f"{manifest.get('schema_version')!r} in {directory}"
        )

    storage = icechunk.local_filesystem_storage(str(base))
    repo = icechunk.Repository.open(storage)
    session = repo.readonly_session(branch="main")
    dataset = xr.open_zarr(session.store, consolidated=False, mask_and_scale=True)
    logger.debug(
        "Loaded gridded artifact %s: %s places, schema v%s",
        directory,
        manifest.get("place_count"),
        manifest.get("schema_version"),
    )
    return GriddedArtifact(directory=directory, manifest=manifest, dataset=dataset)


def _interpret_utc(value: np.datetime64) -> datetime:
    """Make an aware UTC datetime from an array time coordinate.

    Store coordinates carry wall-clock UTC without a timezone; the
    manifest records the same instants with an explicit offset, the way
    :mod:`~tstorm_climo.artifacts` does.
    """
    aware = np.datetime64(value, "s").item()
    return aware.replace(tzinfo=UTC)


def wet_hours(
    dataset: xr.Dataset,
    *,
    threshold: float = DEFAULT_WET_THRESHOLD_MM_PER_HOUR,
) -> xr.DataArray:
    """Count per node the hours whose rate exceeds ``threshold``.

    Args:
        dataset: A loaded gridded artifact's dataset (rates in mm/h).
        threshold: Wet rate in mm/h. Missing-node hours (NaN) fail the
            comparison and are **not** counted -- a gap must not score
            as a dry hour; see :func:`valid_hours`.

    Returns:
        A ``(y, x)`` DataArray of wet-hour counts, carrying the
        threshold it was computed with.
    """
    counts = (dataset[PRECIPITATION_MM_PER_HOUR] > threshold).sum("time")
    counts.name = "wet_hours"
    counts.attrs = {
        "units": "h",
        "long_name": "Hours with precipitation above threshold",
        "threshold_mm_per_hour": threshold,
    }
    return counts


def valid_hours(dataset: xr.Dataset) -> xr.DataArray:
    """Count per node the hours the store actually carries data for.

    The companion to :func:`wet_hours`: a node with few valid hours has
    an unreliable wet count even when that count is small honestly.
    """
    counts = dataset[PRECIPITATION_MM_PER_HOUR].notnull().sum("time")
    counts.name = "valid_hours"
    counts.attrs = {
        "units": "h",
        "long_name": "Hours with observed data",
    }
    return counts

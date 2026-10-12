"""Provenance for published results.

A climatology number is not reproducible without knowing how it was
produced: which archive, which places, which wet threshold, over which
date range. This module writes that context alongside any saved output
as a JSON sidecar, so a notebook or web app consuming a parquet file can
state its own provenance instead of guessing.

Attribution strings live here too. Both are licence obligations, not
decoration: the dynamical.org archive is CC BY 4.0 and OSM data is ODbL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tstorm_climo.hrrr import HRRR_ANALYSIS_DATASET_ID, MIN_VALID_DATE

ARCHIVE_ATTRIBUTION = (
    "NOAA NWS NCEP HRRR data processed by dynamical.org from "
    "NOAA Open Data Dissemination archives."
)
ARCHIVE_LICENSE = "CC-BY-4.0"
PLACES_ATTRIBUTION = "Place data (c) OpenStreetMap contributors, ODbL."
PLACES_LICENSE = "ODbL"


def build_metadata(
    *,
    start: str,
    end: str,
    threshold_mm_per_hour: float,
    place_count: int,
    dataset_id: str = HRRR_ANALYSIS_DATASET_ID,
    reliability: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the provenance record for one report.

    Args:
        start: Requested start of the time range.
        end: Requested end of the time range.
        threshold_mm_per_hour: Wet threshold used, in mm/h.
        place_count: Number of places in the report.
        dataset_id: Archive the data came from.
        reliability: Optional split-half reliability of the ranking.
            Recorded because a leaderboard without it is misleading.
        extra: Any additional keys to merge in.

    Returns:
        A JSON-serializable mapping.
    """
    metadata: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset_id": dataset_id,
        "date_range": {"requested_start": start, "requested_end": end},
        "analysis_window_start": MIN_VALID_DATE,
        "threshold_mm_per_hour": threshold_mm_per_hour,
        "place_count": place_count,
        "units": {
            "precipitation_mm_per_hour": "mm h-1",
            "note": (
                "Converted from the archive's kg m-2 s-1 at read time; "
                "1 kg m-2 s-1 = 3600 mm/h."
            ),
        },
        "attribution": {
            "archive": ARCHIVE_ATTRIBUTION,
            "archive_license": ARCHIVE_LICENSE,
            "places": PLACES_ATTRIBUTION,
            "places_license": PLACES_LICENSE,
        },
        "caveats": [
            "3 km analysis grid: places sharing a grid node get identical "
            "numbers and are flagged 'tied'.",
            "Most between-place variation is sampling noise; see "
            "split_half_reliability.",
            "Model analysis, not observations.",
        ],
    }
    if reliability is not None:
        metadata["ranking_reliability"] = reliability
    if extra:
        metadata.update(extra)
    return metadata


def write_metadata(path: str | Path, metadata: dict[str, Any]) -> Path:
    """Write a metadata sidecar next to a data file.

    Args:
        path: The data file the sidecar describes.
        metadata: The record from :func:`build_metadata`.

    Returns:
        Path to the sidecar that was written.
    """
    sidecar = Path(f"{path}.meta.json")
    sidecar.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return sidecar


def format_caveats(metadata: dict[str, Any]) -> str:
    """Render the caveats for display under a report."""
    lines = []
    reliability = metadata.get("ranking_reliability")
    if reliability is not None:
        lines.append(
            f"Ranking reliability (split-half r) = {reliability:.2f}. "
            "Values below ~0.5 are substantially sampling noise, so the "
            "order of neighbouring places is not meaningful."
        )
    lines.extend(metadata.get("caveats", []))
    return "\n".join(f"  - {line}" for line in lines)

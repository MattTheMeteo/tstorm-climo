"""Build a place catalog from a regional raw Geofabrik OSM PBF extract."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import polars as pl

from tstorm_climo.catalog import (
    KIND_MATCHES,
    PLACES_SCHEMA,
    deduplicate_places,
    exclude_practice_facilities,
    match_tags,
    save_places,
)
from tstorm_climo.places import PLACE_KEY_COLUMN
from tstorm_climo.progress import (
    ByteProgress,
    RateLimiter,
    format_bytes,
    format_count,
    stage,
)

GEOFABRIK_BASE_URL = "https://download.geofabrik.de"
_REGION_PATTERN = re.compile(r"[a-z0-9-]+(?:/[a-z0-9-]+)*\Z")

# Objects between clock checks during a PBF scan. The rate limiter still
# decides whether a progress line is actually written.
_SCAN_REPORT_INTERVAL = 8192

logger = logging.getLogger(__name__)


def geofabrik_url(region: str) -> str:
    """Return the latest PBF URL for a Geofabrik region slug."""
    normalized = region.strip("/")
    if not _REGION_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid Geofabrik region slug: {region!r}")
    return f"{GEOFABRIK_BASE_URL}/{normalized}-latest.osm.pbf"


def download_geofabrik_pbf(
    region: str,
    destination: str | Path,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Stream a latest regional PBF to disk and return source metadata.

    The download is staged beside its destination and renamed only after
    completion. Existing files are never overwritten.
    """
    url = geofabrik_url(region)
    path = Path(destination)
    if path.exists():
        raise FileExistsError(f"PBF destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    owns_client = client is None
    http = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=30, read=120, write=30, pool=30),
        headers={"User-Agent": "tstorm-climo/0.1 (OSM climatology research)"},
    )
    temporary_path: Path | None = None
    logger.info("Downloading %s", url)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.tmp-", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        byte_count = 0
        with (
            os.fdopen(descriptor, "wb") as output,
            http.stream("GET", url) as response,
        ):
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            total = _content_length(content_length)
            logger.info(
                "Geofabrik responded %s: %s",
                response.status_code,
                format_bytes(total) if total is not None else "size unknown",
            )
            progress = ByteProgress(logger, total=total)
            for chunk in response.iter_raw(1024 * 1024):
                if chunk:
                    output.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
                    progress.update(len(chunk))
            progress.finish()
            last_modified = response.headers.get("Last-Modified")
            etag = response.headers.get("ETag")

        if not byte_count:
            raise ValueError(f"Geofabrik returned an empty PBF for {region}")
        if content_length is not None and int(content_length) != byte_count:
            raise ValueError(
                f"incomplete Geofabrik download: expected {content_length} "
                f"bytes, received {byte_count}"
            )
        if path.exists():
            raise FileExistsError(f"PBF destination already exists: {path}")
        temporary_path.rename(path)
        logger.info(
            "Saved %s (%s, sha256 %s)",
            path,
            format_bytes(byte_count),
            digest.hexdigest()[:12],
        )
        return {
            "region": region.strip("/"),
            "url": url,
            "last_modified": last_modified,
            "etag": etag,
            "bytes": byte_count,
            "sha256": digest.hexdigest(),
        }
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if owns_client:
            http.close()


def _content_length(value: str | None) -> int | None:
    """Parse a Content-Length header, ignoring a missing or malformed one."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def extract_geofabrik_places(
    pbf_path: str | Path,
    kinds: Sequence[str],
    *,
    bbox: tuple[float, float, float, float] | None = None,
) -> pl.DataFrame:
    """Extract matching OSM nodes and area geometries from a PBF.

    Areas are built from closed ways and multipolygon/boundary relations by
    pyosmium. Open ways are intentionally not treated as attraction areas;
    this avoids turning tagged ski-piste lines into ski-resort places.
    """
    if not kinds:
        raise ValueError("at least one kind is required")
    for kind in kinds:
        if kind not in KIND_MATCHES:
            raise KeyError(f"unknown place kind: {kind}")

    import osmium.geom
    import osmium.osm

    factory = osmium.geom.WKBFactory()
    records: list[dict[str, Any]] = []
    logger.info(
        "Scanning %s for %s",
        pbf_path,
        ", ".join(kinds),
    )
    limiter = RateLimiter()
    scanned = 0
    matched = 0
    for obj in osmium.FileProcessor(str(pbf_path)).with_areas():
        scanned += 1
        # Calling the clock for every object in a nationwide PBF is
        # measurable work; checking a counter first keeps the scan loop
        # close to its unlogged cost. The final totals are always logged.
        report_due = scanned % _SCAN_REPORT_INTERVAL == 0 and limiter.due()
        if isinstance(obj, osmium.osm.Node):
            node = obj
            tags = {tag.k: tag.v for tag in node.tags}
            match = match_tags(tags, kinds)
            if match is None or not node.location.valid():
                if report_due:
                    _log_scan(scanned, matched, len(records))
                continue
            kind, match_source = match
            geometry_wkb = bytes.fromhex(factory.create_point(node.location))
            osm_type, osm_id = "node", node.id
        elif isinstance(obj, osmium.osm.Area):
            area = obj
            tags = {tag.k: tag.v for tag in area.tags}
            match = match_tags(tags, kinds)
            if match is None:
                if report_due:
                    _log_scan(scanned, matched, len(records))
                continue
            kind, match_source = match
            geometry_wkb = bytes.fromhex(factory.create_multipolygon(area))
            osm_type = "way" if area.from_way() else "relation"
            osm_id = area.orig_id()
        else:
            if report_due:
                _log_scan(scanned, matched, len(records))
            continue

        matched += 1
        records.append(
            {
                PLACE_KEY_COLUMN: f"osm:{osm_type}:{osm_id}",
                "osm_type": osm_type,
                "osm_id": str(osm_id),
                "name": tags.get("name", ""),
                "kind": kind,
                "geometry_wkb": geometry_wkb,
                "match_source": match_source,
            }
        )
        if report_due:
            _log_scan(scanned, matched, len(records))

    logger.info(
        "PBF scan complete: %s objects, %s matched places",
        format_count(scanned),
        format_count(matched),
    )
    if not records:
        return pl.DataFrame(schema=PLACES_SCHEMA)

    with stage(
        logger,
        f"Computing representative points for {format_count(len(records))} geometries",
    ):
        points = _representative_points([record["geometry_wkb"] for record in records])
    for record, point in zip(records, points, strict=True):
        record["lat"], record["lon"], record["geometry_type"] = point

    places = pl.DataFrame(records, schema=PLACES_SCHEMA)
    if bbox is not None:
        south, west, north, east = bbox
        places = places.filter(
            pl.col("lat").is_between(south, north)
            & pl.col("lon").is_between(west, east)
        )
    return places.select(list(PLACES_SCHEMA)).sort(PLACE_KEY_COLUMN)


def _log_scan(scanned: int, matched: int, kept: int) -> None:
    """Report a periodic PBF scan counter (no percentage: totals unknown)."""
    logger.info(
        "Scanned %s OSM objects, matched %s, kept %s",
        format_count(scanned),
        format_count(matched),
        format_count(kept),
    )


def build_geofabrik_catalog(
    region: str,
    kinds: Sequence[str],
    output_dir: str | Path,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Download, filter, and save one local Geofabrik catalog release.

    The large PBF is temporary; only the normalized catalog and manifest
    remain in ``output_dir``.
    """
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError(f"catalog output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Building %s catalog for %s, kinds: %s",
        region,
        destination,
        ", ".join(kinds),
    )
    with TemporaryDirectory(prefix="tstorm-climo-geofabrik-") as temporary:
        pbf_path = Path(temporary) / "extract.osm.pbf"
        source_metadata = download_geofabrik_pbf(region, pbf_path, client=client)
        places = extract_geofabrik_places(pbf_path, kinds, bbox=bbox)
        with stage(logger, "Filtering practice facilities and duplicates"):
            places = exclude_practice_facilities(places)
            before = places.height
            places = deduplicate_places(places)
            logger.info(
                "Kept %s places after removing %s duplicates",
                format_count(places.height),
                format_count(before - places.height),
            )
        if places.height == 0:
            raise ValueError(f"no matching places found in Geofabrik region {region!r}")
        with stage(logger, "Writing catalog release"):
            save_places(
                places,
                destination,
                kinds=kinds,
                source="geofabrik-pbf",
                source_url=source_metadata["url"],
                extra_metadata={
                    "geofabrik": source_metadata,
                    "bbox": list(bbox) if bbox is not None else None,
                },
            )
    logger.info("Catalog release ready: %s places in %s", places.height, destination)
    return destination


def _representative_points(
    geometries_wkb: Sequence[bytes],
) -> list[tuple[float, float, str]]:
    """Compute point-on-surface coordinates and geometry types with DuckDB."""
    import duckdb

    con = duckdb.connect()
    try:
        logger.debug("Loading DuckDB spatial extension")
        con.execute("INSTALL spatial; LOAD spatial;")
        con.execute("CREATE TEMP TABLE source_geometries (geometry_wkb BLOB)")
        con.executemany(
            "INSERT INTO source_geometries VALUES (?)",
            [(geometry,) for geometry in geometries_wkb],
        )
        rows = con.execute(
            """
            SELECT
                ST_Y(ST_PointOnSurface(ST_GeomFromWKB(geometry_wkb))) AS lat,
                ST_X(ST_PointOnSurface(ST_GeomFromWKB(geometry_wkb))) AS lon,
                ST_GeometryType(ST_GeomFromWKB(geometry_wkb)) AS geometry_type
            FROM source_geometries
            """
        ).fetchall()
        return [(float(lat), float(lon), str(kind)) for lat, lon, kind in rows]
    finally:
        con.close()

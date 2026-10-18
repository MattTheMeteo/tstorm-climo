"""Build place catalogs from the LayerCake OSM POI parquet.

LayerCake (a project of OpenStreetMap US) publishes one geo-parquet of
every OSM point of interest:

    https://data.openstreetmap.us/layercake/pois.parquet

62.3 M features, ~4.7 GB, refreshed daily, ODbL. DuckDB reads it
remotely over HTTP range requests, pulling only the row groups it needs,
so the file is never downloaded whole. That makes it the right source
for a periodic full-country pull, which Overpass is not: Overpass is an
ad-hoc query API on a shared service, and a bulk extract is the shape it
handles worst (observed here as 400s, 504s and silent undercounts).

Kinds are selected by token match rather than whole-string equality,
because LayerCake stores ``sport`` and ``landuse`` as semicolon-joined
lists (``skiing;ice_skating``). A prefix match would be wrong in the
other direction too: ``LIKE 'ski%'`` also matches ``skittles``, an old
form of bowling.

Geometry is carried through as WKB, so downstream work can use real
footprints rather than representative points.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import polars as pl

from tstorm_climo.places import great_circle_km
from tstorm_climo.provenance import (
    PLACES_ATTRIBUTION,
    PLACES_LICENSE,
    build_metadata,
)

LAYERCAKE_POIS_URL = "https://data.openstreetmap.us/layercake/pois.parquet"

# Column of the source that identifies each feature; combined with the
# element type this forms the same `osm:{type}:{id}` key used elsewhere.
SOURCE_ID = "id"
SOURCE_TYPE = "type"
SOURCE_GEOMETRY = "geometry"

# (kind -> (column, tokens)). A feature matches when any listed token
# appears in the semicolon-split column.
#
# `ski_area` spans two columns because neither alone is sufficient:
# a `sport`-only match misses resorts tagged solely
# `landuse=winter_sports` (89 US features, e.g. Afton Alps), and
# `leisure=ski_resort` is effectively unused. See PLAN.md.
KIND_MATCHES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "golf_course": [("leisure", ("golf_course",))],
    # `natural=beach` is the primary beach tag, but coverage in
    # LayerCake is poor in practice (see PLAN.md). `beach_resort` is
    # kept as a second path because it exists in the source (14k
    # features worldwide), even though it did not close the North
    # Shore gap on its own.
    "beach": [("natural", ("beach",)), ("leisure", ("beach_resort",))],
    "ski_area": [
        ("sport", ("skiing", "ski", "ski_jumping", "snowboarding")),
        ("landuse", ("winter_sports", "winter_sport")),
    ],
}

# How a matched kind was identified, recorded per row for curation.
MATCH_SOURCE = "match_source"

PLACES_SCHEMA: dict[str, Any] = {
    "place_key": pl.String,
    "osm_type": pl.String,
    "osm_id": pl.String,
    "name": pl.String,
    "kind": pl.String,
    "lat": pl.Float64,
    "lon": pl.Float64,
    "geometry_wkb": pl.Binary,
    "geometry_type": pl.String,
    MATCH_SOURCE: pl.String,
}


def quote(identifier: str) -> str:
    """Quote a SQL identifier.

    ``sport``, ``natural`` and ``leisure`` are DuckDB reserved words and
    fail unquoted; every column is quoted so new kinds need no special
    casing.
    """
    return '"' + identifier.replace('"', '""') + '"'


def token_clause(column: str, tokens: Sequence[str]) -> str:
    """SQL matching any of ``tokens`` within a semicolon-split column.

    Uses ``list_position(...) > 0`` rather than ``list_contains``: the
    latter is a generic ``T`` function that DuckDB cannot unify with the
    ``VARCHAR[]`` returned by ``string_split``, and casting either
    operand does not resolve it.
    """
    parts = [
        "list_position(string_split(lower(coalesce("
        f"{quote(column)}, '')), ';')::VARCHAR[], '{token}') > 0"
        for token in tokens
    ]
    return "(" + " OR ".join(parts) + ")"


def kind_condition(kind: str) -> str:
    """SQL predicate selecting features of ``kind``."""
    matches = KIND_MATCHES.get(kind)
    if not matches:
        raise KeyError(f"unknown place kind: {kind}")
    return "(" + " OR ".join(
        token_clause(column, tokens) for column, tokens in matches
    ) + ")"


def match_source(kind: str) -> str:
    """Human-readable provenance for how a kind was identified."""
    return "+".join(column for column, _ in KIND_MATCHES[kind])


def match_tags(
    tags: Mapping[str, str], kinds: Sequence[str]
) -> tuple[str, str] | None:
    """Return the first requested kind matched by raw OSM tags.

    Matching follows :data:`KIND_MATCHES` and treats semicolon-separated tag
    values as exact tokens, the same semantics as :func:`kind_condition`.
    The second return value lists the tag keys that matched.
    """
    for kind in kinds:
        matches = KIND_MATCHES.get(kind)
        if not matches:
            raise KeyError(f"unknown place kind: {kind}")
        matched_columns = []
        for column, tokens in matches:
            values = {
                value.strip().casefold()
                for value in tags.get(column, "").split(";")
            }
            if values.intersection(token.casefold() for token in tokens):
                matched_columns.append(column)
        if matched_columns:
            return kind, "+".join(matched_columns)
    return None


# Name substrings (lowercased) that mark a golf practice facility rather
# than a full course. Kept deliberately narrow: "golf center" is absent
# because Garrison Par 3 Golf Center is a real course. The last few are
# verbatim facility names with no reliable generic marker.
PRACTICE_FACILITY_MARKERS: tuple[str, ...] = (
    "driving range",
    "pitch and putt",
    "pitch & putt",
    "pitch n putt",
    "teaching center",
    "golf academy",
    "practice facility",
    "fitness club",
    "athletic association",
    "fore kicks",
    "kohr golf",
    "longhi's golf",
    "golf rx",
)

_PRACTICE_FACILITY_PATTERN = "|".join(
    re.escape(marker) for marker in PRACTICE_FACILITY_MARKERS
)

# Preference order when one OSM feature appears as several elements: an
# area beats a point, and a relation (which aggregates ways) beats a bare
# way, which beats a node.
_OSM_TYPE_PREFERENCE: dict[str, int] = {"relation": 2, "way": 1, "node": 0}


def exclude_practice_facilities(places: pl.DataFrame) -> pl.DataFrame:
    """Drop golf practice facilities (driving ranges, pitch-and-putts, ...).

    Only ``golf_course`` rows are inspected; other kinds pass through
    unchanged. Matching is a case-insensitive substring against
    :data:`PRACTICE_FACILITY_MARKERS`.
    """
    if places.height == 0:
        return places
    lowered = pl.col("name").str.to_lowercase()
    marked = lowered.str.contains(_PRACTICE_FACILITY_PATTERN)
    is_facility = pl.col("kind").eq("golf_course") & marked
    return places.filter(~is_facility)


def deduplicate_places(
    places: pl.DataFrame, *, tol_km: float = 1.0
) -> pl.DataFrame:
    """Collapse near-duplicate representations of the same place.

    OSM often maps one feature as a node, a way and a relation under the
    same name. Within each ``(kind, name)`` group the area representation
    is preferred over a point, and any member within ``tol_km`` of an
    already-kept member is dropped. Same-named places farther apart are
    all kept.
    """
    if places.height == 0:
        return places
    keyed = places.with_columns(
        pl.col("name")
        .str.to_lowercase()
        .str.replace_all(r"[^a-z0-9]", "")
        .alias("_norm_name")
    )
    kept: list[dict[str, Any]] = []
    for part in keyed.partition_by(["kind", "_norm_name"], as_dict=True).values():
        ordered = part.sort(_representation_preference(), descending=True)
        group_kept: list[dict[str, Any]] = []
        for row in ordered.drop("_norm_name").iter_rows(named=True):
            if all(
                great_circle_km(row["lat"], row["lon"], other["lat"], other["lon"])
                > tol_km
                for other in group_kept
            ):
                group_kept.append(row)
        kept.extend(group_kept)
    return pl.DataFrame(kept, schema=PLACES_SCHEMA).sort("place_key")


def _representation_preference() -> pl.Expr:
    """Sort key preferring area geometry, then relation > way > node."""
    return (
        pl.col("geometry_type").ne("POINT").cast(pl.Int64) * 100
        + pl.when(pl.col("osm_type") == "relation")
        .then(2)
        .when(pl.col("osm_type") == "way")
        .then(1)
        .otherwise(0)
    )


def build_query(
    kinds: Sequence[str],
    *,
    source_url: str = LAYERCAKE_POIS_URL,
    bbox: tuple[float, float, float, float] | None = None,
) -> str:
    """Build the DuckDB query pulling the requested kinds.

    Args:
        kinds: Place kinds to extract, keys of :data:`KIND_MATCHES`.
        source_url: LayerCake parquet URL.
        bbox: Optional ``(south, west, north, east)`` filter, applied to
            the representative point.

    Returns:
        A SELECT producing one row per matched feature, with a
        ``place_key``, a representative point, and WKB geometry.

    Raises:
        KeyError: If any kind is unknown.
    """
    if not kinds:
        raise ValueError("at least one kind is required")
    for kind in kinds:
        if kind not in KIND_MATCHES:
            raise KeyError(f"unknown place kind: {kind}")

    predicates = " OR ".join(kind_condition(kind) for kind in kinds)
    # CASE mirrors the WHERE so each row records why it matched; first
    # matching kind wins, in the order the caller requested.
    cases = " ".join(
        f"WHEN {kind_condition(kind)} THEN '{kind}'" for kind in kinds
    )
    source_cases = " ".join(
        f"WHEN {kind_condition(kind)} THEN '{match_source(kind)}'"
        for kind in kinds
    )
    bbox_clause = ""
    if bbox is not None:
        south, west, north, east = bbox
        bbox_clause = (
            f" AND lat BETWEEN {south} AND {north}"
            f" AND lon BETWEEN {west} AND {east}"
        )
    source = f"'{source_url}'"
    return f"""
        SELECT
            'osm:' || {quote(SOURCE_TYPE)} || ':' ||
                {quote(SOURCE_ID)}::VARCHAR          AS place_key,
            {quote(SOURCE_TYPE)}                       AS osm_type,
            {quote(SOURCE_ID)}::VARCHAR                AS osm_id,
            coalesce(name[1], '')                      AS name,
            CASE {cases} END                           AS kind,
            ST_Y(ST_PointOnSurface({quote(SOURCE_GEOMETRY)})) AS lat,
            ST_X(ST_PointOnSurface({quote(SOURCE_GEOMETRY)})) AS lon,
            ST_AsWKB({quote(SOURCE_GEOMETRY)})         AS geometry_wkb,
            ST_GeometryType({quote(SOURCE_GEOMETRY)})  AS geometry_type,
            CASE
                {source_cases}
            END                                        AS {MATCH_SOURCE}
        FROM read_parquet({source})
        WHERE ({predicates}){bbox_clause}
    """


def _connect(con: Any) -> Any:
    """Load the DuckDB extensions the query needs."""
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def fetch_places(
    kinds: Sequence[str],
    *,
    source_url: str = LAYERCAKE_POIS_URL,
    bbox: tuple[float, float, float, float] | None = None,
    con: Any | None = None,
) -> pl.DataFrame:
    """Pull places of the given kinds from the LayerCake parquet.

    Args:
        kinds: Place kinds to extract.
        source_url: LayerCake parquet URL (overridable for tests).
        bbox: Optional ``(south, west, north, east)`` filter.
        con: An existing DuckDB connection. One is created (with the
            httpfs and spatial extensions loaded) when omitted.

    Returns:
        A frame conforming to :data:`PLACES_SCHEMA`.

    Raises:
        KeyError: If a kind is unknown.
        ValueError: If ``kinds`` is empty.
    """
    import duckdb

    query = build_query(kinds, source_url=source_url, bbox=bbox)
    connection = _connect(con) if con is not None else _connect(duckdb.connect())
    # DuckDB's Arrow/polars bridges (`.arrow()`, `.pl()`, `.df()`) all
    # require pyarrow, so this goes through `fetchnumpy()`. It returns
    # BLOB columns as object-dtype arrays of `bytearray`, which polars
    # cannot serialise; convert those to plain bytes first.
    columns = connection.sql(query).fetchnumpy()
    columns["geometry_wkb"] = [
        bytes(value) if value is not None else None
        for value in columns["geometry_wkb"]
    ]
    frame = pl.DataFrame(
        {
            name: pl.Series(name, values, dtype=PLACES_SCHEMA[name])
            for name, values in columns.items()
        },
        schema=PLACES_SCHEMA,
    )
    return frame.select(list(PLACES_SCHEMA)).sort("place_key")


def save_places(
    places: pl.DataFrame,
    output_dir: str | Path,
    *,
    kinds: Sequence[str],
    source: str = "layercake-osm-pois",
    source_url: str = LAYERCAKE_POIS_URL,
    extra_metadata: dict[str, Any] | None = None,
) -> Path:
    """Write a catalog release directory and return its path.

    Writes ``places.parquet`` and ``manifest.json`` describing the pull:
    which kinds were requested, how each was identified, per-kind counts,
    geometry types, and the ODbL attribution.

    Args:
        places: Frame from :func:`fetch_places`.
        output_dir: Directory to create; must not already exist.
        kinds: The kinds requested, for the manifest.
        source_url: Source URL recorded in the manifest.
        extra_metadata: Additional manifest keys.

    Returns:
        The release directory path.

    Raises:
        FileExistsError: If ``output_dir`` already exists.
    """
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError(f"catalog output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    counts = {
        kind: places.filter(pl.col("kind") == kind).height for kind in kinds
    }
    metadata = build_metadata(
        start="",
        end="",
        threshold_mm_per_hour=0.0,
        place_count=places.height,
        extra={
            "catalog": {
                "source": source,
                "source_url": source_url,
                "kinds": list(kinds),
                "kind_match": {
                    kind: [
                        {"column": column, "tokens": list(tokens)}
                        for column, tokens in KIND_MATCHES[kind]
                    ]
                    for kind in kinds
                },
                "match_source": {kind: match_source(kind) for kind in kinds},
                "counts": counts,
                "geometry_types": _geometry_counts(places),
                "unnamed_count": places.filter(pl.col("name") == "").height,
                "distinct_nodes_not_applicable": True,
                **(extra_metadata or {}),
            },
            "attribution_override": {
                "places": PLACES_ATTRIBUTION,
                "places_license": PLACES_LICENSE,
            },
        },
    )
    # Stage in a sibling directory and rename into place, so a failure
    # mid-write cannot leave a half-populated release behind: an earlier
    # crash produced a zero-byte places.parquet in the target directory,
    # which then blocked every retry.
    with TemporaryDirectory(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        places.write_parquet(staging / "places.parquet")
        # Write the manifest directly rather than via write_metadata:
        # that helper appends ".meta.json" to its argument, which would
        # produce "manifest.json.meta.json".
        (staging / "manifest.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            raise FileExistsError(
                f"catalog output already exists: {destination}"
            )
        staging.rename(destination)
    return destination


def _geometry_counts(places: pl.DataFrame) -> dict[str, int]:
    """Count rows per geometry type, for the manifest."""
    if places.height == 0:
        return {}
    return {
        str(row[0]): int(row[1])
        for row in places.group_by("geometry_type").len().iter_rows()
    }

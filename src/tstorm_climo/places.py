"""Places of interest (golf courses, beaches, ...) for the climatology."""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel

from tstorm_climo.osm import KIND_TAGS


class Place(BaseModel):
    """A point of interest on the map (e.g. a golf course or beach).

    Attributes:
        osm_type: OSM element type (node, way, or relation).
        osm_id: OSM element ID.
        name: Place name; empty when OSM does not tag a name.
        kind: Place kind, e.g. ``golf_course`` or ``beach``.
        lat: Latitude in decimal degrees (WGS84).
        lon: Longitude in decimal degrees (WGS84).
    """

    osm_type: str
    osm_id: int
    name: str = ""
    kind: str
    lat: float
    lon: float

    @property
    def display_name(self) -> str:
        """A human-friendly label for reports and plots."""
        return self.name or f"{self.kind} {self.osm_type}/{self.osm_id}"


def classify(tags: dict[str, str], kinds: list[str]) -> str | None:
    """Return the first kind whose OSM (key, value) tag matches."""
    for kind in kinds:
        key, value = KIND_TAGS[kind]
        if tags.get(key) == value:
            return kind
    return None


def from_overpass(kinds: list[str], element: dict) -> Place | None:
    """Build a Place from an Overpass element, or None to skip it.

    Ways and relations provide a ``center``; nodes carry lat/lon
    directly. Elements without a resolvable position or without a tag
    matching one of the queried kinds are skipped.
    """
    tags = element.get("tags", {})
    kind = classify(tags, kinds)
    if kind is None:
        return None
    if "center" in element:
        lat, lon = element["center"]["lat"], element["center"]["lon"]
    elif "lat" in element and "lon" in element:
        lat, lon = element["lat"], element["lon"]
    else:
        return None
    return Place(
        osm_type=element["type"],
        osm_id=element["id"],
        name=tags.get("name", ""),
        kind=kind,
        lat=lat,
        lon=lon,
    )


def deduplicate(places: list[Place], tol_km: float = 1.0) -> list[Place]:
    """Drop same-named places that are near-copies of each other.

    OSM often maps one feature as both a node and a polygon under the
    same name. Within each (kind, name) group, polygon representations
    (ways/relations) are preferred over bare nodes, and any remaining
    member within ``tol_km`` of an already-kept place is dropped.
    Distinct places that merely share a name and lie farther apart are
    all kept.
    """
    kept: list[Place] = []
    groups: dict[tuple[str, str], list[Place]] = {}
    for place in places:
        groups.setdefault((place.kind, place.name), []).append(place)
    for members in groups.values():
        ordered = sorted(
            members, key=lambda p: p.osm_type == "node"
        )  # polygons first
        for place in ordered:
            if all(
                haversine_km(place, other) > tol_km for other in kept
            ):
                kept.append(place)
    return kept


def haversine_km(a: Place, b: Place) -> float:
    """Great-circle distance between two places, in kilometers."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lat2 = radians(a.lat), radians(b.lat)
    dlat, dlon = radians(b.lat - a.lat), radians(b.lon - a.lon)
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(min(1.0, sqrt(h)))


def to_polars(places: list[Place]) -> pl.DataFrame:
    """Convert places to a polars DataFrame for analysis and export."""
    return pl.DataFrame([p.model_dump() for p in places])


def save_csv(places: list[Place], path: str) -> None:
    """Save places to CSV, sorted by kind and name."""
    df = to_polars(sorted(places, key=lambda p: (p.kind, p.display_name)))
    df.write_csv(path)


def load_csv(path: str) -> list[Place]:
    """Load places from a CSV saved by :func:`save_csv`."""
    df = pl.read_csv(path)
    return [Place.model_validate(row) for row in df.iter_rows(named=True)]

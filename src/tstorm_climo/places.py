"""Places of interest (golf courses, beaches, ...) for the climatology."""

from __future__ import annotations

import numpy as np
import polars as pl
from pydantic import BaseModel
from tzfpy import get_tz

from tstorm_climo.osm import KIND_TAGS

EARTH_RADIUS_KM = 6371.0

# Fallback zone when a coordinate cannot be resolved to one.
UTC_TIMEZONE = "UTC"


class Place(BaseModel):
    """A point of interest on the map (e.g. a golf course or beach).

    Attributes:
        osm_type: OSM element type (node, way, or relation).
        osm_id: OSM element ID.
        name: Place name; empty when OSM does not tag a name.
        kind: Place kind, e.g. ``golf_course`` or ``beach``.
        lat: Latitude in decimal degrees (WGS84).
        lon: Longitude in decimal degrees (WGS84).
        timezone: IANA timezone name, e.g. ``America/New_York``.
            Looked up from the coordinates when the place is built, and
            carried through the places CSV. Local-time questions (what
            hour was it when the rain arrived) depend on this.
    """

    osm_type: str
    osm_id: int
    name: str = ""
    kind: str
    lat: float
    lon: float
    timezone: str = UTC_TIMEZONE

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


def timezone_at(lat: float, lon: float) -> str:
    """Return the IANA timezone name containing a coordinate.

    Uses ``tzfpy``'s bundled boundary data (2.9 MB). Falls back to
    ``UTC`` rather than raising: a place we cannot place in a timezone
    is still worth keeping, and UTC is an honest label for "we do not
    know". Places near a zone boundary can resolve to either side.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

    Returns:
        An IANA zone name, or ``UTC`` when the coordinate resolves to
        nothing. Note ``tzfpy`` takes ``(lng, lat)``, the reverse of
        this signature.
    """
    return get_tz(float(lon), float(lat)) or UTC_TIMEZONE


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
        timezone=timezone_at(lat, lon),
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


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float):
    """Great-circle distance between two lat/lon points, in kilometers.

    Arguments may be scalars or numpy arrays; the return type matches.
    """
    rlat1, rlat2 = np.radians(lat1), np.radians(lat2)
    dlat, dlon = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    h = (
        np.sin(dlat / 2) ** 2
        + np.cos(rlat1) * np.cos(rlat2) * np.sin(dlon / 2) ** 2
    )
    # Clip before asin: floating point can push h just past 1.
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))


def haversine_km(a: Place, b: Place) -> float:
    """Great-circle distance between two places, in kilometers."""
    return great_circle_km(a.lat, a.lon, b.lat, b.lon)


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

"""Tests for place extraction from OSM/Overpass responses."""

from __future__ import annotations

import polars as pl
import pytest

from tstorm_climo.osm import KIND_TAGS, build_query
from tstorm_climo.places import (
    Place,
    classify,
    from_overpass,
    load_csv,
    save_csv,
    timezone_at,
)

BOUNDS = (42.35, -71.2, 42.95, -70.5)


def test_classify_matches_tag() -> None:
    assert (
        classify({"leisure": "golf_course", "name": "x"}, KIND_TAGS)
        == "golf_course"
    )
    assert classify({"natural": "beach", "name": "x"}, KIND_TAGS) == "beach"
    assert classify({"amenity": "cafe"}, KIND_TAGS) is None


def test_from_overpass_way_uses_center() -> None:
    element = {
        "type": "way",
        "id": 29689626,
        "center": {"lat": 42.5849407, "lon": -70.7617890},
        "tags": {"leisure": "golf_course", "name": "Essex County Club"},
    }
    place = from_overpass(["golf_course"], element)
    assert isinstance(place, Place)
    assert place.osm_id == 29689626
    assert place.osm_type == "way"
    assert place.name == "Essex County Club"
    assert place.kind == "golf_course"
    assert place.lat == pytest.approx(42.5849407)
    assert place.lon == pytest.approx(-70.7617890)


def test_from_overpass_node_uses_latlon() -> None:
    element = {
        "type": "node",
        "id": 1,
        "lat": 42.5,
        "lon": -70.9,
        "tags": {"natural": "beach", "name": "Singing Beach"},
    }
    place = from_overpass(["golf_course", "beach"], element)
    assert place is not None
    assert place.kind == "beach"
    assert (place.lat, place.lon) == (42.5, -70.9)


def test_from_overpass_skips_unmatched_and_unpositioned() -> None:
    kinds = ["golf_course", "beach"]
    assert from_overpass(kinds, {"type": "way", "id": 2}) is None
    assert from_overpass(kinds, {"type": "node", "id": 3, "tags": {}}) is None
    unmatched = {"type": "way", "id": 4, "tags": {"amenity": "cafe"}}
    unmatched["center"] = {"lat": 1, "lon": 1}
    assert from_overpass(["golf_course"], unmatched) is None


def test_build_query_contains_selectors() -> None:
    query = build_query(BOUNDS, ["golf_course", "beach"])
    assert 'nwr["leisure"="golf_course"](42.35,-71.2,42.95,-70.5);' in query
    assert 'nwr["natural"="beach"](42.35,-71.2,42.95,-70.5);' in query


def test_save_csv_roundtrip_sorted(tmp_path) -> None:
    names = ["Y", "B", "A", "Z"]
    places = [
        Place(
            osm_type="way",
            osm_id=i,
            name=n,
            kind="golf_course",
            lat=42.0,
            lon=-70.9,
        )
        for i, n in enumerate(names)
    ]
    path = tmp_path / "places.csv"
    save_csv(places, str(path))
    df = pl.read_csv(str(path))
    assert df["name"].to_list() == ["A", "B", "Y", "Z"]


def test_from_overpass_assigns_timezone() -> None:
    """North Shore places land in America/New_York."""
    element = {
        "type": "way",
        "id": 29689626,
        "center": {"lat": 42.5849407, "lon": -70.7617890},
        "tags": {"leisure": "golf_course", "name": "Essex County Club"},
    }
    place = from_overpass(["golf_course"], element)
    assert place.timezone == "America/New_York"


def test_timezone_defaults_to_utc_when_not_given() -> None:
    place = Place(
        osm_type="way", osm_id=1, kind="beach", lat=42.0, lon=-70.9
    )
    assert place.timezone == "UTC"


def test_timezone_at_falls_back_to_utc_when_unresolvable(
    monkeypatch,
) -> None:
    """An unresolvable coordinate yields UTC rather than raising.

    Stubbed, because tzfpy resolves ocean and polar regions to Etc/GMT
    zones, so no real coordinate reaches the fallback.
    """
    import tstorm_climo.places as places_module

    monkeypatch.setattr(places_module, "get_tz", lambda lng, lat: "")
    assert places_module.timezone_at(42.0, -70.9) == "UTC"


def test_timezone_at_swaps_lat_lng_for_tzfpy() -> None:
    """tzfpy takes (lng, lat); a swapped call would silently misplace.

    Guard: a point in Massachusetts must not resolve to a zone that
    only makes sense for the reversed coordinate.
    """
    assert timezone_at(42.5849, -70.7618) == "America/New_York"
    assert timezone_at(42.5849, -70.7618) != timezone_at(-70.7618, 42.5849)


def test_timezone_at_resolves_ocean_to_etc_zone() -> None:
    """Documents that ocean coordinates still resolve, to Etc/GMT."""
    assert timezone_at(0.0, 0.0) == "Etc/GMT"


def test_timezone_at_differs_across_the_country() -> None:
    """The lookup is genuinely coordinate-driven, not a constant."""
    east = timezone_at(42.5849, -70.7618)
    mountain = timezone_at(39.7392, -104.9903)  # Denver
    pacific = timezone_at(37.7749, -122.4194)  # San Francisco
    assert east != mountain != pacific
    assert mountain == "America/Denver"
    assert pacific == "America/Los_Angeles"


def test_csv_roundtrip_preserves_timezone(tmp_path) -> None:
    place = Place(
        osm_type="way",
        osm_id=1,
        name="X",
        kind="golf_course",
        lat=42.0,
        lon=-70.9,
        timezone="America/New_York",
    )
    path = tmp_path / "p.csv"
    save_csv([place], str(path))
    assert load_csv(str(path))[0].timezone == "America/New_York"

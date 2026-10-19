"""Tests for the shared catalog schema, tag matching, and release writing.

Tag semantics are checked against :func:`match_tags`, which is what the
Geofabrik builder actually runs. No network, and no DuckDB query text to
keep in sync now that the remote-parquet source is gone.
"""

from __future__ import annotations

import polars as pl
import pytest

from tstorm_climo.catalog import (
    KIND_MATCHES,
    PLACES_SCHEMA,
    deduplicate_places,
    exclude_practice_facilities,
    match_source,
    match_tags,
    save_places,
)

# (name, tags). The same rows the old DuckDB fixture held, with the same
# reserved-word columns and semicolon-joined tag lists, so the semantic
# cases below carry over unchanged.
MINI: list[tuple[str, dict[str, str]]] = [
    ("Test Golf", {"leisure": "golf_course"}),
    ("Alpine Valley", {"leisure": "sports_centre", "sport": "skiing"}),
    ("Abenaki", {"sport": "skiing;ice_skating"}),
    ("Sugarbush", {"landuse": "winter_sports"}),
    ("Old Time Bowl", {"leisure": "pitch", "sport": "skittles"}),
    ("Cape Beach", {"natural": "beach"}),
    ("Nordic Trail", {"sport": "ski;snowboard"}),
    ("Water Ski Dock", {"sport": "water_ski"}),
    ("Point Golf", {"leisure": "golf_course"}),
]


def matched_names(kinds: list[str]) -> set[str]:
    """Names of the MINI rows that match any of ``kinds``."""
    return {name for name, tags in MINI if match_tags(tags, kinds) is not None}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("skiing", True),
        ("skiing;ice_skating", True),  # semicolon list
        ("ski", True),
        ("ski;snowboard", True),
        ("snowboarding", True),
        ("skittles", False),  # bowling, not skiing
        ("water_ski", False),
        ("", False),
        (None, False),
    ],
)
def test_token_matching_semantics(value, expected: bool) -> None:
    """The core correctness rule: token match, not substring match.

    A prefix match like ``LIKE 'ski%'`` would wrongly accept
    ``skittles`` and ``water_ski``.
    """
    tags = {"sport": value} if value is not None else {}
    got = match_tags(tags, ["ski_area"]) is not None
    assert got is expected


def test_skittles_is_not_matched_as_skiing() -> None:
    """Guards the prefix-match bug: LIKE 'ski%' catches these."""
    names = matched_names(["ski_area"])
    assert "Old Time Bowl" not in names
    assert "Water Ski Dock" not in names


def test_ski_area_needs_both_tagging_paths() -> None:
    """Neither column alone is sufficient (see PLAN.md)."""
    names = matched_names(["ski_area"])
    # sport-only tagging
    assert "Alpine Valley" in names
    # semicolon list
    assert "Abenaki" in names
    # landuse-only tagging (the Sugarbush case)
    assert "Sugarbush" in names


def test_golf_course_matches_leisure_only() -> None:
    assert matched_names(["golf_course"]) == {"Test Golf", "Point Golf"}


def test_beach_matches_natural_beach() -> None:
    assert matched_names(["beach"]) == {"Cape Beach"}


def test_unknown_kind_raises() -> None:
    with pytest.raises(KeyError, match="unknown place kind"):
        match_tags({"sport": "skiing"}, ["skiing_thing"])


def test_match_source_lists_columns() -> None:
    assert match_source("golf_course") == "leisure"
    assert match_source("ski_area") == "sport+landuse"


def test_raw_tag_match_uses_exact_semicolon_tokens() -> None:
    assert match_tags({"sport": "skiing;ice_skating"}, ["ski_area"]) == (
        "ski_area",
        "sport",
    )
    assert match_tags({"sport": "skittles"}, ["ski_area"]) is None


def test_kinds_all_have_matches() -> None:
    for kind in ("golf_course", "beach", "ski_area"):
        assert KIND_MATCHES[kind]


def test_save_places_refuses_to_overwrite(tmp_path) -> None:
    import polars as pl

    frame = pl.DataFrame(
        {
            "place_key": ["osm:way:1"],
            "osm_type": ["way"],
            "osm_id": ["1"],
            "name": ["X"],
            "kind": ["golf_course"],
            "lat": [42.0],
            "lon": [-71.0],
            "geometry_wkb": [b""],
            "geometry_type": ["POINT"],
            "match_source": ["leisure"],
        },
        schema=PLACES_SCHEMA,
    )
    (tmp_path / "catalog").mkdir()
    with pytest.raises(FileExistsError):
        save_places(
            frame,
            tmp_path / "catalog",
            kinds=["golf_course"],
            source="test-source",
            source_url="https://example.invalid/test",
        )


def test_save_places_round_trips_real_wkb(tmp_path) -> None:
    """Real geometry must survive the parquet write.

    Earlier tests used empty byte placeholders, which hid the fact that
    DuckDB returns BLOBs as object-dtype arrays of `bytearray` -- polars
    cannot serialise those to parquet.
    """
    import duckdb
    import polars as pl

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    wkb = con.sql(
        "SELECT ST_AsWKB(ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 0))')) AS wkb"
    ).fetchnumpy()["wkb"][0]

    # Mirror the byte conversion the remote reader used to do, then write.
    bytes_column = [bytes(v) if v is not None else None for v in [wkb]]
    frame = pl.DataFrame(
        {
            "place_key": ["osm:way:1"],
            "osm_type": ["way"],
            "osm_id": ["1"],
            "name": ["Real Geometry"],
            "kind": ["golf_course"],
            "lat": [42.0],
            "lon": [-71.0],
            "geometry_wkb": pl.Series(bytes_column, dtype=pl.Binary),
            "geometry_type": ["POLYGON"],
            "match_source": ["leisure"],
        },
        schema=PLACES_SCHEMA,
    )
    out = save_places(
        frame,
        tmp_path / "rel",
        kinds=["golf_course"],
        source="test-source",
        source_url="https://example.invalid/test",
    )
    restored = pl.read_parquet(out / "places.parquet")
    assert restored.height == 1
    assert restored["geometry_wkb"][0] == bytes(wkb)
    assert len(restored["geometry_wkb"][0]) == len(bytes(wkb))


def _place(
    key: str,
    osm_type: str,
    osm_id: str,
    name: str,
    *,
    kind: str = "golf_course",
    lat: float = 42.0,
    lon: float = -71.0,
    geometry_type: str = "POLYGON",
) -> dict:
    return {
        "place_key": key,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "name": name,
        "kind": kind,
        "lat": lat,
        "lon": lon,
        "geometry_wkb": b"",
        "geometry_type": geometry_type,
        "match_source": "leisure",
    }


def _frame(rows: list[dict]):
    return pl.DataFrame(rows, schema=PLACES_SCHEMA)


def test_exclude_practice_facilities_drops_ranges_keeps_courses() -> None:
    frame = _frame(
        [
            _place("osm:way:1", "way", "1", "Fenway Driving Range"),
            _place("osm:way:2", "way", "2", "Garrison Par 3 Golf Center"),
            _place("osm:way:3", "way", "3", "Kohr Golf"),
            _place("osm:way:4", "way", "4", "The Country Club"),
        ]
    )
    out = exclude_practice_facilities(frame)
    assert out["name"].to_list() == ["Garrison Par 3 Golf Center", "The Country Club"]


def test_exclude_practice_facilities_only_applies_to_golf() -> None:
    """The curation rule must not touch other kinds."""
    frame = _frame(
        [
            _place("osm:way:1", "way", "1", "Fenway Driving Range", kind="beach"),
            _place("osm:way:2", "way", "2", "Fenway Driving Range", kind="golf_course"),
        ]
    )
    out = exclude_practice_facilities(frame)
    assert out["kind"].to_list() == ["beach"]
    assert out["name"].to_list() == ["Fenway Driving Range"]


def test_deduplicate_places_collapses_duplicate_representations() -> None:
    frame = _frame(
        [
            # One physical club mapped as a relation plus two ways.
            _place(
                "osm:relation:1",
                "relation",
                "1",
                "Vesper Country Club",
                lat=42.55,
                lon=-71.35,
                geometry_type="MULTIPOLYGON",
            ),
            _place(
                "osm:way:2",
                "way",
                "2",
                "Vesper Country Club",
                lat=42.5501,
                lon=-71.3501,
                geometry_type="MULTIPOLYGON",
            ),
            _place(
                "osm:way:3",
                "way",
                "3",
                "Vesper Country Club",
                lat=42.5502,
                lon=-71.3502,
                geometry_type="MULTIPOLYGON",
            ),
            # Two distinct same-named courses, far apart.
            _place(
                "osm:way:4", "way", "4", "Riverside Golf Course", lat=42.0, lon=-71.0
            ),
            _place(
                "osm:way:5", "way", "5", "Riverside Golf Course", lat=43.0, lon=-73.0
            ),
        ]
    )
    out = deduplicate_places(frame)
    assert out.height == 3
    assert out.filter(pl.col("name") == "Vesper Country Club").height == 1
    assert out.filter(pl.col("name") == "Vesper Country Club")[
        "osm_type"
    ].to_list() == ["relation"]
    assert out.filter(pl.col("name") == "Riverside Golf Course").height == 2


def test_deduplicate_places_prefers_area_over_point() -> None:
    frame = _frame(
        [
            _place(
                "osm:node:1",
                "node",
                "1",
                "Point Golf",
                lat=42.0,
                lon=-71.0,
                geometry_type="POINT",
            ),
            _place(
                "osm:way:2",
                "way",
                "2",
                "Point Golf",
                lat=42.0001,
                lon=-71.0001,
                geometry_type="POLYGON",
            ),
        ]
    )
    out = deduplicate_places(frame)
    assert out.height == 1
    assert out["geometry_type"].to_list() == ["POLYGON"]


def test_deduplicate_places_keeps_differently_named_neighbors() -> None:
    frame = _frame(
        [
            _place("osm:way:1", "way", "1", "Course A", lat=42.0, lon=-71.0),
            _place("osm:way:2", "way", "2", "Course B", lat=42.0001, lon=-71.0001),
        ]
    )
    out = deduplicate_places(frame)
    assert out.height == 2


def test_failed_write_leaves_no_partial_release(tmp_path) -> None:
    """A crash mid-write must not leave a half-populated directory."""
    import polars as pl

    target = tmp_path / "rel"
    good = pl.DataFrame(
        {
            "place_key": ["osm:way:1"],
            "osm_type": ["way"],
            "osm_id": ["1"],
            "name": ["X"],
            "kind": ["golf_course"],
            "lat": [42.0],
            "lon": [-71.0],
            "geometry_wkb": [b"\x00\x01"],
            "geometry_type": ["POINT"],
            "match_source": ["leisure"],
        },
        schema=PLACES_SCHEMA,
    )
    # Force the parquet write to fail by passing a frame whose binary
    # column holds an object dtype polars cannot write.
    bad = good.with_columns(
        pl.Series("geometry_wkb", [bytearray(b"\x00")], dtype=pl.Object)
    )
    with pytest.raises((pl.exceptions.PolarsError, TypeError)):
        save_places(
            bad,
            target,
            kinds=["golf_course"],
            source="test-source",
            source_url="https://example.invalid/test",
        )
    assert not target.exists(), "partial release directory left behind"

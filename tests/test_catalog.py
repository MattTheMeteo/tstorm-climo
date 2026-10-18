"""Tests for the LayerCake place-catalog builder.

Query text is asserted against a real DuckDB in-memory database where it
matters, so SQL syntax and token semantics are checked without network.
"""

from __future__ import annotations

import duckdb
import pytest

from tstorm_climo.catalog import (
    KIND_MATCHES,
    PLACES_SCHEMA,
    build_query,
    kind_condition,
    match_source,
    match_tags,
    quote,
    save_places,
    token_clause,
)

# A miniature stand-in for the LayerCake schema: same column names,
# same reserved-word landmines, same semicolon-joined tag lists.
MINI = """
CREATE TABLE pois AS SELECT * FROM (VALUES
    ('way', 1, 'Test Golf',            'golf_course', '',              '', 'POINT'),
    ('way', 2, 'Alpine Valley', 'sports_centre', 'skiing', '', 'POLYGON'),
    ('way', 3, 'Abenaki', '', 'skiing;ice_skating', '', 'POLYGON'),
    ('way', 4, 'Sugarbush', '', '', 'winter_sports', 'POLYGON'),
    ('way', 5, 'Old Time Bowl', 'pitch', 'skittles', '', 'POLYGON'),
    ('way', 6, 'Cape Beach',           '',              '',              '', 'POLYGON'),
    ('way', 7, 'Nordic Trail', '', 'ski;snowboard', '', 'POLYGON'),
    ('way', 8, 'Water Ski Dock', '', 'water_ski', '', 'POINT'),
    ('node', 9, 'Point Golf',          'golf_course',   '',           '', 'POINT')
) AS t("type", "id", "name", "leisure", "sport", "landuse", "geometry_type");
UPDATE pois SET geometry_type = ST_AsText(
    ST_Point(0, 0)) WHERE geometry_type = 'POINT';
SELECT count(*) FROM pois;
"""


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(MINI)
    return con


def test_quote_escapes_embedded_quotes() -> None:
    assert quote("sport") == '"sport"'
    assert quote('a"b') == '"a""b"'


def test_token_clause_splits_on_semicolons() -> None:
    sql = token_clause("sport", ("skiing",))
    assert "string_split" in sql
    assert "list_position" in sql
    assert "';'" in sql


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

    Parameterized over a synthetic one-row table so every case has a row
    to match; the shared MINI fixture only covers a few of them.
    """
    con = duckdb.connect()
    literal = "NULL" if value is None else f"'{value}'"
    got = con.sql(
        "SELECT "
        + token_clause("sport", ("skiing", "ski", "ski_jumping", "snowboarding"))
        + f" FROM (SELECT {literal} AS sport)"
    ).fetchone()
    assert got is not None
    assert bool(got[0]) is expected


def test_skittles_is_not_matched_as_skiing() -> None:
    """Guards the prefix-match bug: LIKE 'ski%' catches this."""
    con = connect()
    clause = kind_condition("ski_area")
    rows = con.sql(f"SELECT name FROM pois WHERE {clause}").fetchall()
    names = {r[0] for r in rows}
    assert "Old Time Bowl" not in names
    assert "Water Ski Dock" not in names


def test_ski_area_needs_both_tagging_paths() -> None:
    """Neither column alone is sufficient (see PLAN.md)."""
    con = connect()
    clause = kind_condition("ski_area")
    names = {
        r[0]
        for r in con.sql(
            f"SELECT name FROM pois WHERE {clause}"
        ).fetchall()
    }
    # sport-only tagging
    assert "Alpine Valley" in names
    # semicolon list
    assert "Abenaki" in names
    # landuse-only tagging (the Sugarbush case)
    assert "Sugarbush" in names


def test_golf_course_matches_leisure_only() -> None:
    con = connect()
    clause = kind_condition("golf_course")
    names = {
        r[0]
        for r in con.sql(
            f"SELECT name FROM pois WHERE {clause}"
        ).fetchall()
    }
    assert names == {"Test Golf", "Point Golf"}


def test_unknown_kind_raises() -> None:
    with pytest.raises(KeyError, match="unknown place kind"):
        kind_condition("skiing_thing")
    with pytest.raises(KeyError):
        build_query(["not_a_kind"])


def test_empty_kinds_raises() -> None:
    with pytest.raises(ValueError, match="at least one kind"):
        build_query([])


def test_match_source_lists_columns() -> None:
    assert match_source("golf_course") == "leisure"
    assert match_source("ski_area") == "sport+landuse"


def test_raw_tag_match_uses_exact_semicolon_tokens() -> None:
    assert match_tags(
        {"sport": "skiing;ice_skating"}, ["ski_area"]
    ) == ("ski_area", "sport")
    assert match_tags({"sport": "skittles"}, ["ski_area"]) is None


def test_build_query_is_valid_sql() -> None:
    """The generated SQL must parse and run, checked without network."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("""
        CREATE TABLE pois AS SELECT * FROM (VALUES
            ('way', 1, 'Test Golf', 'golf_course', '', '', 'skittles'),
            ('way', 4, 'Sugarbush', '', '', 'winter_sports', '')
        ) AS t("type", "id", "name", "leisure", "sport", "landuse", "junk");
        ALTER TABLE pois ADD COLUMN geometry GEOMETRY;
        UPDATE pois SET geometry = ST_Point(-71.0, 42.0);
    """)
    query = build_query(["golf_course", "ski_area"])
    # read_parquet() needs a path; swap the whole FROM for the table.
    local = query.replace(
        "FROM read_parquet('https://data.openstreetmap.us/layercake/pois.parquet')",
        "FROM pois",
    )
    assert "FROM pois" in local
    rows = con.sql(local).fetchall()
    kinds = {r[4] for r in rows}
    assert kinds == {"golf_course", "ski_area"}
    # place_key format matches the rest of the project.
    assert {r[0] for r in rows} == {"osm:way:1", "osm:way:4"}


def test_build_query_emits_all_schema_columns() -> None:
    for column in PLACES_SCHEMA:
        assert f"AS {column}" in build_query(["golf_course"]) or column in (
            "geometry_wkb",
        )


def test_kinds_all_have_matches() -> None:
    for kind in ("golf_course", "beach", "ski_area"):
        assert KIND_MATCHES[kind]


def test_fetch_places_returns_schema(tmp_path) -> None:
    """End-to-end over a local file, with no network."""
    import polars as pl


    frame = pl.DataFrame(
        {
            "place_key": ["osm:way:1", "osm:way:4"],
            "osm_type": ["way", "way"],
            "osm_id": ["1", "4"],
            "name": ["Test Golf", "Sugarbush"],
            "kind": ["golf_course", "ski_area"],
            "lat": [42.0, 44.0],
            "lon": [-71.0, -72.0],
            "geometry_wkb": [b"", b""],
            "geometry_type": ["POLYGON", "POLYGON"],
            "match_source": ["leisure", "sport+landuse"],
        },
        schema=PLACES_SCHEMA,
    )
    out = save_places(frame, tmp_path / "catalog", kinds=["golf_course", "ski_area"])
    assert (out / "places.parquet").exists()
    assert (out / "manifest.json").exists()

    import json

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["catalog"]["counts"] == {
        "golf_course": 1,
        "ski_area": 1,
    }
    assert manifest["catalog"]["match_source"]["ski_area"] == "sport+landuse"
    assert "ODbL" in manifest["attribution"]["places"]


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
        save_places(frame, tmp_path / "catalog", kinds=["golf_course"])

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

    # Mirror fetch_places' conversion, then write.
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
    out = save_places(frame, tmp_path / "rel", kinds=["golf_course"])
    restored = pl.read_parquet(out / "places.parquet")
    assert restored.height == 1
    assert restored["geometry_wkb"][0] == bytes(wkb)
    assert len(restored["geometry_wkb"][0]) == len(bytes(wkb))


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
        save_places(bad, target, kinds=["golf_course"])
    assert not target.exists(), "partial release directory left behind"

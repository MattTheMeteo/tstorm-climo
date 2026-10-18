"""Tests for the Geofabrik PBF adapter using tiny local OSM extracts."""

from __future__ import annotations

import json

import httpx
import osmium.geom
import osmium.osm
import polars as pl
import pytest

from tstorm_climo.geofabrik import (
    build_geofabrik_catalog,
    download_geofabrik_pbf,
    extract_geofabrik_places,
    geofabrik_url,
)

OSM_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test">
  <node id="1" lat="42.0" lon="-71.0" />
  <node id="2" lat="42.0" lon="-70.9" />
  <node id="3" lat="42.1" lon="-70.9" />
  <node id="4" lat="42.0" lon="-71.0" />
  <node id="5" lat="42.05" lon="-70.95">
    <tag k="natural" v="beach"/><tag k="name" v="Point Beach"/>
  </node>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>
    <tag k="leisure" v="golf_course"/><tag k="name" v="Test Golf"/>
  </way>
</osm>'''


def make_pbf(path) -> bytes:
    coords = [(-71.0, 42.0), (-70.9, 42.0), (-70.9, 42.1), (-71.0, 42.0)]
    writer = osmium.SimpleWriter(str(path))
    for node_id, (lon, lat) in enumerate(coords, start=1):
        writer.add_node(
            osmium.osm.mutable.Node(
                id=node_id,
                location=osmium.osm.Location(lon, lat),
            )
        )
    writer.add_node(
        osmium.osm.mutable.Node(
            id=5,
            location=osmium.osm.Location(-70.95, 42.05),
            tags={"natural": "beach", "name": "Point Beach"},
        )
    )
    refs = [
        osmium.osm.NodeRef(osmium.osm.Location(lon, lat), node_id)
        for node_id, (lon, lat) in enumerate(coords, start=1)
    ]
    writer.add_way(
        osmium.osm.mutable.Way(
            id=10,
            nodes=refs,
            tags={"leisure": "golf_course", "name": "Test Golf"},
        )
    )
    writer.close()
    return path.read_bytes()


def test_geofabrik_url_validates_region_slug() -> None:
    assert geofabrik_url("north-america/us/massachusetts") == (
        "https://download.geofabrik.de/"
        "north-america/us/massachusetts-latest.osm.pbf"
    )
    with pytest.raises(ValueError, match="invalid Geofabrik region"):
        geofabrik_url("../../private")


def test_extract_builds_point_and_area_geometries(tmp_path) -> None:
    pbf = tmp_path / "sample.osm"
    pbf.write_bytes(OSM_XML)

    places = extract_geofabrik_places(pbf, ["golf_course", "beach"])

    assert places.height == 2
    assert set(places["place_key"].to_list()) == {
        "osm:way:10",
        "osm:node:5",
    }
    golf = places.filter(pl.col("place_key") == "osm:way:10").row(0, named=True)
    beach = places.filter(pl.col("place_key") == "osm:node:5").row(0, named=True)
    assert golf["kind"] == "golf_course"
    assert golf["geometry_type"] == "MULTIPOLYGON"
    assert bytes(golf["geometry_wkb"])
    assert (golf["lat"], golf["lon"]) == pytest.approx((42.05, -70.925))
    assert beach["kind"] == "beach"
    assert beach["geometry_type"] == "POINT"
    assert (beach["lat"], beach["lon"]) == pytest.approx((42.05, -70.95))


def test_extract_applies_bbox_after_representative_point(tmp_path) -> None:
    pbf = tmp_path / "sample.osm"
    pbf.write_bytes(OSM_XML)
    places = extract_geofabrik_places(
        pbf,
        ["golf_course", "beach"],
        bbox=(42.04, -70.96, 42.06, -70.94),
    )
    assert places["place_key"].to_list() == ["osm:node:5"]


def test_download_streams_pbf_and_records_http_metadata(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == geofabrik_url("north-america/us/massachusetts")
        return httpx.Response(
            200,
            stream=httpx.ByteStream(OSM_XML),
            headers={
                "Last-Modified": "Mon, 12 Oct 2026 01:00:00 GMT",
                "ETag": '"snapshot"',
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    output = tmp_path / "massachusetts.osm.pbf"
    metadata = download_geofabrik_pbf(
        "north-america/us/massachusetts", output, client=client
    )
    assert output.read_bytes() == OSM_XML
    assert metadata["bytes"] == len(OSM_XML)
    assert metadata["etag"] == '"snapshot"'
    assert len(metadata["sha256"]) == 64


def test_build_catalog_discards_download_and_writes_release(tmp_path) -> None:
    pbf_bytes = make_pbf(tmp_path / "fixture.osm.pbf")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(pbf_bytes))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    output = tmp_path / "catalog"
    release = build_geofabrik_catalog(
        "north-america/us/massachusetts",
        ["golf_course", "beach"],
        output,
        client=client,
    )

    assert release == output
    assert {path.name for path in output.iterdir()} == {
        "places.parquet",
        "manifest.json",
    }
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["catalog"]["source"] == "geofabrik-pbf"
    assert manifest["catalog"]["geofabrik"]["region"] == (
        "north-america/us/massachusetts"
    )
    assert manifest["catalog"]["counts"] == {"golf_course": 1, "beach": 1}
    assert pl.read_parquet(output / "places.parquet").height == 2

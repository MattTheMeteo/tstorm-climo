"""Tests for ordered publication of immutable catalog releases to S3."""

from __future__ import annotations

import json

import pytest

from tstorm_climo.catalog_storage import publish_catalog_to_s3


class FakeS3:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.calls.append(("upload", filename, bucket, key))

    def put_object(self, **kwargs) -> None:
        self.calls.append(("put", kwargs))


def test_s3_release_objects_upload_before_latest_pointer(tmp_path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "places.parquet").write_bytes(b"parquet-data")
    (catalog / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-10-13T12:00:00+00:00"})
    )
    client = FakeS3()

    result = publish_catalog_to_s3(
        catalog,
        "weather-catalogs",
        prefix="osm/places",
        client=client,
    )

    assert [call[0] for call in client.calls] == ["upload", "upload", "put"]
    assert client.calls[0][3].startswith("osm/places/releases/")
    assert client.calls[1][3].endswith("/manifest.json")
    pointer_call = client.calls[-1][1]
    assert pointer_call["Bucket"] == "weather-catalogs"
    assert pointer_call["Key"] == "osm/places/latest.json"
    pointer = json.loads(pointer_call["Body"])
    assert pointer["places_key"].endswith("/places.parquet")
    assert pointer["catalog_generated_at"] == "2026-10-13T12:00:00+00:00"
    assert result["release_id"] == pointer["release_id"]


def test_s3_publisher_rejects_incomplete_release(tmp_path) -> None:
    with pytest.raises(ValueError, match="not a catalog release"):
        publish_catalog_to_s3(tmp_path, "weather-catalogs", client=FakeS3())

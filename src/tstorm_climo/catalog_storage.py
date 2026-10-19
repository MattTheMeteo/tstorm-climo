"""Publish completed catalog releases to object storage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CATALOG_PREFIX = "tstorm-climo/catalogs"


def publish_catalog_to_s3(
    catalog_dir: str | Path,
    bucket: str,
    *,
    prefix: str = DEFAULT_CATALOG_PREFIX,
    client: Any | None = None,
) -> dict[str, Any]:
    """Upload a completed catalog release and advance its latest pointer.

    The immutable release objects are uploaded first. The small `latest.json`
    pointer is written last, so readers never discover a partial release.
    AWS credentials are resolved by boto3's standard credential chain.
    """
    directory = Path(catalog_dir)
    places_path = directory / "places.parquet"
    manifest_path = directory / "manifest.json"
    if not places_path.is_file() or not manifest_path.is_file():
        raise ValueError(
            f"not a catalog release directory: {directory} "
            "(expected places.parquet and manifest.json)"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if client is None:
        try:
            import boto3
        except ImportError as error:
            raise RuntimeError(
                "S3 publishing requires the optional dependency "
                "tstorm-climo[catalog-s3]"
            ) from error
        client = boto3.client("s3")

    prefix = prefix.strip("/")
    published_at = datetime.now(UTC)
    places_sha256 = _sha256_file(places_path)
    manifest_sha256 = _sha256_file(manifest_path)
    release_id = f"{published_at.strftime('%Y%m%dT%H%M%S.%fZ')}-{places_sha256[:12]}"
    release_prefix = (
        f"{prefix}/releases/{release_id}" if prefix else f"releases/{release_id}"
    )
    places_key = f"{release_prefix}/places.parquet"
    manifest_key = f"{release_prefix}/manifest.json"

    client.upload_file(str(places_path), bucket, places_key)
    client.upload_file(str(manifest_path), bucket, manifest_key)

    pointer: dict[str, Any] = {
        "release_id": release_id,
        "places_key": places_key,
        "manifest_key": manifest_key,
        "places_sha256": places_sha256,
        "manifest_sha256": manifest_sha256,
        "published_at": published_at.isoformat(timespec="seconds"),
        "catalog_generated_at": manifest.get("generated_at"),
    }
    latest_key = f"{prefix}/latest.json" if prefix else "latest.json"
    client.put_object(
        Bucket=bucket,
        Key=latest_key,
        Body=(json.dumps(pointer, indent=2, sort_keys=True) + "\n").encode(),
        ContentType="application/json",
    )
    return {**pointer, "bucket": bucket, "latest_key": latest_key}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

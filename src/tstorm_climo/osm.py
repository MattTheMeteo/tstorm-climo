"""Querying OpenStreetMap places via the Overpass API."""

from __future__ import annotations

import time
from collections.abc import Callable

import requests

# Overpass API endpoint. Public instance; see https://wiki.osm.org/Overpass_API.
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

# OSM usage policy asks for a descriptive, per-app User-Agent.
USER_AGENT = (
    "tstorm-climo/0.1 (precipitation climatology research; "
    "contact mwl5399@gmail.com)"
)

# HTTP transport for Overpass, swappable in tests.
Transport: Callable[..., requests.Response] = requests.post

# OSM (key, value) tag for each supported place kind.
KIND_TAGS: dict[str, tuple[str, str]] = {
    "golf_course": ("leisure", "golf_course"),
    "beach": ("natural", "beach"),
}


def build_query(
    bbox: tuple[float, float, float, float],
    kinds: list[str],
    *,
    timeout: int = 60,
) -> str:
    """Build an Overpass QL query for the given bounding box and kinds.

    Args:
        bbox: (south, west, north, east) in decimal degrees.
        kinds: Keys of ``KIND_TAGS``.
        timeout: Overpass server-side query timeout in seconds.

    Returns:
        The Overpass QL query string.
    """
    selectors = [
        f'nwr["{k}"="{v}"]'
        for kind in kinds
        for k, v in [KIND_TAGS[kind]]
    ]
    s, w, n, e = bbox
    body = "\n".join(f"  {selector}({s},{w},{n},{e});" for selector in selectors)
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags;\n"


def run_query(
    query: str,
    *,
    endpoint: str = OVERPASS_ENDPOINT,
    transport: Callable[..., requests.Response] | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 5.0,
) -> list[dict]:
    """Run an Overpass query and return the parsed JSON elements.

    Retries on server-side errors (Overpass is a free shared service).
    """
    post = transport or requests.post
    response: requests.Response | None = None
    for attempt in range(1, max_retries + 1):
        response = post(
            endpoint,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
        )
        if response.ok:
            return response.json()["elements"]
        if attempt < max_retries:
            time.sleep(backoff_seconds)
    assert response is not None
    raise RuntimeError(
        f"Overpass query failed after {max_retries} attempts: "
        f"HTTP {response.status_code} {response.text[:200]}"
    )

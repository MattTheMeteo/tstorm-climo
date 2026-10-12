"""Querying OpenStreetMap places via the Overpass API."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

# Overpass API endpoint. Public instance; see https://wiki.osm.org/Overpass_API.
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

# OSM usage policy asks for a descriptive, per-app User-Agent.
USER_AGENT = (
    "tstorm-climo/0.1 (precipitation climatology research; contact mwl5399@gmail.com)"
)

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
    selectors = [f'nwr["{k}"="{v}"]' for kind in kinds for k, v in [KIND_TAGS[kind]]]
    s, w, n, e = bbox
    body = "\n".join(f"  {selector}({s},{w},{n},{e});" for selector in selectors)
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags;\n"


def run_query(
    query: str,
    *,
    endpoint: str = OVERPASS_ENDPOINT,
    transport: Callable[..., httpx.Response] | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 5.0,
    timeout_seconds: float = 120.0,
) -> list[dict]:
    """Run an Overpass query and return the parsed JSON elements.

    Retries on server-side errors (Overpass is a free shared service).

    Args:
        query: Overpass QL, from :func:`build_query`.
        endpoint: Overpass API URL.
        transport: Override for ``httpx.post`` (for tests).
        max_retries: Attempts before giving up.
        backoff_seconds: Fixed pause between attempts. Overpass's public
            instances are shared and commonly return 429/504 under load,
            so retrying is normal rather than exceptional.
        timeout_seconds: Per-attempt network timeout. Without this a
            hung connection blocks the build indefinitely, and long
            regional queries can legitimately take minutes.

    Returns:
        The ``elements`` list from the Overpass JSON response.

    Raises:
        RuntimeError: If every attempt fails.
    """
    post = transport or httpx.post
    last_error: str = "no attempt made"
    for attempt in range(1, max_retries + 1):
        try:
            response: httpx.Response = post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout_seconds,
            )
        except httpx.HTTPError as error:
            # Connection resets and read timeouts are transient on a
            # shared service, so they are retried like an HTTP failure.
            last_error = f"{type(error).__name__}: {error}"
        else:
            if response.is_success:
                elements: list[dict] = response.json()["elements"]
                return elements
            last_error = f"HTTP {response.status_code} {response.text[:200]}"
        if attempt < max_retries:
            time.sleep(backoff_seconds)
    raise RuntimeError(
        f"Overpass query failed after {max_retries} attempts: {last_error}"
    )

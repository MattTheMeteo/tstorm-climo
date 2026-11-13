"""Runtime configuration for the web API, resolved from the environment.

The server is pointed at built artifacts the same way the retired marimo
app was: ``TSTORM_CLIMO_ARTIFACT`` (the observation artifact) and the
optional ``TSTORM_CLIMO_GRIDDED`` (the display store). Resolution stays
outside the analysis package; see PLAN.md Phase 6.

``TSTORM_CLIMO_CORS_ORIGINS`` is a comma-separated list of origins the
browser app may be served from. The default is limited to local Next.js
development; deployments should set their exact frontend origin(s).

``TSTORM_CLIMO_LOG_LEVEL`` controls the package logger's level (default
``INFO``; ``DEBUG`` adds per-request timings), and
``TSTORM_CLIMO_SLOW_REQUEST_SECONDS`` sets the warning threshold for slow
requests (default ``1.0``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")

# A request taking longer than this is worth a warning: the largest query
# the app permits is a couple of seconds, so a slower one is anomalous.
DEFAULT_SLOW_REQUEST_SECONDS = 1.0


@dataclass(frozen=True)
class Settings:
    """Artifact paths the API serves, plus its CORS allow-list."""

    artifact_dir: str | None
    gridded_dir: str | None
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    slow_request_seconds: float = DEFAULT_SLOW_REQUEST_SECONDS

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.environ.get("TSTORM_CLIMO_CORS_ORIGINS")
        return cls(
            artifact_dir=os.environ.get("TSTORM_CLIMO_ARTIFACT") or None,
            gridded_dir=os.environ.get("TSTORM_CLIMO_GRIDDED") or None,
            cors_origins=(
                DEFAULT_CORS_ORIGINS
                if origins is None
                else tuple(
                    origin.strip() for origin in origins.split(",") if origin.strip()
                )
            ),
            slow_request_seconds=float(
                os.environ.get(
                    "TSTORM_CLIMO_SLOW_REQUEST_SECONDS",
                    DEFAULT_SLOW_REQUEST_SECONDS,
                )
            ),
        )

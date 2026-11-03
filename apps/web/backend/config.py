"""Runtime configuration for the web API, resolved from the environment.

The server is pointed at built artifacts the same way the retired marimo
app was: ``TSTORM_CLIMO_ARTIFACT`` (the observation artifact) and the
optional ``TSTORM_CLIMO_GRIDDED`` (the display store). Resolution stays
outside the analysis package; see PLAN.md Phase 6.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Artifact paths the API serves."""

    artifact_dir: str | None
    gridded_dir: str | None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            artifact_dir=os.environ.get("TSTORM_CLIMO_ARTIFACT") or None,
            gridded_dir=os.environ.get("TSTORM_CLIMO_GRIDDED") or None,
        )

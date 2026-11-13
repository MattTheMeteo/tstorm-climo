"""Run the API.

export TSTORM_CLIMO_ARTIFACT="artifacts/north-shore/rain-2018-2025"
export TSTORM_CLIMO_GRIDDED="artifacts/north-shore/gridded"  # optional
uv run --group web uvicorn apps.web.backend.main:app

The application logs through Uvicorn's stderr handler. Set
``TSTORM_CLIMO_LOG_LEVEL`` (default ``INFO``; use ``DEBUG`` for per-request
timings) to control how much the package itself reports. Importing the
package library still installs no handlers.
"""

import logging
import os

from tstorm_climo.progress import LOGGER_NAME

from .app import create_app

level_name = os.environ.get("TSTORM_CLIMO_LOG_LEVEL", "INFO").upper()
logging.getLogger(LOGGER_NAME).setLevel(level_name)
logging.getLogger("apps.web.backend").setLevel(level_name)

app = create_app()

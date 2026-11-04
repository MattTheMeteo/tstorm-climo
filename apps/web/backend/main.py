"""Run the API.

export TSTORM_CLIMO_ARTIFACT="artifacts/north-shore/rain-2018-2025"
export TSTORM_CLIMO_GRIDDED="artifacts/north-shore/gridded"  # optional
uv run --group web uvicorn apps.web.backend.main:app
"""

from .app import create_app

app = create_app()

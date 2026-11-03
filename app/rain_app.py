"""The North Shore rain-climatology app (marimo).

Runs entirely from prepared artifacts — no HRRR archive reads, no
Overpass queries, no network. Point it at a built artifact:

    export TSTORM_CLIMO_ARTIFACT="artifacts/north-shore/rain-2018-2025"
    export TSTORM_CLIMO_GRIDDED="artifacts/north-shore/gridded-2018-2025"
    uv run --group app marimo run app/rain_app.py

The gridded store is optional: without it the map view shows how to
enable it, while every table and chart still works. See
``app/demo_artifact.py`` for a network-free synthetic pair of
artifacts.

Reactivity follows marimo's cell rule: a cell that *reads* another
cell's widget value is not the cell that creates it, so widget creation
and the views that consume values live in separate cells. Every view
cell tolerates a missing configuration (artifact or store unset)
by showing the hint instead of computing — no stopped-cell cascades.
"""

import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import os

    ARTIFACT_DIR = os.environ.get("TSTORM_CLIMO_ARTIFACT", "")
    GRIDDED_DIR = os.environ.get("TSTORM_CLIMO_GRIDDED", "")
    return ARTIFACT_DIR, GRIDDED_DIR


@app.cell
def _():
    from datetime import date, timedelta

    import altair as alt
    import marimo as mo
    import polars as pl

    from tstorm_climo.artifacts import MAX_SELECTED_PLACES, load_observation_artifact
    from tstorm_climo.gridded import load_gridded_artifact, wet_hours
    from tstorm_climo.query import artifact_report

    return (
        MAX_SELECTED_PLACES,
        alt,
        artifact_report,
        date,
        load_gridded_artifact,
        load_observation_artifact,
        mo,
        pl,
        timedelta,
        wet_hours,
    )


@app.cell
def _(ARTIFACT_DIR, load_observation_artifact, mo):
    """Load the prepared artifact once; every view reads from it lazily."""
    artifact = load_observation_artifact(ARTIFACT_DIR) if ARTIFACT_DIR else None
    config_hint = mo.md(
        "### No artifact configured\n\n"
        "Set `TSTORM_CLIMO_ARTIFACT` to a built observation-artifact "
        "directory (and optionally `TSTORM_CLIMO_GRIDDED` for the map "
        "view):\n\n"
        "    export TSTORM_CLIMO_ARTIFACT=... \\\n"
        "        && uv run --group app marimo run app/rain_app.py\n\n"
        "No artifact? `uv run --group app python app/demo_artifact.py` "
        "builds a network-free demo pair under `artifacts/demo/`."
    )
    header = (
        mo.md(
            "# North Shore rain app\n\n"
            f"**Coverage:** {artifact.observed_start:%Y-%m-%d} → "
            f"{artifact.observed_end:%Y-%m-%d} · {len(artifact.places)} "
            f"places · variable `{artifact.manifest['variable']['id']}` "
            f"({artifact.manifest['variable']['units']})"
        )
        if artifact is not None
        else config_hint
    )
    header
    return artifact, config_hint, header


@app.cell
def _(artifact, config_hint, date, mo):
    kinds = sorted({place.kind for place in artifact.places}) if artifact else []
    start = artifact.observed_start.date() if artifact else date(2020, 1, 1)
    end = artifact.observed_end.date() if artifact else date(2020, 1, 1)
    kind = mo.ui.dropdown(options=["all", *kinds], value="all", label="Attraction kind")
    wet_threshold = mo.ui.number(
        start=0.0, stop=2.0, step=0.01, value=0.05, label="Wet threshold (mm/h)"
    )
    date_range = mo.ui.date_range(
        start=start, stop=end, value=(start, end), label="Date range"
    )
    controls = mo.hstack([kind, wet_threshold, date_range], justify="start")
    controls
    return controls, date_range, kind, wet_threshold


@app.cell
def _(MAX_SELECTED_PLACES, artifact, config_hint, mo):
    """One multiselect over every place; `kind` filters the selection."""
    places = artifact.places if artifact is not None else []
    options = {
        f"{place.display_name} · {place.kind}": place.place_key for place in places
    }
    label_of = {key: label for label, key in options.items()}
    golf = [place.place_key for place in places if place.kind == "golf_course"]
    defaults = (
        golf[:MAX_SELECTED_PLACES] or list(options.values())[:MAX_SELECTED_PLACES]
    )
    place = mo.ui.multiselect(
        options=options,
        value=[label_of[key] for key in defaults],
        max_selections=MAX_SELECTED_PLACES,
        label=f"Places (up to {MAX_SELECTED_PLACES})",
        full_width=True,
    )
    place
    return place


@app.cell
def _(
    MAX_SELECTED_PLACES,
    artifact,
    artifact_report,
    config_hint,
    date_range,
    kind,
    mo,
    place,
    wet_threshold,
):
    """The report for one selection: leaderboard table plus reliability."""
    if artifact is None:
        report = None
        block = config_hint
    else:
        keys = [str(key) for key in place.value] or list(place.options.values())[
            :MAX_SELECTED_PLACES
        ]
        if kind.value != "all":
            eligible = {p.place_key for p in artifact.places if p.kind == kind.value}
            keys = [key for key in keys if key in eligible] or list(eligible)[
                :MAX_SELECTED_PLACES
            ]
        if not keys:
            report = None
            block = mo.md("**Select at least one place** to compute the report.")
        else:
            report = artifact_report(
                artifact,
                keys,
                start=str(date_range.value[0]),
                end=str(date_range.value[1]),
                threshold_mm_per_hour=wet_threshold.value,
            )
            keep = [
                column
                for column in (
                    "name",
                    "kind",
                    "wet_fraction",
                    "wet_hours",
                    "valid_hours",
                    "rain_mm",
                    "tied",
                )
                if column in report.summary.columns
            ]
            leader = report.summary.select(keep).sort("wet_fraction", descending=True)
            leaderboard = mo.ui.table(
                leader,
                selection=None,
                pagination=True,
                page_size=25,
                format_mapping={"wet_fraction": lambda value: f"{value:.1%}"},
            )
            reliability = report.reliability
            reliability_note = (
                mo.md(
                    f"**Ranking reliability** (split-half r) = "
                    f"**{reliability:.2f}** for this range and threshold. "
                    "Below ~0.5 the order of neighbouring places is "
                    "mostly sampling noise."
                )
                if reliability == reliability
                else mo.md("Ranking reliability not computable for this selection.")
            )
            block = mo.vstack([reliability_note, leaderboard])
    block
    return block, report


@app.cell
def _(artifact, config_hint, mo, report):
    """Chart focus dropdown over the report's places (wettest by default)."""
    if report is None:
        focus = None
    else:
        focus_options = {
            f"{place.display_name} · {place.kind}": place.place_key
            for place in report.places
        }
        wettest = report.summary.tail(1).item(0, "place_key")
        focus = mo.ui.dropdown(
            options=focus_options,
            value={key: label for label, key in focus_options.items()}[wettest],
            label="Charts show",
        )
    focus
    return focus


@app.cell
def _(alt, focus, mo, pl, report):
    """Monthly and local-hour wet-frequency charts for the focus place."""
    if report is None or focus is None:
        charts = mo.md("Select places to see the monthly and hourly charts.")
    else:
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        per_month = report.monthly.filter(
            pl.col("place_key") == focus.value
        ).with_columns(
            month_name=pl.col("local_month").map_elements(
                lambda month: months[month - 1], return_dtype=pl.String
            )
        )
        per_hour = report.hourly.filter(
            pl.col("place_key") == focus.value
        ).with_columns(hour_name=pl.col("local_hour").cast(pl.String))
        monthly_chart = (
            alt.Chart(per_month)
            .mark_bar()
            .encode(
                x=alt.X("month_name:N", sort=months, title="Local month"),
                y=alt.Y(
                    "wet_fraction:Q",
                    title="Wet share of observed hours",
                    axis=alt.Axis(format="%"),
                ),
                tooltip=["local_month", "wet_hours", "valid_hours", "wet_fraction"],
            )
            .properties(width=380, title="Wet fraction by local month")
        )
        hourly_chart = (
            alt.Chart(per_hour)
            .mark_bar()
            .encode(
                x=alt.X(
                    "hour_name:N",
                    sort=[str(hour) for hour in range(24)],
                    title="Local hour",
                ),
                y=alt.Y(
                    "wet_fraction:Q",
                    title="Wet share of observed hours",
                    axis=alt.Axis(format="%"),
                ),
                tooltip=["local_hour", "wet_hours", "hours_observed", "wet_fraction"],
            )
            .properties(width=380, title="Wet fraction by local hour")
        )
        charts = mo.hstack([monthly_chart, hourly_chart], wrap=False)
    charts
    return charts


@app.cell
def _(GRIDDED_DIR, load_gridded_artifact):
    """Open the optional gridded display store (map view)."""
    store = load_gridded_artifact(GRIDDED_DIR) if GRIDDED_DIR else None
    store
    return store


@app.cell
def _(alt, artifact, config_hint, mo, pl, store, timedelta, wet_threshold, wet_hours):
    """Wet hours per node over the store's last year, with places overlaid."""
    if store is None:
        map_section = mo.md(
            "### Wet-hours map\n\n"
            "Optional: set `TSTORM_CLIMO_GRIDDED` to a gridded display "
            "store to enable the map view — build one with "
            "`uv run tstorm-climo build-gridded --places ...` or the "
            "demo script."
        )
    elif artifact is None:
        map_section = config_hint
    else:
        window_end = store.observed_end.date()
        window_start = max(
            window_end - timedelta(days=365), store.observed_start.date()
        )
        rates = store.dataset.sel(time=slice(str(window_start), str(window_end)))
        wet = wet_hours(rates, threshold=wet_threshold.value)
        tidy = wet.to_dataframe().reset_index()
        nodes = (
            alt.Chart(tidy)
            .mark_circle(size=160, opacity=0.8)
            .encode(
                x=alt.X("longitude:Q", scale=alt.Scale(zero=False), title="Longitude"),
                y=alt.Y("latitude:Q", scale=alt.Scale(zero=False), title="Latitude"),
                color=alt.Color(
                    "wet_hours:Q",
                    scale=alt.Scale(scheme="viridis"),
                    title="wet hours",
                ),
                tooltip=["wet_hours:Q", "latitude:Q", "longitude:Q"],
            )
        )
        places_frame = pl.DataFrame(
            {
                "name": [place.display_name for place in artifact.places],
                "kind": [place.kind for place in artifact.places],
                "lat": [place.lat for place in artifact.places],
                "lon": [place.lon for place in artifact.places],
            }
        )
        markers = (
            alt.Chart(places_frame)
            .mark_circle(color="#111827", size=40)
            .encode(
                x=alt.X("lon:Q", scale=alt.Scale(zero=False), title="Longitude"),
                y=alt.Y("lat:Q", scale=alt.Scale(zero=False), title="Latitude"),
                tooltip=["name", "kind"],
            )
        )
        map_section = (
            (nodes + markers)
            .properties(
                title=f"Wet hours per node · {window_start} → {window_end}",
                width=640,
                height=420,
            )
            .interactive()
        )
    map_section
    return map_section


@app.cell
def _(MAX_SELECTED_PLACES, artifact, config_hint, mo):
    """Provenance footer: source, units, sampling caveats, attribution."""
    if artifact is None:
        caveats = None
    else:
        manifest = artifact.manifest
        attribution = manifest["attribution"]
        caveats = mo.md(
            "### Caveats and provenance\n\n"
            f"Rain detection uses `{manifest['dataset']['source_variable']}` "
            f"from `{manifest['dataset']['id']}` (hourly analyses), "
            "converted to mm/h at read time. A 3 km grid cannot separate "
            "co-located places — rows marked `tied` share the same sampled "
            "node discipline as their neighbours. Reliability is reported "
            f"per selection; read it before trusting an order. At most "
            f"{MAX_SELECTED_PLACES} places are selected at once.\n\n"
            f"{attribution['archive']} · {attribution['archive_license']} · "
            "Places © OpenStreetMap contributors, ODbL."
        )
    caveats
    return caveats


if __name__ == "__main__":
    app.run()

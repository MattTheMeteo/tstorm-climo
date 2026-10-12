# tstorm-climo

Build a precipitation climatology from HRRR analysis data.

The idea: the HRRR (High-Resolution Rapid Refresh) model produces an hourly
analysis of past weather. By stepping through the archive of HRRR analyses,
we can detect *when* rain fell at each grid point and accumulate that into
our own climatology — storm frequency by time of day, season, region, and
so on — without relying on precomputed products.

Data comes from [dynamical.org](https://dynamical.org)'s cloud-optimized
Icechunk Zarr archive (`noaa-hrrr-analysis`, 2014-10-01 to present, hourly)
via the `dynamical-catalog` Python package — no GRIB2 wrangling or bulk
downloads required.

## Installation

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 or newer is required;
uv fetches the pinned interpreter from `.python-version` automatically.

```sh
uv sync
```

The interactive marimo Studio app pulls in extra dependencies that are kept
out of the core CLI:

```sh
uv sync --group app
```

## Usage

```sh
uv run tstorm-climo summarize --start 2025-07-01 --end 2025-07-31
```

For each hourly analysis step in the range, the CLI reports the number of
grid points where precipitation was falling.

Named places (golf courses, beaches, ...) can be pulled from OpenStreetMap
via Overpass into a CSV:

```sh
uv run tstorm-climo fetch-places \
  --bbox 42.35 -71.20 42.95 -70.50 \
  --kind golf_course --kind beach \
  --named-only \
  --output places_north_shore.csv
```

`places_north_shore.csv` in this repository is a committed example built
that way (50 golf courses, 60 named beaches).

Build a reusable hourly rain artifact for later interactive queries:

```sh
uv run tstorm-climo build-observations \
  --places places_north_shore.csv \
  --start 2018-01-01 --end 2025-12-31 \
  --output artifacts/north-shore/rain-2018-2025
```

The new output directory contains `observations.parquet`, `places.parquet`,
and `manifest.json`. Observations use stable `place_key` values, UTC times,
the `rain_rate` variable in mm/h, and an explicit validity flag. The builder
does not apply a wet threshold. It will not overwrite an existing output
directory; choose a new path for each artifact build.

Rank places by how often it rains on them — the Chuck leaderboard:

```sh
uv run tstorm-climo rain-report \
  --places places_north_shore.csv \
  --start 2018-01-01 --end 2026-01-01 \
  --kind golf_course
```

Useful flags: `--month 6` restricts to one calendar month in local time,
`--threshold` sets the wet rate in mm/h (default 0.05), and `--output
report.parquet` writes the summary with a `.meta.json` sidecar recording
the archive, date range, threshold, reliability figure, and the CC-BY /
ODbL attribution. This is a fixed-range summary, not the hourly data
artifact needed to recalculate arbitrary date ranges in an interactive app.

The report always prints its **split-half reliability** alongside the
table. It is calculated for the selected threshold and period; read it
before trusting the ordering. Low reliability means the ranking may
mostly reflect which years were sampled. Places sharing a grid node are
marked `tied`.

## Development

```sh
uv run pytest              # run the test suite
uv run ruff check          # lint
uv run ruff format --check # style
uv run mypy                # type check
```

## Status

Implemented: HRRR data access, OSM place fetching, grid mapping,
per-place rain extraction and summaries, the `rain-report` CLI, and a
builder for reusable hourly rain Parquet artifacts. Snow analysis and a
hosted marimo app are not implemented. The planned app will calculate in
response to user choices from prepared artifacts, not fetch HRRR or Overpass
data on each interaction.

Pipeline: `places_north_shore.csv` (OSM via Overpass) → `Grid` maps each
place to its nearest HRRR node → `extract_series` reads one contiguous
grid block and returns an hourly series keyed by stable OSM `place_key` →
`summarize.place_summary` / `by_local_month` / `by_local_hour` /
`scorecard` produce the per-place tables.

Rain detection uses `precipitation_surface` (hourly-average rate) from
the dynamical.org HRRR analysis archive, converted from kg m-2 s-1 to
**mm/h** at read time so no caller handles the archive's unit.

Two caveats worth knowing before reading any output:

- **A 3 km grid cannot separate co-located places.** The 50 golf
  courses occupy only 47 grid nodes; 6 courses form 3 pairs that share a
  node and therefore get identical numbers. They are flagged `tied`.
- **Rankings can be dominated by sampling noise.** The report calculates
  `summarize.split_half_reliability` for the chosen period and threshold;
  see `PLAN.md` for measured results and limitations. A leaderboard should
  not be read without its reliability figure.

See `PLAN.md` for measurements, decisions, and open questions.

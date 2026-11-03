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

For a region-wide catalog with full OSM area geometries, build from a
Geofabrik PBF. The PBF is downloaded to temporary storage and removed after
the filtered catalog is written:

```sh
uv run tstorm-climo build-catalog \
  --region north-america/us/massachusetts \
  --kind golf_course --kind beach \
  --output artifacts/catalogs/massachusetts
```

Geofabrik is the only catalog source; `--region` is required. To publish a
finished release to S3, install the optional dependency with
`uv sync --extra catalog-s3` and add `--s3-bucket BUCKET`. Catalog releases
are uploaded under immutable keys; a region-specific `latest.json` pointer is
updated only after both the Parquet and manifest are uploaded.

Build a reusable hourly rain artifact for later interactive queries:

```sh
uv run tstorm-climo build-observations \
  --places places_north_shore.csv \
  --start 2018-01-01 --end 2025-12-31 \
  --output artifacts/north-shore/rain-2018-2025
```

`--places` takes either that CSV or a catalog's `places.parquet`, which
carries OSM footprint geometry.

The new output directory contains a place-clustered `observations/` of
sharded Parquet files, a `places.parquet` snapshot, and `manifest.json`.
Observations store only the columns that change hour to hour — `place_key`,
`time_utc`, `variable`, `value`, `valid` — sorted by place so a `place_key`
filter skips whole row groups. Everything constant per place (timezone,
anchor node, sampling identity) lives once in `places.parquet`. The builder
does not apply a wet threshold, and will not overwrite an existing output
directory; choose a new path for each artifact build.

The artifact is readable on its own, with no archive access: the reader
validates the manifest's schema version, holds the observations as a
**lazy scan**, and the query layer puts the selection into the plan, so
the filters and projection are pushed into the Parquet reader and only
the rows and columns a question needs are read. This is the path a
hosted service uses — prepared data in, answers out, with nothing
reading the archive while a user is waiting.

Rank places by how often it rains on them — the Chuck leaderboard:

```sh
uv run tstorm-climo rain-report \
  --places places_north_shore.csv \
  --start 2018-01-01 --end 2026-01-01 \
  --kind golf_course
```

The same report from a prepared artifact, with no archive read:

```sh
uv run tstorm-climo rain-report \
  --artifact artifacts/north-shore/rain-2018-2025 \
  --kind golf_course
```

Useful flags: `--month 6` restricts to one calendar month in local time,
`--threshold` sets the wet rate in mm/h (default 0.05), and `--output
report.parquet` writes the summary with a `.meta.json` sidecar recording
the archive, date range, threshold, reliability figure, and the CC-BY /
ODbL attribution. With `--artifact`, `--start`/`--end` are optional and
default to the artifact's own coverage; with `--places` they are
required, because the archive has to be read for an arbitrary range.
This is a fixed-range summary, not the hourly data artifact needed to
recalculate arbitrary date ranges in an interactive app.
`--places` takes the same CSV or catalog parquet as `build-observations`.

The report always prints its **split-half reliability** alongside the
table. It is calculated for the selected threshold and period; read it
before trusting the ordering. Low reliability means the ranking may
mostly reflect which years were sampled. Places read from the same *set*
of grid nodes get identical numbers and are marked `tied`.

## Development

### Running the app

The web app serves views from built artifacts — it reads no archive and
no network while running. The analysis library builds the artifacts; the
FastAPI backend serves them to the Next.js frontend (see PLAN.md Phase 6).

```sh
uv run tstorm-climo build-observations \
  --places places_north_shore.csv \
  --start 2018-01-01 --end 2026-01-01 \
  --output artifacts/north-shore/rain-2018-2025

uv run tstorm-climo build-gridded \
  --places places_north_shore.csv \
  --start 2025-11-01 --end 2026-11-01 \
  --output artifacts/north-shore/gridded \
  --chunk-hours 744
```

The observation artifact backs the leaderboard, monthly, and local-hour
views (attraction kind, place selection, threshold, and date range are
bounded by the artifact's own coverage); the gridded store enables the
wet-hours-per-node map over its last year. `--chunk-hours` defaults to
about a month. For a quick look without building anything real:

```sh
uv run python scripts/demo_artifact.py   # network-free synthetic pair
```

## Development checks

```sh
uv run pytest              # run the test suite
uv run ruff check          # lint
uv run ruff format --check # style
uv run ty check            # type check
```

CI runs all four plus a package build.

## Status

Implemented: HRRR data access, Geofabrik place-catalog builds, OSM place
fetching, grid mapping, footprint and point sampling, per-place rain
extraction and summaries, the `rain-report` CLI, reusable hourly rain
Parquet artifacts, and a gridded display store (Zarr V3 on Icechunk) with
its `build-gridded` CLI. The web application — a **FastAPI** backend over
the existing query/gridded functions plus a **Next.js + TypeScript**
frontend with **MapLibre GL JS + Deck.gl** maps — is under construction
(see PLAN.md Phase 6); the earlier marimo prototype (`app/rain_app.py`)
has been retired. A hosted deployment (artifact S3 publication,
`latest.json` pointer, container) is not yet built. The app calculates in
response to user choices from prepared artifacts, not by fetching HRRR or
Overpass data on each interaction.

Pipeline: places (OSM via Overpass as CSV, or a Geofabrik catalog with
geometry) → `Grid` maps each place to its HRRR nodes — every node its
polygon footprint covers, or the single nearest node when it has no
footprint → `extract_series` reads one contiguous grid block and returns an
hourly series keyed by stable OSM `place_key` → `summarize.place_summary` /
`by_local_month` / `by_local_hour` / `scorecard` produce the per-place
tables.

Serving reads the same way: `artifacts.load_observation_artifact` loads a
built artifact and validates its schema version, holding the observations
as a lazy scan → `query.select_observations` pushes a bounded selection (at
most `MAX_SELECTED_PLACES` places) down to the Parquet reader and collects
it → the same `summarize` functions produce the tables.
`query.artifact_report` bundles the summary, monthly, and hourly tables
plus the reliability figure, and `query.build_wetness_summary` produces the
one-row-per-place map. Nothing in that path opens a network connection, so
an app session costs no archive reads.

**The server does not hold the artifact.** Opening it reads nothing, and
each query reads only what it needs. On the full 2018–2025 North Shore
artifact (110 places, 7.7 M rows) a June view of the golf courses takes
0.3 s and peaks at 0.35 GB; the largest possible query — everything,
eight years — takes 2.6 s and peaks at 4.8 GB. So the *largest* query the
app allows is what sizes the container. `PLAN.md` has the full table.

Rain detection uses `precipitation_surface` (hourly-average rate) from
the dynamical.org HRRR analysis archive, converted from kg m-2 s-1 to
**mm/h** at read time so no caller handles the archive's unit.

Two caveats worth knowing before reading any output:

- **A 3 km grid cannot separate co-located places.** With point sampling
  (the committed `places_north_shore.csv` has no footprint geometry), the
  50 golf courses occupy 47 nodes and 6 of them form 3 pairs that share a
  node, so each pair gets identical numbers and is flagged `tied`.
  Footprint sampling may separate some of these.
- **Rankings can be dominated by sampling noise.** The report calculates
  `summarize.split_half_reliability` for the chosen period and threshold;
  see `PLAN.md` for measured results and limitations. A leaderboard should
  not be read without its reliability figure.

See `PLAN.md` for measurements, decisions, and open questions.

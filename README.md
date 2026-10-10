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

Requires [uv](https://docs.astral.sh/uv/). Python 3.14 is fetched
automatically.

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

## Development

```sh
uv run pytest      # run the test suite
uv run ruff check  # lint
```

## Status

Data access, place mapping, per-place extraction and per-place
climatology summaries are implemented; the `rain-report` CLI and the
marimo visualization layer are not.

Pipeline: `places_north_shore.csv` (OSM via Overpass) → `Grid` maps each
place to its nearest HRRR node → `extract_series` reads one contiguous
grid block and returns an hourly series per place →
`summarize.place_summary` / `by_local_month` / `by_local_hour` /
`scorecard` produce the per-place tables.

Rain detection uses `precipitation_surface` (hourly-average rate) from
the dynamical.org HRRR analysis archive, converted from kg m-2 s-1 to
**mm/h** at read time so no caller handles the archive's unit.

Two caveats worth knowing before reading any output:

- **A 3 km grid cannot separate co-located places.** The 50 golf
  courses occupy only 47 grid nodes; 6 courses form 3 pairs that share a
  node and therefore get identical numbers. They are flagged `tied`.
- **Most of the between-course spread is sampling noise.** See
  `summarize.split_half_reliability`, which measures it: ranking
  courses by `rate > 0` is near-useless (r = 0.11), so a leaderboard
  should not be published without its reliability figure alongside.

See `PLAN.md` for measurements, decisions, and open questions.

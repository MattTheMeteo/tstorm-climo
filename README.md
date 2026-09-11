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

Early scaffolding. Rain detection currently uses `precipitation_surface`
(hourly-average precipitation rate, kg m-2 s-1) from the dynamical.org
HRRR analysis archive; gridded climatological accumulation, golf-course
point extraction, and the marimo-based visualization layer are not
implemented yet.

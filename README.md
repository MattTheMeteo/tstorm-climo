# tstorm-climo

Build a precipitation climatology from HRRR analysis data.

The idea: HRRR (High-Resolution Rapid Refresh) analysis fields include
accumulated precipitation at each analysis time. By stepping through the
archive of HRRR analyses, we can detect *when* rain fell at each grid point
and accumulate that into our own climatology — storm frequency by time of
day, season, region, and so on — without relying on precomputed products.

## Installation

Requires [uv](https://docs.astral.sh/uv/). Python 3.14 is fetched
automatically.

```sh
uv sync
```

## Usage

```sh
uv run tstorm-climo summarize FILE [FILE ...]
```

Each `FILE` is an HRRR surface analysis file (GRIB2). For each file the CLI
reports the number of grid points where precipitation was falling.

## Development

```sh
uv run pytest      # run the test suite
uv run ruff check  # lint
```

## Status

Early scaffolding. The HRRR reader expects GRIB2 input via `cfgrib`
(and therefore a working `eccodes` install); data download/caching and
gridded climatological accumulation are not implemented yet.

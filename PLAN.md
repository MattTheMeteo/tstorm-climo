# PLAN.md

Roadmap and design notes for `tstorm-climo`: a tool for building weather
climatologies from NWP analysis data and serving them through a web
application. Users should be able to choose an attraction (for example, a
golf course, beach, or ski area), a weather variable, and a historical
period.

**Product direction (2026-11-02).** This is the first app of several. The
analysis lives in the reusable `tstorm_climo` package, which stays free of
web and UI dependencies; each app is a thin layer over it. The user-facing
product is a proper web application — a **FastAPI** backend serving the
existing `query.py`/`gridded.py`/`summarize.py` results as JSON and GeoJSON,
and a **Next.js + TypeScript** frontend with **MapLibre GL JS + Deck.gl**
for the map and a charting library for the time series. The marimo
prototype (`app/rain_app.py`) is retired; see Phase 6.

## Current state

### Data access

| module | what exists | notes |
| --- | --- | --- |
| `tstorm_climo/hrrr.py` | `load_precipitation(start, end, dataset_id, opener)`, `to_mm_per_hour(da)`, `open_analysis(opener)` | Opens dynamical.org's `noaa-hrrr-analysis` Icechunk Zarr (hourly analyses, 2014-10-01 → present) via `dynamical-catalog` and slices `precipitation_surface`. Returns a **`precipitation_mm_per_hour`** variable — see the units note below. Time range via xarray `slice`. `opener` is injectable for tests. `open_analysis` returns the raw dataset, for callers that need the 2-D lat/lon coordinates. |

Key facts we assume (verified against the live archive on 2026-10-10):
- No accumulated APCP exists in the analysis archive — "rain has fallen"
  is defined as hourly-average rate > threshold (very sensitive).
  `categorical_rain_surface` is available for a stricter
  rain-vs-drizzle/frozen distinction.
- **Analysis window starts 2018-01-01.** The archive is sparse before
  2018 and has small gaps through 2020, with NaNs where source data is
  missing. `NaN > threshold` is `False`, so pre-2018 gaps would silently
  score as *dry* and bias every climatology toward "it never rained".
  Restricting to 2018-01-01 onward removes the cause rather than
  patching the symptom. All reads must be bounded at that date; see
  `MIN_VALID_DATE`.
- The archive's grid is native HRRR Lambert Conformal Conic (`x`, `y` in
  metres), but it **also ships 2-D `latitude`/`longitude` coordinate
  arrays** on that grid (verified: `latitude`, `longitude`, both
  `(y, x) float32`). This removes the need for a `pyproj` transform
  entirely — a nearest-neighbour search in NumPy over those arrays is
  exact enough and dependency-free. `pyproj` is **not** installed and
  is not needed.
- `.sel(latitude=..., method="nearest")` does *not* work, but not for
  the reason assumed: xarray cannot build a `PandasIndex` on a 2-D
  coordinate. Handle the search ourselves.
- Grid geometry is regular: `dx = +3000.0 m`, `dy = -3000.0 m` exactly.
- Dataset chunking: time 2160 h (90 d) chunks, y/x 45 (135 km) — point
  reads are cheap, full-CONUS full-time reads are not.

**Unit convention (load-bearing — do not bypass).** The archive stores
`precipitation_surface` as a rate in **kg m-2 s-1**, where 1 unit is
1 mm/s = **3600 mm/h**. That is unintuitive by a factor of 3600 and has
already caused one wrong threshold in this project. So:

- Everything above `hrrr.py` works in **mm/h** and nothing else. Thresholds
  are written in mm/h and compared directly, with no local conversion.
- The kg m-2 s-1 → mm/h conversion happens **exactly once**, in
  `hrrr.to_mm_per_hour`, and `load_precipitation` returns a variable
  named `precipitation_mm_per_hour` with `units="mm h-1"`.
- **Slice before converting.** The archive read is lazy, so applying the
  conversion to an un-sliced variable forces materialization of the whole
  array (748 GiB against the live archive). `extract.py` slices time and
  space first, then converts; a regression test records the shape handed
  to the converter. Synthetic test datasets cannot catch this because
  they are already in memory — only a live run will.

### Places (summer leisure sites)

| module | what exists | notes |
| --- | --- | --- |
| `tstorm_climo/osm.py` | `KIND_TAGS`, `build_query(bbox, kinds)`, `run_query(query)` | Overpass API via **httpx**. Retains a descriptive User-Agent, retries on both HTTP statuses and transport errors, and always sets a request timeout. **Retained for small ad-hoc bbox exploration only** — see the place-catalog pull below, which supersedes it for bulk extraction. |
| `tstorm_climo/places.py` | `Place` (pydantic), `classify`, `from_overpass`, `deduplicate`, `haversine_km`, `to_polars`, `save_csv`, `load_csv`, `load_places`, `places_from_frame` | `Place.place_key` is the stable source-qualified OSM identity (`osm:{type}:{id}`). `load_places` reads either an Overpass CSV or a catalog `places.parquet` (preserving `geometry_wkb`). Ways/relations from Overpass use centers and omit full geometry; nodes use their position. Kind inferred from the element's own tags (Overpass does not say which selector matched). Dedupe collapses same-named places within 1 km (node+way duplicates) while keeping geographically distinct same-named places. |
| `tstorm_climo/climatology.py` | `summarize_grid(precipitation)` | Per hourly analysis step, counts CONUS-wide grid points above a threshold (default 0.05 mm/h). Takes an **already-loaded** dataset — I/O lives in `hrrr.load_precipitation`. Coarse-grained (whole dataset, no spatial subset); a global sanity check, not a per-place result. |
| `tstorm_climo/extract.py` | `extract_series(places, start, end, grid=, opener=)`, `sampling_cells(grid, places)`, `sampling_frame(places, sampling)` | Long-format per-place hourly series (`place_key, time, precipitation_mm_per_hour, valid, y_index, x_index, grid_lat, grid_lon, snap_distance_km, tz_name, sampling, n_cells, sampling_key`). `place_key` is independent of input order; summaries join by key. One contiguous block read, then gather in memory. Slices **before** converting units. Sorted by input place order, then time; the time column resets per place and is not globally sorted. `y_index`/`x_index`/`grid_lat`/`grid_lon`/`snap_distance_km` describe the place's **anchor** (nearest node to its representative point) and are not the nodes that were averaged; `n_cells` and `sampling_key` say what was, and `sampling_frame` holds the full node list. `sampling_cells` is a pure function of grid + places with no archive read, so callers can get the footprint without re-extracting. |
| `tstorm_climo/artifacts.py` | `build_hourly_observation_artifact(places, start, end, output_dir)`, `load_observation_artifact(directory)`, `ObservationArtifact` | Writes an immutable output directory containing a place-clustered `observations/` of sharded Parquet files, a `places.parquet` snapshot, and `manifest.json` (schema v3). The hourly table stores only the columns that change hour to hour (`place_key`, `time_utc`, `variable`, `value`, `valid`), sorted by place so a `place_key` predicate skips whole row groups; everything constant per place (timezone, anchor node, sampling identity) lives once in `places.parquet`. Current variable is `rain_rate` in mm/h; no threshold is applied. Existing output directories are not overwritten. The reader holds the observations as a **lazy scan** and checks `schema_version` before reading anything. |
| `tstorm_climo/query.py` | `select_observations(artifact, place_keys, ...)`, `select_all_observations(artifact, ...)`, `artifact_report(artifact, place_keys, ...)`, `selected_places(artifact, place_keys)`, `build_wetness_summary(artifact, ...)`, `matching_row_groups(...)` | The query layer over a loaded artifact. `select_observations` pushes a bounded selection (at most `MAX_SELECTED_PLACES`) into the lazy Parquet plan and collects it, joining the static per-place columns back so the result feeds `summarize` unchanged; `select_all_observations` is the uncapped batch variant. A requested range is clamped to the artifact's observed coverage, and the generic `value` column is renamed back to the quantity's own name. `artifact_report` bundles the summary, monthly, and hourly tables plus the reliability figure; `build_wetness_summary` produces the one-row-per-place map; `matching_row_groups` measures the pushdown. No network access and no marimo import. |
| `tstorm_climo/catalog.py` | `match_tags(tags, kinds)`, `match_source(kind)`, `deduplicate_places(...)`, `exclude_practice_facilities(...)`, `save_places(...)` | Shared catalog layer: kind-to-tag matching, duplicate and practice-facility cleanup, the normalized WKB schema, and the immutable release directory. Source-specific retrieval lives in `geofabrik.py` and `osm.py`. |
| `tstorm_climo/geofabrik.py` | `build_geofabrik_catalog(region, kinds, output_dir, bbox=)` | Downloads one regional raw `.osm.pbf` to temporary storage, filters nodes and assembled OSM areas by the shared tag rules, computes representative points, and saves a catalog release. The source PBF is discarded after the build. |
| `tstorm_climo/catalog_storage.py` | `publish_catalog_to_s3(catalog_dir, bucket, prefix=)` | Optional boto3-backed publishing: uploads release files under unique keys, then updates a source/region-scoped `latest.json` pointer. Install with `uv sync --extra catalog-s3`. |
| `tstorm_climo/gridded.py` | `build_gridded_artifact(places, start, end, output_dir)`, `load_gridded_artifact(directory)`, `GriddedArtifact`, `wet_hours(dataset, threshold=)`, `valid_hours(dataset)` | Gridded display store for the app's map views: the whole grid block the places sit on, one hour per step, as **Zarr V3 on local Icechunk**. Chunked along time (~744 h default) with the full block as the spatial chunk, so a windowed view spans one or two chunks. The store holds rates, not conclusions — wet-hours-per-node over any window and threshold is one reduce at query time (`NaN` never counts as wet; `valid_hours` shows how much data a node received). Icechunk versions each build, so a later refresh appends hours without losing the old view. Manifest sidecar mirrors the observation artifact's conventions. |
| `tstorm_climo/summarize.py` | `prepare_observations`, `place_summary`, `by_local_month`, `by_local_hour`, `scorecard`, `split_half_reliability` | Per-place climatology in local time. Default threshold 0.05 mm/h, shared with `summarize_grid`. No unit conversion — input is already mm/h. `prepare_observations` joins the places, converts to local time, and marks the wet hours **once**, so a report does not repeat three full passes over the frame; the summary functions read from that prepared frame. Ties are broken on `place_key`, so the output order is reproducible. |
| `tstorm_climo/provenance.py` | `build_metadata`, `write_metadata`, `format_caveats` | JSON sidecar recording archive, date range, threshold, reliability and the CC-BY / ODbL attribution. Makes a published number reproducible and keeps licence obligations in one place. |
| `tstorm_climo/grid.py` | `Grid.from_dataset(ds)`, `Grid.locate`, `locate_places`, `cover_cells`, `bounds()`, `spacing_km`, `max_snap_km`; `GridNode`/`GridCell`/`FootprintCell` | Maps a place onto HRRR nodes using the archive's own 2-D lat/lon arrays, so no `pyproj`. `locate*` snap to the nearest node; `cover_cells` returns every node inside an OSM footprint via DuckDB `spatial`. `max_snap_factor` and `batch_nodes` are constructor keywords. |
| `tstorm_climo/cli.py` | `build-catalog --region/--kind/--bbox/--output/--s3-bucket`, `build-observations --places/--start/--end/--output`, `build-gridded --places/--start/--end/--output/--chunk-hours`, `rain-report --places|--artifact ...`, `summarize ...`, `fetch-places ...` | argparse subcommands. `build-catalog` pulls a Geofabrik region (`--region` required); S3 publishing is optional. `build-observations` creates the reusable hourly rain artifact. `build-gridded` creates the Icechunk display store for the app's map views. `rain-report` writes a **derived summary**, not hourly observations, and reads either the archive (`--places`) or a prepared artifact (`--artifact`). |

Committed data:
- `places_north_shore.csv` — 50 golf courses + 60 named beaches for the
  North Shore bbox (42.35–42.95, −71.20−−70.50), generated by
  `fetch-places --named-only`.

### Place-catalog sources: Geofabrik and Overpass

Overpass is built for ad-hoc queries against a shared service, not bulk
extraction. It has slot allocation, rate limits, and a usage policy that
discourages bulk pulls; a nationwide extract is exactly the shape it
handles worst. Observed directly in this project: HTTP 400s and 504s,
and silent undercounts (a known-good bbox returned 1 course instead of
~5).

**Geofabrik raw `.osm.pbf` is now the only bulk-capable path.**
`build-catalog --region ...` downloads a regional extract, filters it
locally with pyosmium, constructs way and multipolygon area geometry, and
stores only the matching catalog. The PBF is temporary and is not kept in
the release or uploaded to S3. Nodes and assembled areas are included; open
ways are intentionally excluded so ski-piste lines are not mistaken for ski
resorts. Geofabrik's current downloads include a US extract of about
**11.4 GB** and a Massachusetts extract of about **296 MB**
([US](https://download.geofabrik.de/north-america/us.html),
[Massachusetts](https://download.geofabrik.de/north-america/us/massachusetts.html),
checked 2026-10-12). The raw PBF includes the region's OSM data; the free
prebuilt Shapefile/GeoPackage products are curated subsets and are not used
for this path.

`build-catalog` writes a local immutable release; with `--s3-bucket`, it
uploads the Parquet and manifest under unique keys and updates a
region-scoped `latest.json` only after both uploads finish. The PBF itself
is not uploaded. It is a manual CLI workflow today; scheduling a refresh is
future deployment work.

#### LayerCake: removed

The **LayerCake POI GeoParquet** path
(`https://data.openstreetmap.us/layercake/pois.parquet`, 62.3 M features,
4.7 GB, refreshed daily, ODbL) used to be the default source for
`build-catalog`. DuckDB read it remotely over HTTP range requests, so the
4.7 GB file was never downloaded whole, and a single-column remote query
found all **50/50** committed North Shore golf courses in ~30 s. That is a
real advantage and the reason it was ever the default.

It was removed because its beach coverage in this region is effectively
zero (measured below, and 0 of 60 names in common with our Overpass CSV),
while being the *default* meant `build-catalog --kind beach` silently
produced a catalog of one beach. The measured golf-course performance is
recorded here in case a future remote source should be evaluated against
it; the corresponding code (`fetch_places`, `build_query`, and the DuckDB
SQL token helpers that fed them) is deleted, along with the `--source
layercake` CLI choice.


This is what retired LayerCake. `natural=beach` is the primary beach tag
and `leisure=beach_resort` a real but sparse secondary; LayerCake queried
both and still came back nearly empty for this region.

Same North Shore bbox (42.35, -71.20, 42.95, -70.50):

| Source | Beaches |
|---|---|
| Our committed Overpass CSV | **60** |
| LayerCake `natural=beach` | 0 |
| LayerCake `leisure=beach_resort` | 1 ("Waterfront") |
| Names in common | **0** |

LayerCake carries beaches elsewhere (2,770 `natural=beach` worldwide;
present in a Cape Cod box), so this was **regional coverage, not a broken
query** - verified by pulling the same box twice. LayerCake is now removed;
see "LayerCake: removed" above.

### Ski areas: tagging is fragmented, but curatable (measured)

Winter areas are **not** tagged consistently. `leisure=ski_resort` is
effectively unused. Two live conventions carry the data, and neither
alone is sufficient:

| Path | US features |
|---|---|
| `sport` contains `skiing`/`ski`/`ski_jumping`/`snowboarding` | 413 (sport-only) |
| `landuse` contains `winter_sports` | **89 (winter-only)** |
| both tags present on one feature | 51 |

(`sport_only` / `winter_only` / `both` over a US bounding box; the box
includes edges of southern Canada and northern Mexico, so these are
candidates for inspection, not a curated US total.)

**Neither tag alone works.** The 89 `winter_only` features include real,
well-known resorts a `sport`-only filter misses entirely — e.g. **Afton
Alps** (Virginia) and **Badlands Snow Park** (South Dakota), both with
empty `sport` and `leisure`. This is the Sugarbush case: resorts tagged
only `landuse=winter_sports`. Dropping the `landuse` path would lose
~14% of identifiable areas.

Matching rules that matter (learned the hard way — see pitfalls below):
- `sport` and `landuse` are **semicolon-joined lists**
  (`skiing;ice_skating`, `skiing;cycling;fishing`). Match **tokens**,
  not whole-string equality, or real resorts are lost.
- A prefix match `LIKE 'ski%'` also catches **`skittles`** (an old form
  of bowling) — 236 false positives worldwide in a first attempt.
- Typos already exist in the wild: `landuse=winter_sport` (1 feature),
  `winter_sports;grass` (1), `sport=skiin`.

Even the union is imperfect: `Ausblick Ski Hill` appears **twice** — once
fully tagged, once bare — so duplicate features for one physical resort
exist. Remaining noise includes facilities rather than resorts
(`Biathlon Range`, `Adventure Center`).

**Decision:** `ski_area` is curatable but not purely mechanical. Ship a
`ski_area` kind sourced from the union of both paths, plus a
checked-in **curation overlay** for corrections the tags cannot express
(duplicate features, facility-vs-resort). Do not claim completeness.

Tooling: `uv` + Python 3.14, `pytest`, `ruff` (E/F/I/UP/B/SIM), `ty`.
Declared core dependencies: `dynamical-catalog`, `xarray`, `polars`, `pydantic`,
`numpy`, `duckdb`, `httpx`, `icechunk`, `tzfpy`, `osmium`. The web app adds a
FastAPI backend and a Next.js + TypeScript frontend (Node toolchain), kept out
of the core package. Deliberately NOT used: GRIB2/cfgrib, runtime OSM
fetches in the analysis path.

- `numpy` is imported directly by `grid`, `extract`, `summarize`, `places`,
  and `climatology`, so it belongs in `pyproject.toml` even though every
  current install resolves it transitively.
- `duckdb` is needed twice: `grid.cover_cells` uses its `spatial` extension
  for point-in-polygon, and `geofabrik` uses it for spatial joins.
- `icechunk` backs the local Zarr V3 gridded display store (`gridded.py`).
- `osmium` is Geofabrik-only; `boto3` is the optional `catalog-s3` extra.
  It used to be listed in *both* the core dependencies and the extra,
  which made the extra a no-op — the core install always had boto3, and
  `uv sync --extra catalog-s3` installed nothing extra. `boto3` is now
  in the extra only, matching the documented intent, and
  `catalog_storage` imports it lazily so the core CLI still works and
  names the extra if S3 publishing is asked for.

Testing strategy: unit tests run on synthetic xarray datasets via an
injected `opener`; no network in the fast test suite.

## Plan — build order

### Phase 1: Places on the grid (the mapping core)

1. ~~**`tstorm_climo/grid.py` — lat/lon → grid transform.**~~ **DONE.**
   - `Grid.from_dataset(ds)` wraps the archive's 2-D lat/lon arrays;
     `Grid.locate(lat, lon)` / `locate_places(places)` return a
     `GridCell(y_index, x_index, grid_lat, grid_lon, distance_km)`.
   - No `pyproj` dependency (see the corrected data-access notes above).
   - The search is windowed to the places' bounding box, so cost scales
     with the region of interest rather than the domain, and batched to
     bound peak memory.
   - Longitude differences are scaled by `cos(lat)` so the search
     minimises ground distance rather than degree distance. Verified
     against a naive full-grid brute force: the weighting picks the
     genuinely closer node for 6 of the 110 places, by up to 0.27 km.
   - Off-grid places raise `PlaceOffGridError` rather than silently
     snapping to a domain-edge node.
   - **Validation result (live archive):** all 110 committed places snap
     within **1.99 km** (mean 1.11 km) of a node, against a geometric
     ceiling of half a cell diagonal ≈ 2.11 km. Every place falls inside
     its own cell; coasts and domain edges are not a problem.
   - **Known resolution limit:** the 110 places occupy only **86 distinct
     grid nodes**; 18 nodes hold more than one place, with up to 4 on a
     single node. A 3 km analysis grid cannot separate co-located
     places. Scoped to the golf leaderboard that actually matters:
     - The 50 courses occupy 47 nodes. **44 courses sit alone** on their
       node; **6 courses form 3 unrankable pairs** — Fresh Pond /
       Oakley (1.76 km), Garrison Par 3 / Northfields (1.36 km), and
       King Rail / Reedy Meadow (1.57 km).
     - A further 7 courses share a node with a *beach* (Essex County
       Club, Rockport, Bass Rocks, Winthrop, Winchester, Tedesco, New
       Meadows). This does **not** disturb the course ranking — a beach
       sharing a course's number is harmless. Worth knowing only so the
       beach chart doesn't look like it is contradicting the course one.
     - Worst single cell overall is `y=272 x=1621` (42.5770, -70.7472),
       holding 4 places: Essex County Club plus Black, Graves and
       Singing Beaches, spread 2.64 km.
     - **These counts are point-sampled** (the committed places CSV has
       no footprint geometry) and were measured on the anchor node.
       `cell_places` now keys on the sampled node *set*, so they do not
       describe footprint-sampled places and must be recomputed — see
       item 7.
   - A 3×3 neighbourhood average was **considered and rejected**: it
     costs 9× the reads and does not separate co-located places, since
     they share the window too. It would only blur the grid, not sharpen
     the answer.
   - **Polygon footprints.** `Grid.cover_cells(geometry_wkb)` returns the
     nodes whose centres fall inside an OSM `POLYGON`/`MULTIPOLYGON`
     footprint, via DuckDB's `spatial` extension, as
     `FootprintCell(..., weight)` with equal weights summing to 1.0. An
     empty result means fall back to `locate()` -- a small polygon between
     nodes covers no centre. This is the cheap half of item 7 below: no
     `pyproj`, no clipping, because it samples node centres rather than
     area-weighting the polygon.
   - **Structure pass over `grid.py`.**
     - `GridNode` is the shared pydantic base carrying `y_index`,
       `x_index`, `grid_lat`, `grid_lon` and `key`; `GridCell` adds
       `distance_km`, `FootprintCell` adds `weight`. Previously the two
       models duplicated four fields and an identical `key` property.
     - `max_snap_factor` and `batch_nodes` are `Grid` constructor keywords
       instead of mutable module globals, so the batching test builds two
       grids rather than monkeypatching. Values unchanged (`0.75`,
       `250_000`).
     - `_window`'s slack derives from `max_snap_km` rather than
       `spacing_km`, with the longitude margin widened by `1 / cos(lat)`
       to match the correction `_locate` already makes when ranking nodes.
       **No bug was demonstrated** -- exhaustive brute force on regular
       and converging LCC-like grids agrees with the windowed search in
       every in-range case, and the old margin already exceeded the
       half-cell-per-axis bound. The change is for self-consistency: the
       margin is tied to the tolerance that decides accept/reject, so a
       custom `max_snap_factor` cannot silently outrun it.
       `test_windowed_search_agrees_with_brute_force` guards this.
     - `_spatial_connection` registers a dict of NumPy arrays instead of a
       pandas `DataFrame`. **pandas was never a declared dependency** --
       it only resolved transitively; polars would have needed `pyarrow`,
       which is not installed. The finite-node filter stays in NumPy.
     - No measurable cost: on a 1050x1050 domain the search window for the
       110 committed places is 39x29 nodes, unchanged, since the places'
       own spread dominates the slack.
2. ~~**Point/neighborhood extraction.**~~ **DONE** (`tstorm_climo/extract.py`).
   - Source field: **`precipitation_surface`** (GRIB short name `prate`),
     units kg m-2 s-1, "average precipitation rate since the previous
     forecast step". It is a **rate**, not an accumulation — the archive
     carries no APCP, so accumulating across steps is our job.
   - Nearby fields that are *not* substitutes: `snowfall_surface`
     (`tsrate`, m s-1 of snow **depth**, ~10× density difference);
     `composite_reflectivity` (dBZ, the independent radar check);
     `precipitable_water_atmosphere` (moisture, not precipitation).
    - **Long format**: one row per (place, hour), columns
      `place_key, time, precipitation_mm_per_hour, valid, y_index,
      x_index, grid_lat, grid_lon, snap_distance_km, tz_name`. Sorted by
      input place order then time; the time column resets per place and
      is *not* globally sorted.
   - **Rates converted to mm/h at the boundary** (×3600), so the
     kg m-2 s-1 unit never reaches the output.
   - **`valid` flag** carried explicitly: 99.70% valid over 2018–2026
     for the golf courses; the remaining 0.30% (10,400 rows) are
     archive gaps, not zero rain.
   - No threshold applied here — that belongs to the summary layer.
   - Measured: 50 courses × 8 years = 3.5 M rows in **14 s**.
   - Per place, the hourly series is the mean over the grid nodes its
     footprint covers, or the nearest grid point when it has no polygon.
     The 3×3 neighborhood was considered and rejected (see step 1 above).
     Footprint sampling is described in item 7.
   - Read the places' bounding box as one contiguous block and index in
     memory, rather than issuing a scattered read per place (the archive
     chunks y/x at 135 km, so point reads are cheap, scattered reads
     are not).
3. ~~**Per-place climatology summary.**~~ **DONE**
   (`tstorm_climo/summarize.py`). Default threshold **0.05 mm/h**,
   overridable per call; the
   `> 0` end of the range stays available for "the turf was damp".
   - `place_summary` → per place: `valid_hours`/`coverage` (data
     quality), `wet_hours`/`wet_fraction`, `rain_mm` (depth; each step
     is one hour so mm/h sums to mm), `tied`/`cell_places` (places
     reading the same set of grid nodes), and a dense `rank` where 1 is
     driest.
   - `by_local_month`, `by_local_hour` → slices in **local** time,
     normalized by hours observed (a local hour seen 12 times and wet 6
     times is 0.5, not 1.0).
   - `scorecard(kind=, month=)` → the Chuck table, driest first, tied
     places adjacent.
   - Local time is converted by partitioning on `tz_name` and
     converting per group — polars' `convert_time_zone` takes a static
     string, not a column. Verified against a fixed-offset model: 0
     mismatches in summer (EDT, UTC−4) and 0 in winter (EST, UTC−5).

   **Exploratory results at 0.5 mm/h, 50 courses × 2018–2026 (12.9 s
   end to end; 0.5 mm/h is not the current default):**
   - Overall wet fraction spans only **4.9%–5.4%** across all 50
     courses. The spread is ~5.8 standard errors (SE ≈ 0.084 pp with
     ~70k valid hours), so it is statistically real — but see the
     reliability warning below, which is the important part.
   - June alone spans **2.5%–3.0%**.
   - **Diurnal signal is weak**: by local hour, wet fraction varies only
     4.6%–5.5% across the day, with a shallow maximum around 07:00 and
     a minimum mid-afternoon. Not a pipeline artifact — verified in
     summer-restricted data at four thresholds (max/min ratio 1.30 at
     `> 0`, 1.73 at `> 0.5`, 2.12 at `> 2`), and local-time assignment
     checked against a fixed-offset model in both DST seasons.
     Plausible meteorology: much of New England summer rain is
     stratiform or frontal rather than convective, so the classic
     afternoon-convection peak is muted.

    ### `categorical_rain_surface` compared (item 8, mostly answered)

   `crain` (0/1, "0=no; 1=yes", `step_type: instant`) was evaluated over
   the same 50 courses × 2018–2026:
   - **It is a rate cutoff in disguise.** Agreement with `rate > 0.01`
     is 97.0%, and `crain == 1` cases sit at a median rate of
     0.20 mm/h with only 7.1% below 0.01 mm/h. It is close to, but not
     identical to, a ~0.01 mm/h threshold.
   - It marks **more** hours wet than any rate threshold of interest:
     11.8% of hours, vs 17.6% at `> 0`, 13.1% at `> 0.01`, 5.2% at
     `> 0.5`. So it behaves like a "trace of rain" detector, close to
     the wet end of the range.
   - 1.8% of `crain == 0` hours still carry `rate > 0.1 mm/h`, so it is
     not a clean restatement of the rate.
   - NaN positions are **identical** to the rate field's, confirming the
     same archive gaps rather than independent data.
   - Conclusion: it does not replace a threshold decision; it mostly
     relocates it. Keep it as a cross-check in Phase 2, not as the
     primary definition.

   ### ⚠ The ranking is mostly noise — read this before publishing

   Split-half reliability (correlating each course' wet fraction from
   the first half of the record against the second half) measures how
   much of a course-to-course difference is stable signal rather than
   sampling noise:

   | Wet definition | Split-half r |
   |---|---|
   | `crain == 1` | **0.66** |
   | `rate > 0.5 mm/h` | **0.40** |
   | `rate > 0.01 mm/h` | −0.10 |
   | `rate > 0` | 0.13 |

   At this tested 0.5 mm/h threshold, the between-course spread is
   **substantially sampling noise**, and
   the apparent 4.9%–5.4% range is mostly which eight years you happened
   to look at. `crain == 1` is the most stable of these, and
   `rate > 0.5` is moderately stable; `rate > 0` is no better than a
   coin flip for ordering courses.

   Consequence: the leaderboard must **not** present single-year or
   low-threshold rankings as meaningful. Report the reliability figure
   next to any ranking, prefer `crain`/higher thresholds for stability,
   and say plainly that a 3 km analysis archive cannot resolve these
   courses. This is the most important finding in the project so far
   and outranks the point differences.

   ### ⚠ June — the question Chuck actually asked — is the least reliable

   Split-half reliability by **local calendar month**, 50 courses,
   2018–2026, split by year (2018–2021 vs 2022–2026):

   | Month | 0.05 | 0.10 | 0.50 | 1.00 | 2.00 mm/h |
   |---|---|---|---|---|---|
   | Jan | 0.64 | 0.75 | 0.10 | −0.60 | 0.67 |
   | Mar | −0.43 | −0.35 | 0.68 | 0.69 | 0.64 |
   | **Jun** | 0.16 | 0.17 | **0.28** | 0.14 | 0.14 |
   | Jul | 0.39 | 0.55 | 0.62 | 0.64 | 0.62 |
   | Sep | −0.47 | −0.22 | 0.04 | −0.02 | 0.06 |
   | **All months (mean)** | 0.06 | 0.14 | 0.18 | 0.21 | 0.27 |

   **June is worse than the all-year figure of 0.41** — best case 0.28,
   and no threshold does better than 0.28. The arithmetic explains why:
   in June at 0.5 mm/h each course sees 144–174 wet hours, a spread of
   only **30 hours**, against Poisson noise of **±12.6 hours** on ~158
   events. The true spread is barely above noise, so the ordering does
   not survive a different date range.

   Caveats on these numbers: individual month/threshold cells are
   themselves unstable (Sept is −0.47, Jan 0.75), so read them as
   "this cell is uninformative", not as precise estimates. Splitting by
   year is the harshest split; adjacent-year splits would look better
   and would overstate reproducibility.

    These reliability figures describe the tested threshold and periods;
    the CLI recomputes reliability for each selected threshold/date range.
    **Design consequence for the visualization:** lead with the all-year
   view (r = 0.41) labelled low-confidence; present June as a
   *distribution* of wet hours rather than a ranking; and look for
   stable differences at a **coarser grouping** (regions, or the wet
   half vs the dry half) rather than individual courses. Ranking 50
   courses in June would need ~10× more record, a larger effect, or
   coarser units — not better code.
4. ~~**CLI**: `rain-report`~~ **DONE.** Prints the ranked table
   (driest first), with `--month N` for a local-time month slice,
   `--threshold` in mm/h, `--kind` to filter, and `--output` for parquet
   or CSV plus a `.meta.json` provenance sidecar.
   - The **reliability figure is always printed with the table**, along
     with the caveats and both licence attributions. A leaderboard
     without it is misleading, so it is not optional output.
   - `--places` accepts a catalog `places.parquet` as well as a CSV, so
     the table can be built from footprint geometry rather than being
     stuck on nearest-node samples (see item 7).
    - This produces a derived summary for fixed reports. It is not the
      interactive app's source artifact: a summary cannot be re-aggregated
      correctly for an arbitrary date range. The app needs hourly
      observations, built separately in Phase 3.

### Phase 2: Variable and event definitions

5. **Define supported weather variables.** Keep attraction kind (where)
   independent from variable (what). For each variable, record its source
   dataset/field, normalized output unit, conversion, missing-data rules,
   and summary semantics. Rain uses the shared 0.05 mm/h default threshold;
   validate the snow field's units and time-step meaning before exposing it
   as a user choice. Do not assume rain and snow amounts can be summarized
   identically.
6. **Rain-event definition.** If event counts are added, merge consecutive
   wet hours into discrete events (minimum duration/separation), per place.
   Track event start hour-of-day, duration, and accumulation where the
   source supports it. Keep these rules as analysis options, not baked into
   the observation artifact.
7. ~~**Spatial averaging over a course footprint.**~~ **Landed**
    (node-centre averaging). A generic 3x3 window is identical for any two
    points in the same cell, so it blurs without separating. True area
    weighting of the OSM polygon would need `pyproj` + polygon-clipping
    and re-fetching geometry -- two new dependencies to resolve 3 pairs out
    of 50 courses. Not worth it; revisit only if a finer dataset appears.
    - `Grid.cover_cells()` gives node-centre-in-polygon for none of that
      cost, and `extract_series` averages covered nodes with equal
      weights, falling back to the nearest node for point or uncovered
      places.
    - **The footprint is recorded, not just the anchor.** Extraction emits
      `sampling`, `n_cells`, and `sampling_key` alongside the anchor node
      columns, and `extract.sampling_frame` can materialize the full
      per-(place, node) table when needed. In observation artifacts
      (schema v3), those static per-place sampling columns (`sampling`,
      `n_cells`, `sampling_key`, and the anchor node) are stored once per
      place in `places.parquet` and joined back at query time. The anchor
      columns kept their old meaning, which is the trap they always were:
      a footprint place sitting exactly on its anchor reports
      `snap_distance_km = 0` while averaging several nodes. `n_cells`
      exposes that.
    - **`cell_places` / `tied` are re-keyed onto `sampling_key`.** They
      grouped on the anchor node, which stopped describing the sampling
      once footprints applied -- two courses sharing an anchor but
      averaging disjoint node sets were called unrankable, and vice versa.
      The honest test is whether two places read the *same* node set.
      Places sharing only some nodes are coupled but distinguishable, so
      they are not marked tied; a Jaccard overlap metric is a reasonable
      follow-up. **The Phase 1 resolution numbers therefore no longer
      apply to footprint-sampled places.**
    - `rain-report` reads through `load_places` as well, so both it and
      `build-observations` accept a catalog parquet and average footprints.
      The printed caveat text was updated to match.
8. **Threshold sensitivity study.** Compare wet fractions across
   thresholds (0, 0.1, 0.5, 1 mm/h — see the units note in open
   questions) and against `categorical_rain_surface`, which may be a
   more honest rain-vs-drizzle boundary than any rate cutoff. Keep the
   chosen 0.05 mm/h product default explicit, and show reliability so users
   can see how conclusions change with threshold.

### Phase 3: Offline hourly observation artifacts

9. ~~**Build hourly artifacts for interactive queries.**~~ **DONE (schema v3).**
   `build-observations --places/--start/--end/--output` calls
   `extract_series` and writes a new immutable output directory containing
   a place-clustered `observations/` directory (`shard-*.parquet`),
   `places.parquet`, and `manifest.json`. The hourly observation shards
   store only the five columns that vary hour to hour (`place_key`,
   `time_utc`, `variable`, `value`, `valid`), sorted by `place_key` then
   `time_utc` so a `place_key` predicate skips whole shards and row
   groups. Everything constant per place (place identity, `geometry_wkb`,
   timezone, anchor node, and `sampling` / `n_cells` / `sampling_key`)
   lives once in `places.parquet` and is joined back by `query.py` at
   read time. The current builder supports `rain_rate` in mm/h and does
   not apply a wet threshold. Add additional variables after their source
   fields and units are validated.
10. **Harden and describe the artifact.** The v3 manifest records the
    dataset/source field, normalized units, requested and observed date
    range, build time, place and row counts, valid fraction, shard count,
    observation/static column lists, and attribution. Keep `place_key`
    and validity information end-to-end.
    - **`schema_version` is validated on read** —
      `load_observation_artifact` requires `schema_version == 3` and
      refuses older or newer bundles rather than misreading them.
    - **Still open:** add software/code version to the manifest and assess
      completeness against the expected hourly timeline, since a timestamp
      missing from the archive entirely is not currently counted in
      `valid_fraction`.
    - **Place catalog refresh is an offline build step.** Use Geofabrik
      PBF for regional OSM filtering (`build-catalog`) and Overpass only
      for small ad-hoc lookups (`fetch-places`). Never call these sources
      in response to app interactions. For broad coverage, build one
      state/region at a time and merge by `place_key`. Keep unnamed but
      otherwise valid attractions with a generated display label, or
      record their exclusion as an explicit curation rule.
    - **Publish the catalog snapshot as one release.** The catalog Parquet
      contains scalar fields and a WKB geometry column on each row, and
      the observation artifact's `places.parquet` snapshots those same
      columns alongside the static grid-sampling fields used for that
      extraction.
    - `build-catalog` writes a new local release directory and refuses to
      overwrite it. With `--s3-bucket`, it uploads the catalog Parquet and
      manifest under unique release keys, then advances a source/region-
      scoped `latest.json` pointer. The raw Geofabrik PBF is discarded after
      parsing. Retain older S3 releases for rollback and provenance. The CLI
      build is implemented; a scheduled cloud job and atomic refresh process
      are future deployment work.
11. ~~**Keep archive I/O out of app interactions.**~~ **DONE.** Build or
    refresh artifacts separately (manual refresh is adequate initially).
    The app does not call HRRR or Overpass when a user changes a selector.
    The measured North Shore block remains small; no database or
    distributed cache is justified.
    - **The serving path is live.** `artifacts.load_observation_artifact`
      validates `schema_version` and holds `observations/*.parquet` as a
      **lazy scan**, so opening an artifact reads only `manifest.json` and
      the small `places.parquet` table. `query.select_observations` pushes
      a bounded selection (at most `MAX_SELECTED_PLACES = 20` places) into
      the lazy plan, where Polars prunes shards and row groups via Parquet
      statistics, then collects and joins the static per-place columns
      back. `query.artifact_report` bundles the summary, monthly, and
      hourly tables with the reliability figure, and
      `query.build_wetness_summary` produces the one-row-per-place map.
      `rain-report --artifact` exercises this same path with zero
      archive reads; the web app's API (Phase 6) calls it directly.
    - **Date-bound semantics are fixed and shared.** A date-only bound
      covers the whole day, matching the extraction path's slice
      behaviour, and a requested range is clamped to the artifact's
      observed coverage so a request for years the artifact does not hold
      returns the covered part rather than nothing.
    - **Artifact resolution:** the web app's backend resolves its
      observation artifact and optional gridded store from the
      `TSTORM_CLIMO_ARTIFACT` and `TSTORM_CLIMO_GRIDDED` environment
      variables (or an S3 `latest.json` pointer). Publishing observation
      artifacts to S3 with a `latest.json` pointer (matching
      `catalog_storage.py`) remains a deployment follow-up.
12. **Adapt to archive format changes.** dynamical.org keeps
    `analysis` (time-optimized), `analysis-virtual` (map-optimized), plus
    forecast and 18 h low-latency variants. Confirm which is best for
    point time series and document the choice in the artifact manifest.
13. **Attribution and licensing.** The catalog is CC BY 4.0 — make sure
     published outputs carry the citation ("NOAA HRRR processed by
     dynamical.org from NOAA Open Data Dissemination archives") plus OSM
     attribution (ODbL) on any published notebook/site.

### Phase 4: Interactive app — marimo prototype (retired)

> **Superseded.** The marimo app below was the first working UI and proved
> the serving path (lazy Parquet scan, `query.artifact_report`, the Icechunk
> gridded store). It is retired; the product is the web application in
> **Phase 6**, which reuses the same package functions unchanged. The
> measurements in this phase still size the server.

#### Gridded display store: Icechunk (built & wired)

Map views need the grid, not a per-place table. `gridded.py` writes the
places' whole grid block as one hourly `precipitation_mm_per_hour` array
(Zarr V3) into an immutable local Icechunk store next to a
`manifest.json` sidecar, mirroring the observation artifact's
conventions:

- **The block is the extraction block.** It comes from the same
  `extract.sampling_cells`/`sampling_bounds` helpers as the per-place
  artifacts, so one archive read can serve both formats.
- **The store holds rates, not conclusions.** Wet hours per node are
  counted at query time (`wet_hours`, threshold as an argument; NaN
  never counts as wet, and `valid_hours` shows where data is missing),
  so one store serves every window and threshold the app asks for at
  North Shore scale.
- **Time is the chunk axis** (~744 h default; the full block is one
  spatial chunk), so a one-year wet-hours view touches a handful of
  chunks. The dims carry the archive's global node indexes, so app
  views can be joined to place anchors without a lookup table.
- **Icechunk, not a flat Zarr directory**: a later refresh appends new
  hours as a new commit and older views keep working.

Wired into both the CLI and the app: `build-gridded` builds the store,
and `app/rain_app.py` renders the wet-hours-per-node map for the store's
last year when `TSTORM_CLIMO_GRIDDED` is set.

#### Deployment decisions (settled, not yet built)

- **Container:** a plain, platform-agnostic Dockerfile serving the web
  app (FastAPI serving the API and the built Next.js frontend) and
  listening on `$PORT`. No platform-specific manifest (no `fly.toml`, no
  `render.yaml`), so the same image runs on Fly/Render/Railway/ECS/
  Kubernetes or a plain VPS with Compose.
- **Artifact delivery: S3 releases + startup pull or direct scan.**
  Observation artifacts get the release treatment the catalog releases
  already have — immutable keys plus a `latest.json` pointer. The server
  can either download the resolved artifact to local storage at boot
  (keeping query latency local and image size small) or pass an `s3://`
  URI directly to `load_observation_artifact` for lazy remote scans. It
  needs `publish_observations_to_s3` and a reader for the `latest.json`
  pointer, neither of which exists yet.
- **Not decided yet:** the container's memory limit. Interactive queries
  are now bounded to `MAX_SELECTED_PLACES = 20` places on the slimmed
  schema v3 shards, which substantially reduces per-query memory
  compared to the uncapped 110-place benchmark below; measure the
  20-place peak on the live artifact before setting the container limit.

14. ~~**Factor a reusable report/query function.**~~ **DONE**
    (`tstorm_climo/query.py`). `select_observations` takes observation data
    plus a selected variable, date range, attraction kind, and place keys
    (capped at `MAX_SELECTED_PLACES = 20`) and returns the filtered
    observations; `artifact_report` returns the per-place summary, the
    monthly and local-hour climatologies, and the ranking reliability for
    the selected range and threshold. It is independent of marimo and of
    network I/O, so the CLI, the tests, and the app share the same
    analysis behaviour.
    - Artifact paths are resolved outside `query.py` via environment
      variables (`TSTORM_CLIMO_ARTIFACT`, `TSTORM_CLIMO_GRIDDED`) in the
      app or `--artifact` on the CLI.
15. **Build the interactive view with marimo.** **First version
    built** (`app/rain_app.py`): attraction kind, place multiselect
    (capped at `MAX_SELECTED_PLACES`), wet threshold, and a date range
    bounded by the artifact's own coverage; the leaderboard table with
    tie flags and the split-half reliability banner, monthly and
    local-hour wet-frequency Altair charts for a focus place, and — when
    `TSTORM_CLIMO_GRIDDED` is set — the wet-hours-per-node map for the
    store's last year, sourced from the Icechunk store rather than the
    per-place Parquet. The app reads the artifact lazily per query via
    `query.artifact_report`; no cell opens a network connection.
    Artifact resolution is by environment variable
    (`TSTORM_CLIMO_ARTIFACT`, `TSTORM_CLIMO_GRIDDED`), and
    `scripts/demo_artifact.py` generates a synthetic local pair for
    network-free development. Remaining polish: place-outline overlays,
    comparing several places side by side, and configurable map windows
    (currently store-coverage-minus-a-year).
16. **Use marimo's live Python server runtime.** Custom date ranges are an
    effectively unbounded input space, so a finite Prepared/zero-Python
    export cannot precompute every result. Browser/WASM execution would
    require exposing relevant data and running Python-compatible
    dependencies in each visitor's browser; keep the initial app
    server-side instead and serve it with `marimo run app/rain_app.py` on
    a container-capable host ([marimo deployment guide](https://docs.marimo.io/guides/deploying/)).
    Prepared static exports remain an option for fixed reports or a small,
    explicitly enumerated set of choices.
17. **Keep app runtime read-only and independent of live data APIs.** Give
    the marimo server read-only access to prepared artifacts; keep any S3
    credentials on the server, never in the browser view. Reactive controls
    can recompute summaries but must not trigger HRRR downloads or Overpass
    queries. Start with one app process and measure memory/latency before
    adding APIs, job queues, or shared caching.
18. **Pin the app environment.** Add marimo and marimo-studio as app-only
    dependencies rather than requirements for the core CLI. Pin Studio and
    any view-provider versions because the project is new and evolving.
19. **Publish reproducibly while serving the latest catalog.** Display the
    exact catalog and observation-artifact versions resolved by the app,
    with their manifest/provenance. When a new catalog release is published,
    advance `latest` after validation and reload/restart the app cleanly;
    do not mix catalog files within a session. Keep previous releases for
    rollback and reproducibility, and keep OSM and HRRR attribution visible.
    - **Row order is now reproducible.** Grouping does not promise a row
      order, so sorting the summary on the wet fraction alone shuffled
      equally-wet places from one run to the next — the leaderboard
      changed between runs with identical numbers. `place_summary` and
      `scorecard` now break ties on `place_key`, so the same artifact
      always prints in the same order. Verified against the full
      2018–2025 North Shore artifact: four consecutive runs give
      byte-identical tables.

### Measured: what serving costs

Measured on the full North Shore artifact (110 places, 2018-01-01 to
2025-12-31, 7,714,080 observation rows, 31 MB of Parquet), on the
development machine, one process per scenario.

The artifact is read as a **lazy scan**, so the filters and the
projection are pushed into the Parquet reader and the rows that cannot
match are never read:

| Scenario | Report time | Peak RSS |
|---|---|---|
| Load the artifact, no query yet | 0.01 s | 0.15 GB |
| All 110 places, 8 years | 2.6 s | 4.8 GB |
| 50 golf courses, 8 years | 1.1 s | 2.4 GB |
| 50 golf courses, one June | 0.3 s | 0.35 GB |
| 50 golf courses, one day | 0.2 s | 0.30 GB |

Three things follow, and they shape the hosting plan:

- **The server does not hold the artifact.** Loading it costs 0.15 GB
  and reads nothing; each query reads only what it needs. An idle
  server between requests sits at the interpreter's footprint rather
  than at 1.3 GB. Before the lazy scan, the artifact was resident from
  the moment it was opened.
- **Memory is the binding constraint, not latency.** The largest query
  — everything, eight years — still peaks around 4.8 GB, because that
  selection genuinely is most of the data. A typical query is far
  cheaper: a June view of the golf courses peaks at 0.35 GB. A
  container has to be sized for the largest query the app allows, or
  the app has to bound it.
- **Preparing the frame once matters.** Joining the place metadata,
  converting to local time, and marking the wet hours were each a full
  pass over the frame, and a report wants all three. Doing them once
  (`summarize.prepare_observations`) cut the eight-year report from
  4.3 s to 2.6 s and the peak from 5.6 GB to 5.2 GB. The numbers are
  unchanged; only the work done to reach them.
- **Slimming and sharding the observations (landed in schema v3).**
  The schema-v2 observations repeated eight per-place-constant columns
  (`y_index`, `x_index`, `grid_lat`, `grid_lon`, `snap_distance_km`,
  `sampling`, `n_cells`, `sampling_key`) on every hourly row. Schema v3
  drops `grid_lat`/`grid_lon`, moves the static per-place columns into
  `places.parquet` (joined back at query time), clusters and shards
  `observations/` by `place_key`, and bounds interactive queries to
  `MAX_SELECTED_PLACES = 20`.

One alternative was measured and is **not** worth doing, so it is
recorded here rather than repeated:

- **Aggregating inside each timezone partition** to avoid concatenating
  the converted frame was measured at 1,062 MB against 851 MB for the
  straightforward version: `partition_by` copies the frame either way,
  so the saving does not exist.

### Why a lazy scan rather than a query engine

The alternatives were measured rather than assumed:

- **DuckDB reading the object in place** is the standard "query the
  lake" pattern (as Athena, BigQuery, and Trino do), and DuckDB is
  already a dependency. But converting its result back to Polars
  requires `pyarrow`, which is not installed — and moving the
  aggregation into SQL would mean a second implementation of the
  analysis semantics, which is exactly what the CLI, the tests, and
  the app are meant to share.
- **Polars' lazy scan** does the same thing with no new dependency and
  no rewrite: the filters go into the Parquet reader, and the analysis
  layer is unchanged.

The lazy scan is the better fit here, and it is also what lets the
artifact sit on S3: `pl.scan_parquet("s3://...")` reads the object
directly, with the same pushdown, and no local copy is needed.

### Phase 5: Generalizations

20. **More attraction kinds and regions.** Integrate and QA the existing
    golf-course and beach catalogs in the app; add ski-area catalogs and
    check OSM tag coverage. Preserve geometry/source identity when possible.
    Treat representative-point sampling as an explicit initial method;
    large or elongated attractions may need footprint summaries. Continue
    to support bbox-driven place catalogs and show region coverage.
21. **Use retained geometry when justified.** Geometry now flows from the
    catalog through `Place.geometry_wkb` into footprint sampling (item 7),
    so the remaining work is presentational and evaluative: draw attraction
    outlines, and assess footprint summaries for large beaches and ski
    areas. The representative point is still the right thing for simple
    lookup and for the fallback when a polygon covers no node centre.
    Footprint aggregation should become the default only once its benefit
    is measured against HRRR's 3 km resolution.
22. **More weather variables and sources.** Add a variable only after its
    source field, units, temporal meaning, and summary interpretation are
    validated. Keep the user-facing variable registry separate from OSM
    attraction kinds. Add other datasets only when a concrete coverage or
    resolution need justifies them.

### Phase 6: Web application (the product)

**Decision (2026-11-02).** The user-facing product is a proper web app,
built as the first of several apps on the reusable `tstorm_climo` package.
Stack: **FastAPI** (backend, pydantic-native) + **Next.js / TypeScript**
(frontend) + **MapLibre GL JS + Deck.gl** (map) + a charting library for
the time series. The marimo prototype (Phase 4) is retired.

#### Why this stack

- The analysis is already decoupled: `query.py`, `gridded.py`, and
  `summarize.py` import neither marimo nor any web framework, so the web
  app is a thin layer over `tstorm_climo` and the two never diverge.
- FastAPI is pydantic-native, matching the repo's modelling style, and
  serves JSON/GeoJSON with negligible ceremony.
- Next.js + TypeScript give the richest geospatial ecosystem for the map
  (MapLibre GL JS, Deck.gl's React bindings, and Kepler.gl for fast
  prototyping) and a reusable pattern for future apps.

#### Layout (first app of many)

```text
src/tstorm_climo/         # shared analysis library (no web/UI deps)
apps/web/backend/         # FastAPI — the API layer over tstorm_climo
apps/web/frontend/        # Next.js + TypeScript — the UI
scripts/demo_artifact.py  # network-free synthetic artifacts for dev/tests
tests/                    # library + API tests
```

Each future app adds a sibling service (or a new `apps/` entry) that
imports `tstorm_climo`; the package never gains UI or web code.

#### API surface (draft)

- `GET  /api/places?kind=…` → the artifact's places (identity, kind,
  geometry), bounded by `MAX_SELECTED_PLACES`.
- `POST /api/report` → `{place_keys, start, end, threshold_mm_per_hour}`
  → `artifact_report(...)`: the summary, monthly, and local-hour tables
  plus the split-half reliability figure.
- `GET  /api/wetness?start&end&threshold` → the wet-hours-per-node map as
  GeoJSON (one feature per node) from `gridded.wet_hours`.

The API is thin: pydantic validates input, date ranges are clamped to the
artifact's coverage (already in `query.py`), and the rest delegates to the
package.

#### Data delivery

- **Time series** are small (≤ `MAX_SELECTED_PLACES` places × a bounded
  range); return them as JSON in the report response.
- **Geometry** (places, footprints, the wet-hours grid) is GeoJSON. The
  North Shore block is ~39×29 nodes, so GeoJSON is enough at this scale;
  move to vector/raster tiles (e.g. MVT via `tippecanoe`/`Martin`) only
  when a region larger than the block is served.
- Keep raw rates vs. conclusions: the gridded store holds rates, and wet
  hours are a query-time reduce (`gridded.wet_hours`), as today.

#### Serving and deployment

- One container first: FastAPI serves the API and the built Next.js
  frontend (Next.js standalone output). Separate API + frontend services
  are a later option, not the first cut.
- Pull the observation artifact from S3 at startup via a `latest.json`
  pointer (add `publish_observations_to_s3` and a reader; the catalog
  release already has this pattern in `catalog_storage.py`).
- Size for the largest allowed query or bound it in the API; the schema-v3
  `MAX_SELECTED_PLACES = 20` cap reduces per-query memory well below the
  uncapped 110-place benchmark above — measure the 20-place peak before
  setting a container limit.
- Read-only at runtime: no HRRR/Overpass/network in the serving path.

#### Build order

1. ~~Retire the marimo app~~ and its `app` dependency group (done).
2. Add `publish_observations_to_s3` + a `latest.json` reader (mirrors the
   catalog publishing path in `catalog_storage.py`).
3. FastAPI backend: pydantic request models and the three endpoints above,
   wired to `query.py`/`gridded.py` unchanged; test against
   `scripts/demo_artifact.py`.
4. Next.js frontend: the leaderboard (with the reliability banner), the
   monthly/local-hour charts, and the MapLibre/Deck.gl wet-hours map with
   place overlays and outlines.
5. Dockerfile (FastAPI + Next.js) with the S3 pull at startup; deploy to a
   container host (Fly/Render/Railway/VPS).

#### Reliability stays first-class

The leaderboard's split-half reliability (Phase 3) is the most important
finding in the project and outranks presentation. The web app shows the
reliability figure next to any ranking and labels coarser/regional
comparisons honestly, exactly as the marimo prototype did.

## Open design questions

- **Co-located places (decided: report, don't hide).** 18 grid nodes hold
  more than one place, so under point sampling those places share
  identical wet-hour counts. For the golf leaderboard this affects **3
  pairs (6 of 50 courses)**.
  1. *(chosen)* Flag them: `place_summary` emits `tied` and
     `cell_places`, and `scorecard` keeps tied places adjacent so the
     shared node is obvious. A precise-looking wrong number is worse than
     "these 3 pairs cannot be separated by this data".
  2. Naive ranking with a footnote — rejected, because the footnote is
     the only signal and it is easy to miss.
  The course-vs-beach overlaps (7 courses) need no treatment beyond a
  note. The flag now keys on the sampled node *set* rather than the anchor
  node, so it stays correct once footprints apply — see item 7. If finer
  separation is ever genuinely needed, the only real fix is a finer
  dataset, not a different sampling of HRRR.
- **Wet threshold — units corrected, conversion centralized.** The archive
  stores `precipitation_surface` in **kg m-2 s-1**, where 1 kg m-2 s-1 =
  1 mm/s = **3600 mm/h**. An earlier draft of this file proposed `0.01`
  kg m-2 s-1 as a "small" threshold; that is **36 mm/h**, a cloudburst,
  and would mark only ~0.005% of cells wet. Do not use it.
  Measured June 2025 over the North Shore block: p90 = 0.005 mm/h,
  p99 = 0.65 mm/h, p99.9 = 4.83 mm/h, max = 31.2 mm/h.
  Candidate thresholds and the fraction of cells they mark wet:
  `0.1 mm/h` → 4.0%; `0.5 mm/h` → 1.3%; `1.0 mm/h` → 0.66%.
  Thresholds are now written in **mm/h** everywhere and the conversion
  lives once in `hrrr.to_mm_per_hour` (see the unit convention above),
  so this class of error should not recur.
  The default for both per-place summaries and the grid-wide sanity
  check is **0.05 mm/h**, shared from `thresholds.py`. Ranking reliability
  remains an empirical property of the selected threshold and is reported
  alongside leaderboards. `> 0` remains the "turf was damp" end of
  the range but is near-useless for ordering places (r = 0.11).
- **Event definition.** Minimum duration/separation for counting
  separate rain events (two wet hours with a dry hour between: two
  events or one?). Picked per application rather than baked in forever.
- **Verification data.** The archives include no ground truth besides
  radar (`composite_reflectivity`, available in the same dataset).
  Consider cross-validating the rate>0 heuristic against dBZ > 10 as an
  independent rain detector, and eventually against METAR/ASOS obs.
- **Building assumption to challenge later:** we are using *model
  analyses*, not observations. Radar-assimilated precipitation is
  decent but can miss coastal events (HRRR's radar coverage extends
  offshore but the golf courses near the coast sit in the model's
  weakest radar region). Keep a note in any published output.

## Remaining-work execution roadmap

The audited, ordered implementation plan for open items and deployment
follow-ups is in [`docs/remaining-work.md`](docs/remaining-work.md). It marks
landed work, required decisions, dependencies, scope, files, acceptance
checks, and items intentionally deferred.

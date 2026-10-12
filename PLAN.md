# PLAN.md

Roadmap and design notes for `tstorm-climo`: a tool for building weather
climatologies from NWP analysis data and serving them through an interactive,
server-side marimo app. Users should be able to choose an attraction (for
example, a golf course, beach, or ski area), a weather variable, and a
historical period.

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
| `tstorm_climo/osm.py` | `KIND_TAGS`, `build_query(bbox, kinds)`, `run_query(query)` | Overpass API via **httpx** (was `requests`). Kinds map to OSM tags (`golf_course` → `leisure=golf_course`, `beach` → `natural=beach`). Body sent with a descriptive User-Agent (Overpass 406-rejects the default client UA). Retries with fixed backoff on both HTTP error statuses **and** transport errors (`httpx.HTTPError`), because a reset or read timeout on this shared service is transient and must not abort a long build. Every attempt carries an explicit `timeout` (default 120 s) — without one, a hung connection blocks the build indefinitely. |
| `tstorm_climo/places.py` | `Place` (pydantic), `classify`, `from_overpass`, `deduplicate`, `haversine_km`, `to_polars`, `save_csv`, `load_csv` | `Place.place_key` is the stable source-qualified OSM identity (`osm:{type}:{id}`). Ways/relations currently use Overpass centers and their full geometry is not retained; nodes use their position. Kind inferred from the element's own tags (Overpass does not say which selector matched). Dedupe collapses same-named places within 1 km (node+way duplicates) while keeping geographically distinct same-named places. |
| `tstorm_climo/climatology.py` | `summarize_grid(precipitation)` | Per hourly analysis step, counts CONUS-wide grid points above a threshold (default 0.05 mm/h). Takes an **already-loaded** dataset — I/O lives in `hrrr.load_precipitation`. Coarse-grained (whole dataset, no spatial subset); a global sanity check, not a per-place result. |
| `tstorm_climo/extract.py` | `extract_series(places, start, end, grid=, opener=)` | Long-format per-place hourly series (`place_key, time, precipitation_mm_per_hour, valid, y_index, x_index, grid_lat, grid_lon, snap_distance_km, tz_name`). `place_key` is independent of input order; summaries join by key. One contiguous block read, then gather in memory. Slices **before** converting units. Sorted by input place order, then time; the time column resets per place and is not globally sorted. |
| `tstorm_climo/artifacts.py` | `build_hourly_observation_artifact(places, start, end, output_dir)` | Writes an immutable output directory containing `observations.parquet`, the exact `places.parquet` catalog used, and `manifest.json`. Current variable is `rain_rate` in mm/h; no threshold is applied. Includes a canonical place-catalog hash and valid/invalid row counts. Existing output directories are not overwritten. |
| `tstorm_climo/summarize.py` | `place_summary`, `by_local_month`, `by_local_hour`, `scorecard`, `split_half_reliability` | Per-place climatology in local time. Default threshold 0.05 mm/h, shared with `summarize_grid`. No unit conversion — input is already mm/h. |
| `tstorm_climo/provenance.py` | `build_metadata`, `write_metadata`, `format_caveats` | JSON sidecar recording archive, date range, threshold, reliability and the CC-BY / ODbL attribution. Makes a published number reproducible and keeps licence obligations in one place. |
| `tstorm_climo/cli.py` | `build-observations --places/--start/--end/--output`, `rain-report --places/--start/--end/--threshold/--kind/--month/--output`, `summarize --start/--end`, `fetch-places --bbox/--kind/--named-only/--no-dedupe/--output` | argparse subcommands. `build-observations` creates the reusable hourly rain artifact. `rain-report` prints the leaderboard, reliability figure, and caveats, and optionally writes a **derived summary** parquet + sidecar. |

Committed data:
- `places_north_shore.csv` — 50 golf courses + 60 named beaches for the
  North Shore bbox (42.35–42.95, −71.20–−70.50), generated by
  `fetch-places --named-only`.

Tooling: `uv` + Python 3.14, `pytest`, `ruff` (E/F/I/UP/B/SIM).
Dependencies: `dynamical-catalog`, `xarray`, `polars`, `pydantic`,
`tzfpy`, `httpx`. Deliberately NOT used: GRIB2/cfgrib, runtime OSM
fetches in the analysis path.

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
   - A 3×3 neighbourhood average was **considered and rejected**: it
     costs 9× the reads and does not separate co-located places, since
     they share the window too. It would only blur the grid, not sharpen
     the answer.
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
   - Per place, extract the hourly precipitation time series at the
     nearest grid point. Single-point only: the 3×3 neighborhood was
     considered and rejected (see the note in step 1 above).
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
     is one hour so mm/h sums to mm), `tied`/`cell_places` (grid-node
     collisions), and a dense `rank` where 1 is driest.
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
7. ~~**Spatial averaging over a course footprint.**~~ **Dropped for the
   current point-sample analysis.** A generic 3×3 window is identical for
   any two points in the same cell,
   so it blurs without separating. A true footprint average (area-weight
   the OSM polygon over covered cells) could separate some co-located
   courses, but needs `pyproj` + polygon-clipping and re-fetching
   geometry — two new dependencies and a schema change to resolve 3
   pairs out of 50 courses. Not worth it. Revisit only if a finer
   dataset becomes available.
8. **Threshold sensitivity study.** Compare wet fractions across
   thresholds (0, 0.1, 0.5, 1 mm/h — see the units note in open
   questions) and against `categorical_rain_surface`, which may be a
   more honest rain-vs-drizzle boundary than any rate cutoff. Keep the
   chosen 0.05 mm/h product default explicit, and show reliability so users
   can see how conclusions change with threshold.

### Phase 3: Offline hourly observation artifacts

9. **Build hourly artifacts for interactive queries.** **DONE for rain v1.**
   `build-observations --places/--start/--end/--output` calls
   `extract_series` and writes a new output directory containing
   `observations.parquet`, `places.parquet`, and `manifest.json`. The
   observation schema includes `place_key, time_utc, variable, value,
   valid` plus grid-sampling details. The current builder supports only
   `rain_rate`; it does not apply a wet threshold. The output directory is
   not overwritten, so each build is an immutable artifact bundle. Add
   additional variables after their source fields and units are validated.
10. **Harden and describe the artifact.** The initial manifest records the
    dataset/source field, normalized units, requested and observed date
    range, place-catalog content hash, build time, row counts, valid fraction,
    and attribution. Keep `place_key` and validity information end-to-end.
    Follow-up: add software/code version and assess completeness against the
    expected hourly timeline, since a missing timestamp entirely is not
    currently counted in `valid_fraction`. Start with one region and one
    Parquet file; partition by variable/year only if filtered reads need it.
    The place-catalog hash identifies the exact attributes used. Geometry is
    not yet collected; when added, it must join the same catalog release.
    - **Place catalog refresh is an offline build step.** Query Overpass
      manually or on a low-frequency schedule, never in response to app
      interactions. For broad coverage, fetch bounded regions/tiles in
      batches rather than one nationwide query; merge and deduplicate by
      `place_key`. Keep unnamed but otherwise valid attractions with a
      generated display label, or record their exclusion as an explicit
      curation rule.
    - **Publish place attributes and geometry as one catalog release.**
      Keep scalar attributes (`place_key`, OSM type/ID, name, kind,
      representative point, timezone) in the places table. Store geometry
      separately, keyed by the same `place_key` (one geometry record per
      place; multipolygons remain one feature). The current importer only
      requests centers; fetching and retaining full way/relation geometry
      is a planned change, not current behavior.
    - The observation builder currently writes the place table and rain
      series together in a new output directory, but does not fetch geometry
      or manage a `latest` pointer. For catalog refreshes, each release should
      have an immutable version directory containing both tables plus a
      manifest (retrieval time, query/region coverage, tag mapping,
      attribution, and checksums). Validate key coverage before publishing.
      Update a small `latest` pointer **only after** the full release is
      ready; never overwrite the two live files independently. Retain older
      releases for rollback and provenance even though users see the latest
      release by default. Local files are sufficient initially; use
      S3/object storage when the hosted app needs shared durable artifacts.
11. **Keep archive I/O out of app interactions.** Build or refresh artifacts
    separately (manual refresh is adequate initially). The app must not
    call HRRR or Overpass when a user changes a selector. The measured
    North Shore block remains small; no database or distributed cache is
    justified. Use Polars lazy scans or load the artifact once, then add
    caching only if measured app latency warrants it.
12. **Adapt to archive format changes.** dynamical.org keeps
    `analysis` (time-optimized), `analysis-virtual` (map-optimized), plus
    forecast and 18 h low-latency variants. Confirm which is best for
    point time series and document the choice in the artifact manifest.
13. **Attribution and licensing.** The catalog is CC BY 4.0 — make sure
     published outputs carry the citation ("NOAA HRRR processed by
     dynamical.org from NOAA Open Data Dissemination archives") plus OSM
     attribution (ODbL) on any published notebook/site.

### Phase 4: Interactive marimo-studio app

14. **Factor a reusable report/query function.** Accept observation data,
    selected `place_key`/attraction kind, variable, date range, and any
    supported summary options; return tables and plot-ready data. Keep it
    independent of marimo and network I/O so the CLI, tests, and app share
    the same analysis behavior.
15. **Build the interactive view with marimo-studio.** Keep analysis cells
    and reusable Python functions in the notebook/package; put presentation
    code in a separate Studio view. Start with the simplest frontend that
    meets the need (plain HTML is sufficient); use React/Svelte only if the
    interaction or design warrants it. Provide controls for attraction kind,
    place, variable, and custom start/end dates bounded by artifact coverage.
    Resolve the latest complete place-catalog release when the app starts
    (or reloads it); keep one consistent catalog snapshot per app session.
    Filter hourly artifacts server-side and call the reusable summary
    functions. Show coverage, units, source, sampling method, reliability
    where applicable, and caveats. Initial views: map, period totals/
    frequency, monthly and local-hour climatology, and place comparisons.
16. **Use Studio's live Python server runtime.** Custom date ranges are an
    effectively unbounded input space, so a finite Prepared/zero-Python
    export cannot precompute every result. Browser/WASM execution would
    require exposing relevant data and running Python-compatible
    dependencies in each visitor's browser; keep the initial app
    server-side instead. Studio is the view/presentation
    tooling, not the hosting service: serve it with `marimo run` on a
    container-capable host ([Studio run/share guide](https://marimo-team.github.io/marimo-studio/guide/run-and-share/),
    [marimo deployment guide](https://docs.marimo.io/guides/deploying/)).
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

### Phase 5: Generalizations

20. **More attraction kinds and regions.** Integrate and QA the existing
    golf-course and beach catalogs in the app; add ski-area catalogs and
    check OSM tag coverage. Preserve geometry/source identity when possible.
    Treat representative-point sampling as an explicit initial method;
    large or elongated attractions may need footprint summaries. Continue
    to support bbox-driven place catalogs and show region coverage.
21. **Use retained geometry when justified.** Once geometry is available,
    draw attraction outlines and assess grid-cell/footprint summaries for
    large beaches and ski areas. Keep the representative point for simple
    lookup and fallback. Evaluate the benefit against HRRR's 3 km resolution
    before making footprint aggregation the default.
22. **More weather variables and sources.** Add a variable only after its
    source field, units, temporal meaning, and summary interpretation are
    validated. Keep the user-facing variable registry separate from OSM
    attraction kinds. Add other datasets only when a concrete coverage or
    resolution need justifies them.

## Open design questions

- **Co-located places (decided: report, don't hide).** 18 grid nodes hold
  more than one place, so those places share identical wet-hour counts.
  For the golf leaderboard this affects **3 pairs (6 of 50 courses)**.
  Options considered:
  1. *(chosen)* Add a `grid_cell` column, rank within cells, and label
     unresolved pairs explicitly — "these 3 pairs cannot be separated by
     this data" beats a precise-looking wrong number.
  2. Naive ranking with a footnote — rejected, because the footnote is
     the only signal and it is easy to miss.
  The course-vs-beach overlaps (7 courses) need no treatment beyond a
  note. If finer separation is ever genuinely needed, the only real fix
  is a finer dataset, not a different sampling of HRRR.
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

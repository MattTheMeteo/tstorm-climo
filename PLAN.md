# PLAN.md

Roadmap and design notes for `tstorm-climo`: a tool for building a
precipitation and thunderstorm climatology from NWP analysis data, with a
long-term goal of hosting a marimo notebook or site showing the
precipitation climatologies of North Shore (MA) golf courses.

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
| `tstorm_climo/osm.py` | `KIND_TAGS`, `build_query(bbox, kinds)`, `run_query(query)` | Overpass API. Kinds map to OSM tags (`golf_course` → `leisure=golf_course`, `beach` → `natural=beach`). Body sent with a descriptive User-Agent (Overpass 406-rejects the default `requests` UA). Retries with backoff. |
| `tstorm_climo/places.py` | `Place` (pydantic), `classify`, `from_overpass`, `deduplicate`, `haversine_km`, `to_polars`, `save_csv`, `load_csv` | `Place.place_key` is the stable source-qualified OSM identity (`osm:{type}:{id}`). Ways/relations use polygon centroids, nodes use position. Kind inferred from the element's own tags (Overpass does not say which selector matched). Dedupe collapses same-named places within 1 km (node+way duplicates) while keeping geographically distinct same-named places. |
| `tstorm_climo/climatology.py` | `summarize_grid(precipitation)` | Per hourly analysis step, counts CONUS-wide grid points above a threshold (default 0.05 mm/h). Takes an **already-loaded** dataset — I/O lives in `hrrr.load_precipitation`. Coarse-grained (whole dataset, no spatial subset); a global sanity check, not a per-place result. |
| `tstorm_climo/extract.py` | `extract_series(places, start, end, grid=, opener=)` | Long-format per-place hourly series (`place_key, time, precipitation_mm_per_hour, valid, y_index, x_index, grid_lat, grid_lon, snap_distance_km, tz_name`). `place_key` is independent of input order; summaries join by key. One contiguous block read, then gather in memory. Slices **before** converting units. Sorted by input place order, then time; the time column resets per place and is not globally sorted. |
| `tstorm_climo/summarize.py` | `place_summary`, `by_local_month`, `by_local_hour`, `scorecard`, `split_half_reliability` | Per-place climatology in local time. Default threshold 0.05 mm/h, shared with `summarize_grid`. No unit conversion — input is already mm/h. |
| `tstorm_climo/provenance.py` | `build_metadata`, `write_metadata`, `format_caveats` | JSON sidecar recording archive, date range, threshold, reliability and the CC-BY / ODbL attribution. Makes a published number reproducible and keeps licence obligations in one place. |
| `tstorm_climo/cli.py` | `rain-report --places/--start/--end/--threshold/--kind/--month/--output`, `summarize --start/--end`, `fetch-places --bbox/--kind/--named-only/--no-dedupe/--output` | argparse subcommands. `rain-report` prints the leaderboard, its reliability figure and caveats, and optionally writes parquet + sidecar. |

Committed data:
- `places_north_shore.csv` — 50 golf courses + 60 named beaches for the
  North Shore bbox (42.35–42.95, −71.20–−70.50), generated by
  `fetch-places --named-only`.

Tooling: `uv` + Python 3.14, `pytest`, `ruff` (E/F/I/UP/B/SIM).
Dependencies: `dynamical-catalog`, `xarray`, `polars`, `pydantic`,
`requests`. Deliberately NOT used: GRIB2/cfgrib, runtime OSM fetches in
the analysis path.

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

   **First results, 50 courses × 2018–2026, 0.5 mm/h (12.9 s end to
   end):**
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

   ### `categorical_rain_surface` compared (item 7, mostly answered)

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

   So the between-course spread is **substantially sampling noise**, and
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
   - This is the producer of the artifact a future notebook or web app
     consumes: compute once, read the parquet, inherit the provenance.
     It is deliberately *not* a caching layer (that stayed dropped) —
     parquet-from-CLI is less machinery and is enough for our needs.

### Phase 2: Event-aware climatology

5. **Rain-event definition.** Merge consecutive wet hours into discrete
   rain events (min duration, min separation), per place. Track event
   start hour-of-day, duration, and total accumulation where possible.
6. ~~**Spatial averaging over a course footprint.**~~ **Dropped.** A
   generic 3×3 window is identical for any two points in the same cell,
   so it blurs without separating. A true footprint average (area-weight
   the OSM polygon over covered cells) could separate some co-located
   courses, but needs `pyproj` + polygon-clipping and re-fetching
   geometry — two new dependencies and a schema change to resolve 3
   pairs out of 50 courses. Not worth it. Revisit only if a finer
   dataset becomes available.
7. **Threshold sensitivity study.** Compare wet fractions across
   thresholds (0, 0.1, 0.5, 1 mm/h — see the units note in open
   questions) and against `categorical_rain_surface`, which may be a
   more honest rain-vs-drizzle boundary than any rate cutoff. Document
   the recommended default.

### Phase 3: Scale and memory strategy

8. ~~**Caching/staging layer.**~~ **Dropped.** Measured against the live
   archive, the premise did not hold: the North Shore block is only
   **26 × 21 nodes**, and the *entire* 2018–2026 window over that block
   is **153 MB in ~12 s**, with no cache priming needed. A full-year read
   is ~19 MB / ~2 s. One contiguous block read, then index in memory,
   covers every use case we have. No staging layer.
9. **Adapt to archive format changes.** dynamical.org keeps
   `analysis` (time-optimized), `analysis-virtual` (map-optimized), plus
   forecast and 18 h low-latency variants. Confirm which is best for
   point time series (likely `analysis`, the time-optimized one) and
   document the choice.
10. **Attribution and licensing.** The catalog is CC BY 4.0 — make sure
    published outputs carry the citation ("NOAA HRRR processed by
    dynamical.org from NOAA Open Data Dissemination archives") plus OSM
    attribution (ODbL) on any published notebook/site.

### Phase 4: Streamlit-like workbook + site

11. **marimo notebook** (preferred per repo notes) with:
    - map of North Shore places colored by wet fraction;
    - per-place hour-of-day histograms ("summer afternoon deluge risk");
    - the ranked driest-to-wettest course table (the Chuck leaderboard);
12. **Publishing.** Host as a static export of the notebook or a small
    web app; pin the input data (versioned parquet) so published numbers
    are reproducible.

### Phase 5: Generalizations

13. **More kinds.** If golf clubs want more granular measures we can add
    or adjust OSM kinds (debatable: `water_park`, `swimming_pool`,
    `pitch`). Keep `KIND_TAGS` as the single source of truth.
14. **Beyond the North Shore.** The pipeline is already bbox-driven;
    keep the CLI able to take any bbox kind list and place CSV so this
    works for other regions/datasets (catalog has more than HRRR).

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

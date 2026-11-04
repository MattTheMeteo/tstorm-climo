# Remaining work roadmap

**Research snapshot: 2026-11-03** · branch `feature/nextjs-app` · PR [#3](https://github.com/MattTheMeteo/tstorm-climo/pull/3)

This is the execution plan for the unfinished work in `PLAN.md`, based on the
current source and tests rather than the older Phase 4 marimo notes or PR
description alone. It is a plan, not an authorization to change the analysis
semantics. In particular, this document does not change anything under
`src/tstorm_climo/`.

I chose a separate document because `PLAN.md` is now a long-lived record of
data measurements and prior decisions. Keeping a detailed implementation
roadmap here makes that record easier to scan while this link keeps the plan
discoverable from `PLAN.md`.

## Audit: what is still open

| PLAN item | Current code status | Remaining work |
| --- | --- | --- |
| Phase 2.5, supported variables | Partial | `artifacts.VARIABLE_VALUE_COLUMNS` maps only `rain_rate`; there is no metadata-rich weather-variable registry. No second variable should be advertised before its source and summary semantics are verified. |
| Phase 2.6, rain events | Open | No event grouping/counts exist. Minimum wet duration and dry-gap/separation rules remain product/science decisions. |
| Phase 2.8, threshold sensitivity | Partial | `PLAN.md` records useful exploratory rate/`categorical_rain_surface` comparisons, but there is no reproducible study command covering the stated threshold set and chosen comparison period. The `0.05 mm/h` default remains explicit in `thresholds.py`. |
| Phase 3.10, artifact hardening | Partial | Schema v3, source/unit/date metadata, validity, and attribution exist. Code version and expected-vs-present hourly timeline/completeness are not recorded. |
| Phase 3.12, archive changes | Partial | `hrrr.py` and artifact manifests identify `noaa-hrrr-analysis` and `precipitation_surface`; the time-series-vs-map archive choice and a live contract check against alternative/current formats remain open. |
| Phase 3.13, attribution | Partial | CLI sidecars and artifact manifests carry NOAA/dynamical.org and OSM attribution. The web page does not display either attribution or the model-analysis caveat. |
| Phase 4 deployment decisions / Phase 6.2, S3 | Open | Catalog S3 publishing exists. Observation-artifact publishing and an observation `latest.json` resolver/pull do not. There is no deployment image. |
| Phase 4 memory limit | Open | The largest documented report is 4.8 GB for 110 places; the actual 20-place, full-range web query and expected concurrency have not been measured. |
| Phase 5.20, kinds/regions | Partial | Golf and beach catalogs exist. The Geofabrik `ski_area` tag union and tag tests also exist, but there is no curation overlay, Overpass ski-area path, multi-region merge/selection workflow, or app QA with a real expanded catalog. |
| Phase 5.21, retained geometry | Mostly done | WKB survives in artifacts, the API returns GeoJSON, and `WetnessMap` draws polygon/multipolygon outlines. The remaining item is to evaluate whether footprint summaries improve results for large beaches/ski areas; do not change the current averaging semantics without evidence. |
| Phase 5.22, more variables/sources | Open/deferred | Only rain rate is supported end-to-end. Snow and additional datasets are not exposed. |
| Phase 6.5, deployment | Open | No root Dockerfile, startup artifact pull, production host configuration, or deployed service exists. The old Phase 4 marimo-specific items are superseded by Phase 6. |
| Phase 6.19, reproducibility while serving | Partial | Stable output order is implemented. The app does not expose the resolved immutable artifact/release identity or trigger a controlled reload when a new release is published. |
| Open design: model caveat | Partial | `provenance.build_metadata` already includes “Model analysis, not observations.” and CLI reports show caveats. The web UI does not. |
| Open design: verification data | Open | `composite_reflectivity` is available in the archive but has not been turned into a reproducible verification study; METAR/ASOS matching is future work. |
| Luna/PR follow-ups: map dates, footprints, CORS | Done | `Dashboard` passes `start`/`end` to `getWetness`; the API slices/clamps dates; `place_record` emits GeoJSON and `WetnessMap` renders outline features; `TSTORM_CLIMO_CORS_ORIGINS` is parsed and defaults to local origins only. The deployment still has to set its actual origin. |
| Luna/PR follow-up: Starlette/httpx warning | Not reproduced | `uv run pytest -q` passed (251 tests) without a Starlette/httpx warning. The current CI logs instead show GitHub Actions Node 20 deprecation notices for the existing `@v4`/`@v5` actions; track that separately below. |
| Undeclared direct test/demo dependency | Open | `scripts/demo_artifact.py` and eight test modules import pandas directly, but `pyproject.toml` does not declare pandas in the dev group. It currently arrives transitively. |

The CI runs on the research-time head (`29678ee`) passed both Python and
frontend jobs; PR #3 had no reviews or review decision at that time. These are
baseline results, not a claim about checks triggered by this roadmap commit.

## Roadmap entries

Every entry states what remains, not a request to begin it now. “S/M/L” means
small/medium/large; “PR” estimates a coherent review unit, not just the number
of commits. `src/tstorm_climo/` changes listed below are future proposals only
and remain outside this planning task.

### 1. Put provenance, attribution, and the model caveat on the web page

- **Why it matters / value:** Published web output needs the NOAA/dynamical.org
  and OpenStreetMap credits, and users must not mistake HRRR analyses for
  ground observations. This closes the highest-value publication gap without
  changing any analysis results.
- **Dependencies:** None. Observation manifests already contain attribution;
  `src/tstorm_climo/provenance.py::ARCHIVE_ATTRIBUTION`,
  `PLACES_ATTRIBUTION`, and the “Model analysis, not observations” caveat are
  already established.
- **Scope:** S; one small PR, suitable as the **first next PR**.
- **Files/functions:** `apps/web/backend/api.py::coverage` and
  `apps/web/backend/schemas.py::CoverageResponse` should expose the available
  attribution/provenance from the loaded manifest; update
  `apps/web/frontend/lib/schemas.ts::CoverageSchema` and `lib/types.ts`, then
  render a visible source/caveat block in `components/Dashboard.tsx` or
  `app/layout.tsx`. Add API assertions in `tests/test_api.py` and frontend
  assertions in `apps/web/frontend/__tests__/web.test.tsx`. Prefer the
  manifest's citation strings over duplicated literals; decide a clear
  fallback if an older/hand-built manifest lacks attribution.
- **Acceptance:** API returns the archive and places attribution; rendered UI
  shows both citations and states that these are model analyses, not
  observations. Run `uv run pytest -q`, `uv run ty check`,
  `cd apps/web/frontend && npm run typecheck && npm test && npm run build`.
- **Human decision/risk:** Confirm the exact displayed credit wording and
  whether ODbL attribution should be adjacent to every map or a persistent
  page footer. Do not imply that the model-analysis caveat is ground-truth
  validation.

### 2. Verify and document the archive contract/variant

- **Why it matters / value:** A changed dynamical.org layout, variable name,
  time axis, or coordinate convention can otherwise make a build fail late or
  silently change its data. The current manifest names the dataset and source
  field, but does not answer whether `analysis` or `analysis-virtual` is the
  correct point-series source.
- **Dependencies:** None for the investigation. Complete this before adding a
  variable or changing artifact-source metadata.
- **Scope:** M; a short research/contract PR with a small live smoke check.
- **Files/functions:** Inspect `src/tstorm_climo/hrrr.py::open_analysis`,
  `HRRR_ANALYSIS_DATASET_ID`, `PRECIPITATION_VARIABLE`, and
  `load_precipitation`; verify dimensions/time cadence/units for
  `precipitation_surface`, `categorical_rain_surface`, and 2-D
  `latitude`/`longitude`. Add a focused command such as
  `scripts/check_archive_contract.py` using `open_analysis(dataset_id=...)`;
  document the selected source/field contract in `PLAN.md` and the manifest
  fields built by `artifacts.py::build_hourly_observation_artifact`.
- **Acceptance:** A live, manually invoked check reports the selected
  dataset's required fields, coordinate shapes, units, and hourly time
  cadence; synthetic tests prove clear errors for missing/renamed required
  fields. The normal offline suite remains network-free:
  `uv run pytest -q`, `uv run ruff check`, `uv run ty check`.
- **Human decision/risk:** Use catalog-discovered identifiers for the
  alternatives; do not guess dataset IDs. Select the analysis variant based
  on archive documentation and a point-query benchmark, not the map-optimized
  name alone. Do not add a silent fallback to another product if its
  semantics differ.

### 3. Make the threshold-sensitivity finding reproducible

- **Why it matters / value:** Existing notes already show that categorical
  rain and rate thresholds produce materially different wet fractions and
  ranking reliability. Re-running that comparison reproducibly is needed
  before presenting a threshold as a meaningful rain-event boundary.
- **Dependencies:** Entry 2 for a confirmed archive contract. Do not change
  the current default while collecting evidence.
- **Scope:** M; one research-focused PR (script plus concise recorded output).
- **Files/functions:** Add `scripts/threshold_sensitivity.py` to use the
  existing `hrrr.open_analysis`, `Grid.from_dataset`,
  `extract.sampling_cells`/`extract_series`, and public summary/reliability
  functions where their input semantics fit. Compare the documented rate
  thresholds `0`, `0.1`, `0.5`, `1.0` mm/h plus the current explicit
  `0.05` default, and report wet fraction and split-half reliability on one
  explicitly named date window. Include `categorical_rain_surface` only after
  its field metadata is rechecked; label it as a separate categorical
  detector, not a rate or accumulation. Record method, date window, and
  results in `docs/` or the research section of `PLAN.md` without committing
  bulk observations.
- **Acceptance:** `uv run python scripts/threshold_sensitivity.py --help`
  works offline; a live run emits a deterministic table with field, units,
  threshold, valid coverage, wet fraction, and reliability; synthetic
  fixtures cover the report aggregation. Run `uv run pytest -q`, `uv run
  ruff check`, `uv run ruff format --check`, and `uv run ty check`.
- **Human decision/risk:** Choose a stable comparison window (avoid silently
  mixing a partial current year with completed years), confirm whether the
  `0.05 mm/h` default remains the product default, and decide whether
  `categorical_rain_surface` is useful enough to keep in the study. Historical
  exploratory results in `PLAN.md` remain useful but are not a substitute for
  a reproducible run.

### 4. Define a rain-only variable registry before exposing another variable

- **Why it matters / value:** Attraction kind is “where” and weather variable
  is “what”; a registry prevents a user-facing variable ID from being
  interpreted with the wrong units or summary. The current
  `VARIABLE_VALUE_COLUMNS` map is an implementation lookup for one variable,
  not the full source/unit/missing-data/summary contract requested by Phase 2.
- **Dependencies:** Entry 2's source contract and entry 3's threshold
  findings. Registry v1 should describe rain only; actual snow support is a
  separate, deferred vertical slice.
- **Scope:** M; one cross-layer PR.
- **Files/functions:** Propose a typed `VariableSpec`/registry in a focused
  `src/tstorm_climo/variables.py` (Pydantic model consistent with the repo),
  then have `artifacts.py::VARIABLE_VALUE_COLUMNS` and manifest construction
  refer to its validated `rain_rate` entry. Keep `query.py::_scan` and
  `artifact_report` variable selection explicit. Add `variable` to
  `apps/web/backend/schemas.py::ReportRequest` and API handling only if the
  app is intended to select among registry entries; update frontend API
  types/schemas and controls only when there is more than one validated
  choice. Tests belong in `tests/test_artifacts.py`, `tests/test_query.py`,
  `tests/test_api.py`, and the matching frontend tests.
- **Acceptance:** A registry entry fully declares source dataset/field,
  normalized unit/conversion, time meaning, missing-data rule, threshold
  policy, and summary interpretation. Invalid/unregistered IDs fail loudly;
  rain artifacts and results remain byte/value-equivalent. Run Python checks,
  `uv build`, and frontend `npm run typecheck`, `npm test`, `npm run build` if
  the API/UI changes.
- **Human decision/risk:** Decide whether a metadata-only rain registry is
  worthwhile before a second variable exists. Do not add a snow entry until
  `snowfall_surface` units and time-step meaning are verified; snow depth and
  precipitation rate must not be conflated. No new package is needed for a
  registry.

### 5. Decide whether event metrics are needed; defer event semantics until then

- **Why it matters / value:** Event starts, duration, separation, and
  accumulation can answer a different question from wet-hour climatology,
  but the result depends strongly on the dry-gap and minimum-duration rules.
- **Dependencies:** Threshold policy (entry 3) and variable contract (entry
  4). First record a product decision; do not encode a guessed event rule in
  the observation artifact.
- **Scope:** S for a decision note; M/whole PR for an approved event feature.
- **Files/functions:** Decision note in `PLAN.md` or this roadmap. If approved,
  add pure event grouping (e.g. `src/tstorm_climo/events.py::rain_events`) on
  selected hourly rows; integrate later with `query.py::artifact_report`,
  `apps/web/backend/schemas.py`, and the UI only if the product needs those
  results. Add focused `tests/test_events.py` cases for gaps, minimum wet
  duration, missing hours, and accumulation. Keep event options out of
  `artifacts.py` storage semantics.
- **Acceptance:** The decision names whether events are in scope and, if yes,
  the wet threshold, minimum wet duration, maximum dry separation, timezone
  convention for start hour, missing-hour behavior, and accumulation unit.
  Approved code passes the shared Python checks and has deterministic
  examples for each boundary rule.
- **Human decision/risk:** Specifically answer whether one dry hour between
  wet hours means one event or two. **Recommended defer:** hourly wetness and
  reliability are the shipped product; event counts are premature until a
  user-facing question requires them.

### 6. Complete observation-artifact provenance and time coverage metadata

- **Why it matters / value:** A saved artifact needs to say which code built
  it and whether “valid fraction” excludes timestamps absent from the archive
  entirely. Today it counts rows present in the source time coordinate, so a
  missing timestamp is not in its denominator.
- **Dependencies:** Entry 2 for archive cadence and a human definition of
  expected coverage. Can proceed independently of deployment.
- **Scope:** M; multi-commit artifact metadata/tests PR, likely no summary
  semantic change.
- **Files/functions:** `src/tstorm_climo/artifacts.py::build_hourly_observation_artifact`
  and `load_observation_artifact`; `src/tstorm_climo/provenance.py` or package
  metadata lookup for installed code version; `tests/test_artifacts.py` for
  expected/present/valid time counts and old manifests. Preserve schema v3
  reader behavior where possible; bump `ARTIFACT_SCHEMA_VERSION` only if
  fields or layout become required to read correctly.
- **Acceptance:** Manifest records a reproducible software version and
  separately names requested window, archive-present steps, valid steps, and
  the completeness denominator. A deliberately missing timestamp is
  accounted for as missing, never as a dry hour. Existing v3 fixtures and
  `uv run pytest -q` pass; `uv run ty check` and `uv run ruff check` pass.
- **Human decision/risk:** Define whether “expected” means every UTC hour in
  the requested range clipped to the archive's actual time coverage or some
  other source-reported interval. Do not count future/unavailable requested
  hours as archive failures. Do not fill missing timestamps with zero rain.

### 7. Publish immutable observation releases to S3 and resolve `latest.json`

- **Why it matters / value:** The web service needs a versioned, auditable
  artifact without baking user data into the image or building from HRRR at
  request time. Catalog publishing already demonstrates immutable objects
  plus a pointer written last.
- **Dependencies:** None for code structure; production use depends on a
  bucket, prefix, and server-side AWS credentials. Entry 6's provenance
  fields should be settled before relying on them for release identity.
- **Scope:** M; a whole backend/storage PR, followed by a separate deployment
  PR.
- **Files/functions:** Add `src/tstorm_climo/artifact_storage.py::publish_observations_to_s3`
  and a resolver/downloader for a pointer URI. Upload every required
  observation artifact object (all shards, `places.parquet`, manifest) to a
  unique release key; publish `latest.json` only after the complete upload.
  Add a `publish-observations` CLI subcommand in `src/tstorm_climo/cli.py` for
  the manual release path. Extend `apps/web/backend/config.py::Settings`
  with one canonical latest-pointer setting while retaining local
  `TSTORM_CLIMO_ARTIFACT`; resolve/download once in `apps/web/backend/app.py`
  lifespan or a storage helper, then pass the immutable local release to
  `load_observation_artifact`. Mirror `catalog_storage.py`'s lazy `boto3`
  import and existing `[catalog-s3]` extra; tests in a new
  `tests/test_artifact_storage.py` should use a fake S3 client like
  `tests/test_catalog_storage.py`.
- **Acceptance:** Tests prove all release objects upload before the pointer,
  invalid/incomplete artifacts do not advance it, the pointer resolves to an
  immutable release, and missing boto3 names `tstorm-climo[catalog-s3]`.
  A manual bucket smoke test downloads and loads a release with no HRRR or OSM
  calls. Run `uv run pytest -q`, `uv run ty check`, `uv run ruff check`, and
  `uv build`.
- **Human decision/risk:** Choose bucket/prefix, retention/rollback policy,
  public-vs-private reads, IAM permissions, and whether the service stages a
  local copy at startup (recommended for this first deploy) rather than doing
  remote Parquet scans on every request. Never send AWS credentials to the
  frontend.

### 8. Measure the real web query, then build and deploy the container

- **Why it matters / value:** A container limit chosen from the 110-place
  benchmark can be unsafe or wasteful for the app's 20-place cap. Deployment
  also needs a working path for both the observation and map artifacts.
- **Dependencies:** Entry 7 for observation release delivery; decide how the
  local Icechunk gridded store reaches the container (read-only mounted
  volume or its own immutable S3 upload/startup pull). Set production
  `TSTORM_CLIMO_CORS_ORIGINS` to exact origins. A live app build is not
  blocked on adding snow, events, or more regions.
- **Scope:** L; a whole PR plus a real host deployment.
- **Files/functions:** Add a focused benchmark under `scripts/` that loads
  `ObservationArtifact` and invokes `query.artifact_report` for 20 places over
  the maximum allowed date range; record peak RSS and query latency, including
  the intended concurrent-request count. Add root `Dockerfile` and an
  entrypoint; use Python/uv and Node versions compatible with
  `pyproject.toml`/`package.json`, `uv sync --frozen --group web`, and
  `npm ci && npm run build`. Configure Next standalone output in
  `apps/web/frontend/next.config.mjs` and arrange for the external `$PORT` to
  reach the frontend while FastAPI serves the API on an internal port (or
  document and choose a single-process static-export alternative). Add
  startup handling for the S3 observation pointer and the chosen gridded
  artifact delivery. Revisit `apps/web/backend/app.py::create_app`,
  `config.py::Settings`, `frontend/lib/api.ts::API_BASE`, and
  `README.md` deployment instructions. Add a Docker build/smoke job to
  `.github/workflows/ci.yml` only after a host-neutral image is verified.
- **Acceptance:** `docker build -t tstorm-climo-web .` succeeds; a local
  container with demo/fixture artifacts answers `/health`, `/api/coverage`,
  `/api/report`, and `/api/wetness`, serves the frontend, and routes its API
  requests correctly. A deployment smoke test confirms startup resolves one
  immutable release, the configured CORS origin is allowed (and an unrelated
  origin is not), and changing selectors triggers no HRRR/Overpass calls.
  Set the memory limit from measured peak plus concurrency/headroom rather
  than guessing; run the full Python/frontend commands listed below.
- **Human decision/risk:** Pick host (Fly/Render/Railway/ECS/VPS), region,
  memory tier after measurement, allowed frontend origin, secrets/IAM model,
  and gridded-store delivery. `TSTORM_CLIMO_GRIDDED` is local-path-only today;
  without a mounted/downloaded store the map correctly returns unavailable.
  Decide whether one container runs Next standalone plus Uvicorn or FastAPI
  serves a static export; the current code is not wired for either production
  topology. Do not remove the current 20-place cap as a memory workaround
  without a product decision.

### 9. Expand catalog kinds/regions only with coverage QA and curation

- **Why it matters / value:** More regions and attraction kinds make the
  package reusable beyond the North Shore, but an incorrect or incomplete
  place catalog is worse than a smaller honest one.
- **Dependencies:** A North Shore deployment can ship first. For ski areas,
  human curation and a checked-in correction/exclusion list must precede
  claiming catalog coverage; entry 10's footprint evaluation can run on the
  resulting real geometries.
- **Scope:** L; separate catalog/app PRs, not a mechanical tag expansion.
- **Files/functions:** Keep `catalog.py::KIND_MATCHES`' existing ski union and
  `tests/test_catalog.py` token tests. Add a reviewed curation overlay (use a
  simple checked-in format already supported by dependencies, or explicit
  Python data; do not add YAML tooling without justification), wire it into
  `geofabrik.py::build_geofabrik_catalog`, and decide whether
  `osm.py::KIND_TAGS`/`fetch-places` should support `ski_area` at all. Add a
  deterministic multi-catalog merge by `place_key` only when broad regions
  are needed. QA the resulting kinds/coverage in catalog manifests and the
  UI (`apps/web/backend/api.py::places`, `frontend/components/Dashboard.tsx`).
- **Acceptance:** Rebuild a named regional test catalog, compare known
  included/excluded resorts, prove duplicate/facility curation is stable,
  and verify app kind/region controls against a real artifact. Run catalog
  tests and the shared checks; no network call is added to request handling.
- **Human decision/risk:** Which region(s) and curated ski facilities are in
  scope, whether app users select region or consume a pre-merged catalog, and
  whether unnamed places remain. **Recommended defer:** ski-area tagging is
  explicitly curatable, not mechanical; ship golf/beach North Shore first.

### 10. Evaluate footprint summaries for large attraction types

- **Why it matters / value:** Geometry is retained and rendered, but a
  node-centre mean over a large beach or resort may or may not better answer
  “rain at the attraction” on a 3 km grid.
- **Dependencies:** Real, geometry-bearing regional catalog (entry 9). The
  map-outline implementation is already complete and is not a dependency.
- **Scope:** M; one research PR/report, no analysis semantic change unless a
  measured result justifies a separately reviewed proposal.
- **Files/functions:** Compare the current behavior in
  `extract.py::sampling_cells`/`extract_series`, `grid.py::Grid.cover_cells`,
  and `places.py::Place.geometry_wkb` on selected large beaches/resorts;
  add a script under `scripts/` or a concise report under `docs/`. Use a
  catalog built by `geofabrik.py::build_geofabrik_catalog`; the committed
  CSV has point-only places and cannot answer this question. Do not introduce
  polygon clipping or `pyproj` as part of this study.
- **Acceptance:** Report point-vs-footprint node counts, wet fractions,
  missing coverage, and ranking/reliability change for named places, with
  geometry/source/date provenance. Synthetic geometry tests cover only the
  comparison mechanics; no claim of regional benefit from synthetic data.
- **Human decision/risk:** Decide what amount of ranking change constitutes
  benefit and whether a large polygon's mean answers the product question.
  **Recommended defer** until a real beach/ski catalog and a question exist.

### 11. Choose verification data; keep external validation out of v1

- **Why it matters / value:** A second rain detector can reveal missed or
  spurious wet hours; independent station data is needed before calling the
  HRRR result observed truth.
- **Dependencies:** Entry 2 field/temporal metadata and entry 3 threshold
  baseline. This does not block the model-analysis caveat or app release.
- **Scope:** M; research PR first, external station integration is a separate
  L effort.
- **Files/functions:** Start with `scripts/verify_rain_detector.py` using
  `hrrr.open_analysis` and validated `composite_reflectivity`; document
  collocation/time matching, thresholds, coverage, and disagreement. Only if
  needed later, build a distinct METAR/ASOS ingestion module using the
  existing HTTP/data stack and a separately reviewed provenance/licensing
  policy; do not put network observation reads in app requests or alter
  `extract_series` semantics for this study.
- **Acceptance:** A reproducible report states detector definitions, matched
  sample counts, temporal/spatial limitations, and disagreement metrics; a
  smoke test uses synthetic inputs. No ground-truth claim is made from
  `composite_reflectivity` alone.
- **Human decision/risk:** Decide whether same-archive radar reflectivity is
  a useful first cross-check and whether METAR/ASOS is worth its temporal
  matching, data-source, and attribution costs. **Recommended defer** external
  station ingestion until a concrete validation question is approved.

### 12. Make direct test/demo imports explicit and clear CI action warnings

- **Why it matters / value:** Reproducible installs should declare direct
  imports rather than rely on transitive packages. CI currently succeeds but
  its logs report Node 20 deprecation for the workflow actions.
- **Dependencies:** None. These are maintenance tasks and can be done in
  parallel with research/storage work.
- **Scope:** S; one small maintenance PR (can be split if the action upgrade
  needs independent review).
- **Files/functions:** Add pandas to the **dev** dependency group in
  `pyproject.toml` and regenerate `uv.lock`, because `scripts/demo_artifact.py`
  plus eight tests directly import pandas; do not add it to runtime/core
  dependencies. In `.github/workflows/ci.yml`, update
  `actions/checkout@v4`, `actions/setup-python@v5`, `actions/setup-node@v4`,
  and `astral-sh/setup-uv@v5` only to verified action releases that support
  the runner's Node 24 runtime. The Starlette/httpx warning is not part of
  this change: none was emitted by current local pytest or found in the
  inspected current CI logs.
- **Acceptance:** Clean install using `uv sync --frozen --group web`, then
  `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`, and
  `uv run ty check`; frontend workflow remains green. New CI run no longer
  reports the Node 20 action-runtime deprecation. `npm ci`, `npm test`, and
  `npm run build` pass.
- **Human decision/risk:** Confirm suitable current action releases before
  changing pins; do not update actions speculatively. Pandas is justified
  only as a dev dependency because it is directly imported; replacing all
  those uses with another API is an alternative if avoiding that declaration
  is preferred.

### 13. Automate artifact refresh only after a stable first deployment

- **Why it matters / value:** Current catalog builds and artifact refreshes
  are manual, which is adequate initially; a scheduled process becomes
  valuable once releases are hosted and rollback/provenance are reliable.
- **Dependencies:** Entries 6–8: source/completeness metadata, immutable
  publishers, startup resolution, and a human-approved refresh cadence.
- **Scope:** L; a separate scheduled-job PR, not part of the first Docker
  deployment.
- **Files/functions:** Orchestrate the existing `cli.py` build commands and
  `catalog_storage.py::publish_catalog_to_s3`, plus entry 7's observation
  publisher. Add a narrowly scoped scheduled workflow or host job only after
  its credentials, catalog region, archive end date, and validation gates are
  known. Validate both catalog and observation builds before advancing any
  pointer; restart/redeploy the app against one immutable resolved release
  rather than hot-swapping catalog contents mid-session.
- **Acceptance:** A dry run builds both artifacts from the same catalog
  snapshot; failed validation leaves every `latest.json` unchanged; a
  successful release records exact IDs and can be rolled back. Document the
  schedule and manual recovery command.
- **Human decision/risk:** Refresh frequency, Geofabrik refresh policy,
  current-year data window, cloud scheduler, and rollback retention. **Defer**
  scheduling until manual publication and deployment have been exercised.

## Suggested order, parallel work, and explicit deferrals

1. **First next PR: item 1, visible provenance/attribution/caveat.** It is a
   small cross-layer change, reuses existing manifest strings, closes a
   publication obligation and the most important scientific caveat, and
   cannot perturb analysis results.
2. **In parallel:** item 12 maintenance; item 2 archive-contract inspection;
   item 11 verification-data decision; and item 10 footprint study planning
   can proceed independently. Do not start a second weather variable until
   items 2–4 agree on source and meaning.
3. **Sequential research:** finish item 2, then item 3 threshold sensitivity,
   then decide/implement the rain-only registry in item 4. Make an explicit
   go/no-go decision on item 5 event metrics after the threshold policy is
   understood.
4. **Parallel implementation after the contract is clear:** item 6 manifest
   completeness and item 7 S3 publishing can proceed independently; item 12
   can land at any point. Keep observation and catalog releases immutable.
5. **Deployment gate:** item 8 follows item 7 and the memory benchmark. Set
   the production CORS origin and choose how the gridded Icechunk store is
   delivered before claiming the map is deployed.
6. **Later generalization:** item 9 catalog expansion, item 10 footprint
   evaluation, item 11 external verification, and item 13 scheduled refresh
   are not prerequisites to the first North Shore rain app.

**Explicitly skip/defer for the first hosted release:**

- **Multi-variable support / snow:** large vertical slice (archive field,
  units, extraction/artifact, query, API, UI, and interpretation). Build a
  rain-only registry first; do not add snow just to populate a selector.
- **Rain-event counts:** defer until the product needs events and a human
  chooses minimum duration, dry separation, missing-hour handling, and
  accumulation rules.
- **Ski-area app coverage:** tag union already exists for Geofabrik, but the
  data needs human curation; do not promise completeness or treat the union
  as a curated resort catalog.
- **Footprint-analysis changes:** geometry rendering and current
  node-centre sampling are already implemented. Defer changing sampling
  weights or adding clipping/projection dependencies until a real-catalog
  comparison demonstrates value.
- **METAR/ASOS ingestion and scheduled refresh:** valuable research/operations
  only after a concrete validation need and a stable deployment exist.
- **Marimo-specific work/checks:** retired and superseded by the FastAPI +
  Next.js app. Do not restore `marimo check` to CI for this roadmap.

**Common acceptance commands for code PRs** (plus the focused checks listed
above):

```sh
uv run ruff check
uv run ruff format --check
uv run ty check
uv run pytest -q --cov=src/tstorm_climo --cov=apps.web.backend --cov-report=term-missing
uv build

cd apps/web/frontend
npm ci
npm run typecheck
npm test
npm run build
npm audit
```

Do not add dependencies casually. Existing Python and frontend dependencies
are sufficient for the proposed initial registry, S3 path (the existing
`catalog-s3` extra), and app. The pandas dev declaration in item 12 is the one
explicitly justified direct-import exception.

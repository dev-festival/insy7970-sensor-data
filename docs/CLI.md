# CLI Reference

This is the running list of command-line tools for the project. Run commands from the project root with `uv run`.

## Current Commands

### Show CLI Help

```powershell
uv run sensor-data --help
```

Use this when you want to see the available command groups and top-level options.

### Health Check

```powershell
uv run sensor-data health
```

Prints a JSON health/configuration summary. This is the quickest way to confirm the package imports, config loads, and the app is in mock mode.

Example output fields:

```text
status
version
source_mode
data_dir
waites.token_configured
maximo.dsn
```

Optional:

```powershell
uv run sensor-data health --env-file .env.example
```

Use `--env-file` when you want to point the command at a specific env file.

### Start FastAPI Service

```powershell
uv run sensor-data serve --source mock
```

Starts the FastAPI app and static browser shell.

Default URLs:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/dates`
- `http://127.0.0.1:8000/api/waites/raw-runs`
- `http://127.0.0.1:8000/api/snapshots/2025-07-09`
- `http://127.0.0.1:8000/api/trends?start_date=2025-07-09&end_date=2025-07-11`
- `http://127.0.0.1:8000/docs`

Useful options:

```powershell
uv run sensor-data serve --source mock --host 127.0.0.1 --port 8000
uv run sensor-data serve --source mock --reload
```

Use `--port` if `8000` is already busy.

### Fetch Mock Waites Data

```powershell
uv run sensor-data waites fetch --source mock --date 2025-07-09 --facility 679
```

Writes raw mock Waites source evidence and a manifest:

```text
data/raw/waites/date=2025-07-09/
```

Also writes small processed reference tables:

```text
data/processed/waites/reference/asset_tree.csv
data/processed/waites/reference/equipment.csv
data/processed/waites/reference/installation_points.csv
data/processed/waites/reference/metadata.json
```

The command prints a JSON summary with record counts and output paths.

Supported mock trend dates:

```text
2025-07-09
2025-07-10
2025-07-11
```

These dates are deliberately shaped for trend testing:

```text
201300 rising vibration
201301 stable vibration
201303 normalizing vibration and temperature
201307 temperature spike on 2025-07-10
201305 missing readings on 2025-07-10
```

### Fetch Live Waites Canary Data

```powershell
uv run sensor-data waites fetch --source api --date 2026-07-19 --facility 679
```

Uses `WAITES_BASE_URL` and `WAITES_ACCESS_TOKEN` from `.env`. This command is intentionally a narrow raw-data canary:

```text
data/raw/waites/date=2026-07-19/
```

The live manifest records endpoint names, sanitized request params, status codes, elapsed times, record counts, output paths, and error details when an endpoint fails. It must not include the access token.

The Waites client uses Python's `truststore` package so TLS verification can use the operating system trust store. This preserves certificate verification while supporting corporate root CAs such as the Honda gateway certificate chain.

If the canary still fails with `CERTIFICATE_VERIFY_FAILED`, fix the local Windows trust store or provide an approved CA path in a later configuration sprint. Do not commit certificates or disable verification in source code.

### Validate Raw Waites Evidence

```powershell
uv run sensor-data waites validate --source mock --date 2025-07-09
uv run sensor-data waites validate --source api --date 2026-07-19
```

Reads an existing raw Waites run and writes:

```text
data/raw/waites/date=YYYY-MM-DD/validation.json
```

The command prints JSON with:

```text
status
error_count
warning_count
endpoint_record_counts
issues
validation_path
```

Warnings are allowed to proceed. Hard validation errors exit nonzero and should be fixed before snapshot or trend processing.

### Verify Raw Evidence

```powershell
uv run sensor-data raw verify --source waites --date 2026-07-19
```

Checks the raw run manifest, required endpoint artifacts, byte counts, SHA-256 checksums, and gzip readability. The command is read-only and prints JSON with:

```text
status
error_count
warning_count
artifacts
issues
```

Use this before and after compression when you want proof that the raw evidence is intact.

### Compress Raw Evidence

```powershell
uv run sensor-data raw compress --source waites --date 2026-07-19
```

Validates the raw run, writes `.json.gz` endpoint artifacts, removes the replaced plain `.json` endpoint files, and updates `manifest.json` with:

```text
artifact.state
artifact.compression
artifact.byte_count
artifact.sha256
artifact.compressed_byte_count
artifact.compressed_sha256
```

The logical artifact name remains the original `.json` path. Downstream readers can load either `equipment.json` or `equipment.json.gz` through the same code path.

### Prune Raw Evidence

```powershell
uv run sensor-data raw prune --source waites --older-than-days 30
```

Dry-run is the default. It lists raw Waites date directories older than the cutoff and reports whether each candidate can be verified before deletion.

To actually delete verified candidates:

```powershell
uv run sensor-data raw prune --source waites --older-than-days 30 --delete --confirm-delete
```

Prune will not delete a date directory that is missing a manifest or fails verification.

### Load Waites Observations

```powershell
uv run sensor-data store load-waites --source mock --date 2025-07-09
uv run sensor-data store load-waites --source api --date 2026-07-19
```

This is the optional inspection/replay path. Normal `0.6.2` day workflows aggregate
validated raw payloads directly into durable facts and do not require persistent
native-observation staging. When explicitly invoked, this command loads a validated
raw Waites run into SQLite:

```text
data/processed/observations.sqlite
```

The load preserves native timestamps for RMS, temperature, ImpactVue, equipment, installation points, and action items in date-scoped staging tables. It also upserts compact reference tables keyed by source and equipment/sensor ID:

```text
waites_equipment_reference
waites_asset_tree_reference
waites_installation_point_reference
```

Those reference tables are the one-row-per-asset-tree, one-row-per-equipment, and one-row-per-sensor view. The date-scoped `waites_equipment` and `waites_installation_points` rows exist only so a snapshot can replay the exact pull for that date when inspection retention is enabled.

The load records source date, source mode, facility, manifest SHA-256, load time, schema version, endpoint row counts, and daily metric rollup counts.
Action items are also upserted into `waites_events`, keyed by source, provider, and
provider event ID. Repeated daily observations update status and last-seen state
without creating duplicate Events rows.

The command is idempotent by source date. By default, rerunning it replaces the existing rows for that date:

```powershell
uv run sensor-data store load-waites --source mock --date 2025-07-09 --replace
```

Use `--no-replace` when you want the command to fail if the date is already loaded.

### Backfill Durable Waites Events

```powershell
uv run sensor-data store backfill-events --source mock
uv run sensor-data store backfill-events --source api
```

Migrates action items for dates already recorded in the ingestion ledger. The JSON
report separates dates imported from retained raw or SQLite rows, dates confirmed
genuinely empty by their endpoint count, and dates requiring a narrow Waites
action-item re-fetch. The operation is replay-safe. Coverage is persisted so the
web Events provider reports incomplete historical dates as `partial` instead of
returning an unexplained empty result.

### Purge Native Waites Observations

```powershell
uv run sensor-data store purge-native --source mock --date 2025-07-09 --dry-run
uv run sensor-data store purge-native --source api --date 2026-07-19 --confirm-delete
uv run sensor-data store purge-native --source api --start-date 2026-07-13 --end-date 2026-07-15 --confirm-delete
```

Deletes releasable date-scoped SQLite staging rows only after the matching ingestion
ledger and fixed daily fact rows exist and validate. It removes the selected
date/range from `waites_equipment`, `waites_installation_points`, RMS, temperature,
ImpactVue, and derived rollups. It preserves `waites_action_items` as durable event
facts, along with the one-row reference tables, daily snapshot table, ledger,
ingestion state, manifest, and validation report.

### Build Sensor Snapshot

```powershell
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data snapshot build --source api --date 2026-07-19
```

Reads and validates raw Waites evidence for the selected date by default. It
calculates daily rows and atomically commits compact references, events, fixed daily
facts, a snapshot revision, and ingestion state to:

```text
data/processed/observations.sqlite
  sensor_daily_facts
  waites_ingestion_ledger
```

Routine builds do not write snapshot CSV or metadata files. The ledger records
endpoint counts, validation status, manifest hash, snapshot row count, and retention
status.

To build from the SQLite observation store instead:

```powershell
uv run sensor-data snapshot build --source mock --date 2025-07-09 --input sqlite
```

Load the date first with `store load-waites`.

To import an existing historical CSV snapshot as a migration/recovery action:

```powershell
uv run sensor-data snapshot store --source mock --date 2025-07-09
```

To explicitly export one stored date:

```powershell
uv run sensor-data snapshot export --source mock --date 2025-07-09 --destination exports/snapshot.csv
```

The destination is required; an export is never created as a side effect of a
normal sync.

### Build Trends

```powershell
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data trend build --source api --start-date 2026-07-19 --end-date 2026-07-19
```

Queries the fixed SQLite daily facts and returns trend counts/readiness. It does not
materialize CSV or metadata during routine processing; the browser queries the same
store directly.

To build trend outputs from the SQLite daily snapshot store:

```powershell
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11 --input sqlite
```

Missing SQLite daily snapshots in the range are reported as skipped dates. This path does not require raw endpoint JSON or timestamp-native SQLite observation rows.

To explicitly export sensor and equipment trend CSVs plus metadata:

```powershell
uv run sensor-data trend export --source mock --start-date 2025-07-09 --end-date 2025-07-11 --destination exports/trend
```

Routine web/API trend reads are read-only queries over SQLite daily snapshots:

```text
GET /api/trends?source=api&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&metric=rms_vel&dimension=x&stat=mean
```

Supported web filters include `scope`, `asset_tree_id`, `equipment_id`, `installation_point_id`, `sensor_id`, and `customer_asset_id`. Operational reads do not silently fall back to files.

### Migrate or Roll Back Snapshot Authority

```powershell
uv run sensor-data store migrate-snapshots --source api
uv run sensor-data store snapshot-authority --authority legacy
uv run sensor-data store snapshot-authority --authority fixed
```

`migrate-snapshots` streams the retained legacy rows into the fixed schema, verifies
row/null/zero counts and canonical hashes, records an audit, and only then activates
the fixed table. The authority commands provide a metadata-only rollback or
reactivation while the legacy table is retained through sprint `0.6.6`.

### Run Human-Readable Workflows

```powershell
uv run sensor-data workflow mock-day --date 2025-07-09
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --cluster-models
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679
uv run sensor-data workflow api-range --start-date 2026-07-24 --end-date 2026-07-26 --facility 679 --raw-retention release --cluster-models
```

Workflow commands run the normal multi-step paths and print a compact, human-readable summary. They are the comfortable operator surface. The lower-level commands above remain the composable JSON surface.

`mock-day` fetches and validates raw mock Waites evidence, then writes the durable
daily facts directly in one transaction.

`mock-trend` runs the mock day flow across the requested date range and builds trend outputs. It reads processed snapshots by default:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11 --input snapshots
```

It can also build trends directly from SQLite observation loads:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11 --input sqlite
```

`api-day` is the friendly live canary wrapper. It fetches live Waites data,
validates the raw shape, verifies checksums, atomically stores the API-source durable
facts, and applies the retention policy. It requires `WAITES_ACCESS_TOKEN`.

`mock-range` and `api-range` are operating-window workflows. They process each date independently, reuse valid daily snapshots by default, build trend outputs, and can either build legacy cluster-window interpretation artifacts or the registered SQLite model grid:

```powershell
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
uv run sensor-data workflow api-range --start-date 2026-07-24 --end-date 2026-07-26 --facility 679 --raw-retention release --dimension x --k 4
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --cluster-models
uv run sensor-data workflow api-range --start-date 2026-07-24 --end-date 2026-07-26 --facility 679 --raw-retention release --cluster-models
```

Useful range options:

```powershell
--dimensions x,y,z,temperature
--cluster-models
--feature-spaces x_accel,y_vel,z_vel,temperature
--ks 5
--skip-fetch
--skip-cluster
--resume
--force
--max-days 31
--json
```

`--resume` is the default. Existing valid snapshot, feature, cluster, drift, and registered model rows are reused. Use `--force` when you deliberately want to rebuild.

Raw retention modes are available on day and trend workflows:

```powershell
uv run sensor-data workflow mock-day --date 2025-07-09 --raw-retention keep
uv run sensor-data workflow mock-day --date 2025-07-09 --raw-retention release
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679 --raw-retention release
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679 --raw-retention keep --keep-native
```

`keep` preserves raw endpoint files, timestamp-native SQLite rows, and date-scoped
source metadata copies for inspection. `compress` gzips endpoint payloads and keeps
SQLite staging rows. `release` deletes endpoint payloads and purges releasable
date-scoped SQLite staging rows after snapshot and ledger persistence are verified;
compact `waites_action_items` event facts remain queryable. Live `api-day` defaults
to `release`; mock workflows default to `keep`.

In release mode, `waites_installation_point_reference` remains one row per sensor
while `waites_installation_points` is cleared for that source date. Waites action
items also remain available to the web Events pane.

Each workflow supports `--json` when a script wants the combined structured result:

```powershell
uv run sensor-data workflow mock-day --date 2025-07-09 --json
```

### Build Evidence Reports

```powershell
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11
```

Builds an inspectable report for the controlled mock trend range. Run the mock trend workflow first so raw counts, SQLite loads, snapshots, and trend artifacts exist:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11
```

The report writes:

```text
reports/mock-trend/start=2025-07-09_end=2025-07-11/
  report.md
  report.qmd
  report.html
  checks.json
  samples/
  charts/
```

It includes raw endpoint counts, SQLite load counts, snapshot and trend counts, sample CSVs, min/avg/max SVG trend charts, and expected-versus-observed checks for the known mock behaviors.

The command writes a fallback HTML report without external dependencies. If Quarto is installed, it also attempts to render `report.qmd` to HTML. To skip Quarto rendering:

```powershell
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11 --no-render
```

Use `--json` when a script wants the report summary:

```powershell
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11 --json
```

### Preview Clustering Features

```powershell
uv run sensor-data cluster features --source mock --date 2025-07-09
```

Builds feature matrix previews without running clustering. The default writes one matrix per clustering dimension:

```text
data/processed/features/date=2025-07-09_source=mock/
  feature_matrix_x.csv
  feature_summary_x.csv
  feature_matrix_y.csv
  feature_summary_y.csv
  feature_matrix_z.csv
  feature_summary_z.csv
  feature_matrix_temperature.csv
  feature_summary_temperature.csv
  metadata.json
```

The first feature contract is deliberately dimension-specific. X, Y, and Z vibration readings are kept separate so clustering compares like with like. Temperature is also clustered as its own `temperature` dimension because it is operationally important but not axis-specific. Current ImpactVue snapshot columns are excluded from the default feature matrices until their clustering role is explicit.

Build a single dimension when you want to inspect one matrix:

```powershell
uv run sensor-data cluster features --source mock --date 2025-07-09 --dimension temperature
```

`--axis x` remains accepted as a compatibility alias for vibration dimensions.

Use `--json` for the feature readiness summary:

```powershell
uv run sensor-data cluster features --source mock --date 2025-07-09 --json
```

### Run Clustering

```powershell
uv run sensor-data cluster run --source mock --date 2025-07-09 --dimension x --k 4
uv run sensor-data cluster run --source mock --date 2025-07-09 --dimension temperature --k 3
```

Runs deterministic KMeans clustering for one like-for-like dimension. The command consumes the dimension-specific feature matrix contract. If the matching feature matrix does not exist yet, it builds that dimension's feature preview from the daily snapshot first.

Outputs are written under:

```text
data/processed/clusters/date=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
  sensor_clusters.csv
  cluster_summary.csv
  pca_coordinates.csv
  metrics.json
```

`sensor_clusters.csv` contains one row per sensor with the assigned `cluster`, distance to centroid, identifiers, and feature values. `cluster_summary.csv` contains cluster counts, within-cluster error, original feature means, and scaled centroids. `pca_coordinates.csv` gives two plotting coordinates for quick inspection. `metrics.json` records feature columns, scaler means/scales, inertia, silhouette score, Calinski-Harabasz score, cluster counts, and output paths.

Dimension guidance:

- Use `x`, `y`, or `z` for axis-specific vibration comparisons.
- Use `temperature` for non-axis temperature behavior.
- Do not compare cluster labels across different dimensions; compare within the same dimension and source.

Choosing `k`:

- Start small for the current mock/live pilot, usually `--k 2`, `--k 3`, or `--k 4`.
- `k` must be less than or equal to the feature row count.
- Silhouette and Calinski-Harabasz metrics are available only when `2 <= k < row_count`.
- Treat cluster numbers as deterministic labels for a run, not permanent semantic names.

Use `--json` when a script wants the run summary:

```powershell
uv run sensor-data cluster run --source mock --date 2025-07-09 --dimension x --k 4 --json
```

### Compare Cluster Drift

```powershell
uv run sensor-data cluster drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
```

Compares two existing cluster runs for the same source, dimension, and `k`. Run `cluster run` for both dates first.

Outputs are written under:

```text
data/processed/drift/from=YYYY-MM-DD_to=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
  cluster_drift.csv
  centroid_drift.csv
  metrics.json
```

`cluster_drift.csv` compares per-sensor assignments and marks whether the raw cluster label changed. `centroid_drift.csv` compares same-label scaled centroid distances. Raw KMeans labels are deterministic here, but they are not semantic group names, so use aligned drift before treating changed-cluster counts as an operating signal.

### Align Cluster Drift

```powershell
uv run sensor-data cluster align-drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
```

Maps clusters from the first date to nearest compatible centroids on the second date, then recalculates per-sensor drift using the aligned labels.

Additional outputs are written beside the raw drift artifacts:

```text
data/processed/drift/from=YYYY-MM-DD_to=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
  aligned_cluster_drift.csv
  centroid_alignment.csv
  aligned_metrics.json
```

`centroid_alignment.csv` records the cluster mapping, centroid distance, source/target cluster sizes, and a simple mapping confidence. `aligned_cluster_drift.csv` keeps both `raw_label_changed` and `aligned_changed` so label movement and likely behavior movement can be inspected separately.

### Build Cluster Windows

```powershell
uv run sensor-data cluster window --source mock --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
```

Runs or reuses per-date cluster artifacts across a date range and compares adjacent dates with centroid-aligned drift. Outputs are written under:

```text
data/processed/cluster_windows/start=YYYY-MM-DD_end=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
  window_summary.csv
  quality_summary.csv
  aligned_drift_summary.csv
  centroid_alignment.csv
  metrics.json
```

`quality_summary.csv` includes row counts, feature counts, inertia, silhouette, Calinski-Harabasz, warnings, and interpretation text. Small mock runs such as 9 rows with `k=4` are marked as contract tests, not cluster-quality evidence.

### Build Registered Cluster Model Grid

```powershell
uv run sensor-data cluster registry build-grid --source mock --start-date 2025-07-09 --end-date 2025-07-11 --feature-spaces x_accel,y_vel,z_vel,temperature --ks 5
uv run sensor-data cluster registry build-grid --source api --start-date 2026-07-24 --end-date 2026-07-26 --feature-spaces x_accel,y_vel,z_vel,temperature --ks 5
uv run sensor-data cluster registry rebuild-date --source api --date 2026-07-26
```

Build snapshots for the date range first. The registry reads fixed daily facts
directly from SQLite and applies the single active service policy (`k=5`, seed 42)
for each date and supported feature space. It writes model and adjacent-date drift
results transactionally into:

```text
data/processed/observations.sqlite
  cluster_model_runs
  cluster_model_assignments
  cluster_model_centroids
  cluster_drift_runs
  cluster_drift_assignments
  cluster_centroid_alignment
```

Normal registered builds create no feature, model, drift, CSV, or JSON mirrors.
Use the explicit legacy diagnostic commands only when rollback/parity evidence is
needed before `0.6.6`.

Default model grid:

```text
feature_spaces = x_accel,y_vel,z_vel,temperature
k = 5
random_seed = 42
```

Feature-space meanings:

```text
x_accel      X-axis RMS acceleration daily stats
y_vel        Y-axis RMS velocity daily stats
z_vel        Z-axis RMS velocity daily stats
temperature  sensor temperature daily stats
```

Useful options:

```powershell
--feature-spaces x_accel,temperature
--ks 5
--resume
--force
--json
```

`--ks` remains a compatibility option but accepts only the active value. A
different value fails explicitly. `rebuild-date` rebuilds the selected date and
only its previous/current and current/next drift pairs; use it after a snapshot
patch instead of rebuilding the full range. Use `--json` for build, reuse,
insufficient-data, failure, and drift counts.

## Raw Retention Guidance

The default live operating layer is now the daily snapshot CSV plus the SQLite `sensor_daily_snapshots` table and `waites_ingestion_ledger`. Keep live raw JSON only long enough to validate, troubleshoot, and persist the daily facts.

For normal live canaries:

```powershell
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679 --raw-retention release
```

For inspection/replay:

```powershell
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679 --raw-retention keep --keep-native
```

For lower-level manual cleanup, keep deletion scoped and deliberate:

- run `raw verify`;
- build or store the daily snapshot;
- run `store purge-native --dry-run`;
- rerun with `--confirm-delete` only when the ledger and snapshot rows are present.

If older release-mode dates left date-scoped installation/equipment rows behind, clean them with:

```powershell
uv run sensor-data store purge-native --source api --start-date 2026-07-23 --end-date 2026-07-25 --confirm-delete
```

Example multi-day mock workflow:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data cluster features --source mock --date 2025-07-09
uv run sensor-data cluster features --source mock --date 2025-07-10
uv run sensor-data cluster features --source mock --date 2025-07-11
uv run sensor-data cluster run --source mock --date 2025-07-09 --dimension x --k 4
uv run sensor-data cluster run --source mock --date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster align-drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster window --source mock --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
```

Equivalent lower-level JSON command sequence:

```powershell
uv run sensor-data waites fetch --source mock --date 2025-07-09 --facility 679
uv run sensor-data store load-waites --source mock --date 2025-07-09
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data waites fetch --source mock --date 2025-07-10 --facility 679
uv run sensor-data store load-waites --source mock --date 2025-07-10
uv run sensor-data snapshot build --source mock --date 2025-07-10
uv run sensor-data waites fetch --source mock --date 2025-07-11 --facility 679
uv run sensor-data store load-waites --source mock --date 2025-07-11
uv run sensor-data snapshot build --source mock --date 2025-07-11
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
```

## Current Test Command

```powershell
uv run pytest
uv run pytest --cov=insy_sensor_data --cov-report=term-missing
```

Runs the mock-mode test suite. Tests should not need Waites credentials, Maximo access, ODBC drivers, or plant network access.

Optional live Waites canary tests:

```powershell
$env:INSY_RUN_LIVE_TESTS = "1"
$env:INSY_LIVE_WAITES_DATE = "2026-07-19"
uv run pytest -m live
```

Unset `INSY_RUN_LIVE_TESTS` to return to offline-only test behavior.

## Maximo Asset History

Query one bounded, read-only work-order history through the mock fixture or the
server-configured DB2/ODBC DSN:

```powershell
uv run sensor-data maximo asset-history --assetnum LEVF454TS --start-date 2025-07-09 --end-date 2025-07-11 --source mock
```

The result is JSON with normalized `wonum`, `assetnum`, `reportdate`, description,
work type, and status. The required date range protects the live DB2 provider from an
unbounded history lookup.

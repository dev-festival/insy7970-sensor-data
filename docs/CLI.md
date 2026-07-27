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

Loads a validated raw Waites run into SQLite:

```text
data/processed/observations.sqlite
```

The load preserves native timestamps for RMS, temperature, ImpactVue, equipment, installation points, and action items. It records source date, source mode, facility, manifest SHA-256, load time, schema version, endpoint row counts, and daily metric rollup counts.

The command is idempotent by source date. By default, rerunning it replaces the existing rows for that date:

```powershell
uv run sensor-data store load-waites --source mock --date 2025-07-09 --replace
```

Use `--no-replace` when you want the command to fail if the date is already loaded.

### Build Sensor Snapshot

```powershell
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data snapshot build --source api --date 2026-07-19
```

Reads raw Waites evidence for the selected date by default and writes:

```text
data/processed/snapshots/date=2025-07-09/sensor_snapshot.csv
data/processed/snapshots/date=2025-07-09/metadata.json
```

Snapshot builds validate the matching raw run first. The snapshot metadata records the validation status and report path.

To build from the SQLite observation store instead:

```powershell
uv run sensor-data snapshot build --source mock --date 2025-07-09 --input sqlite
```

Load the date first with `store load-waites`.

### Build Trends

```powershell
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data trend build --source api --start-date 2026-07-19 --end-date 2026-07-19
```

Reads processed snapshots and writes:

```text
data/processed/trends/start=2025-07-09_end=2025-07-11/sensor_trends.csv
data/processed/trends/start=2025-07-09_end=2025-07-11/equipment_trends.csv
data/processed/trends/start=2025-07-09_end=2025-07-11/metadata.json
```

Trend builds only consume snapshots whose metadata source matches the requested `--source`. This prevents mock and API snapshots from being mixed silently.

To build trend outputs directly from SQLite observation loads:

```powershell
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11 --input sqlite
```

Missing SQLite loads in the range are reported as skipped dates.

### Run Human-Readable Workflows

```powershell
uv run sensor-data workflow mock-day --date 2025-07-09
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679
```

Workflow commands run the normal multi-step paths and print a compact, human-readable summary. They are the comfortable operator surface. The lower-level commands above remain the composable JSON surface.

`mock-day` fetches raw mock Waites evidence, validates it, loads SQLite observations, and builds a sensor snapshot.

`mock-trend` runs the mock day flow across the requested date range and builds trend outputs. It reads processed snapshots by default:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11 --input snapshots
```

It can also build trends directly from SQLite observation loads:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11 --input sqlite
```

`api-day` is the friendly live canary wrapper. It fetches live Waites data, validates the raw shape, verifies checksums, loads SQLite observations, and builds an API-source snapshot. It requires `WAITES_ACCESS_TOKEN`.

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

## Raw Retention Guidance

Keep live raw JSON long enough to validate, troubleshoot, and reprocess the daily facts. Compress validated raw runs early, especially live API pulls. Treat `data/processed/observations.sqlite`, snapshots, trends, clusters, drift, and Maximo joins as the longer-lived working set.

Deletion should stay deliberate:

- run `raw verify`;
- run `raw prune` as dry-run;
- review candidate dates;
- rerun with `--delete --confirm-delete` only when the processed outputs or SQLite observations are sufficient.

Example multi-day mock workflow:

```powershell
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data cluster features --source mock --date 2025-07-09
uv run sensor-data cluster features --source mock --date 2025-07-10
uv run sensor-data cluster features --source mock --date 2025-07-11
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

## Planned Commands

These are not implemented yet, but they are the intended shape from the sprint plan.

```powershell
uv run sensor-data cluster run --date YYYY-MM-DD --dimension x --k 4 --source mock
uv run sensor-data drift compare --from YYYY-MM-DD --to YYYY-MM-DD --source mock
uv run sensor-data maximo asset-history --assetnum A119450 --source mock
```

As commands are implemented, move them from this planned section into the current section with examples.

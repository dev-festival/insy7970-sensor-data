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

### Build Sensor Snapshot

```powershell
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data snapshot build --source api --date 2026-07-19
```

Reads raw Waites evidence for the selected date and writes:

```text
data/processed/snapshots/date=2025-07-09/sensor_snapshot.csv
data/processed/snapshots/date=2025-07-09/metadata.json
```

Snapshot builds validate the matching raw run first. The snapshot metadata records the validation status and report path.

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

## Raw Retention Guidance

Keep live raw JSON long enough to validate, troubleshoot, and reprocess the daily facts. Compress validated raw runs early, especially live API pulls. Treat `data/processed/`, future SQLite observations, snapshots, trends, clusters, drift, and Maximo joins as the longer-lived working set.

Deletion should stay deliberate:

- run `raw verify`;
- run `raw prune` as dry-run;
- review candidate dates;
- rerun with `--delete --confirm-delete` only when the processed outputs or SQLite observations are sufficient.

Example multi-day mock workflow:

```powershell
uv run sensor-data waites fetch --source mock --date 2025-07-09 --facility 679
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data waites fetch --source mock --date 2025-07-10 --facility 679
uv run sensor-data snapshot build --source mock --date 2025-07-10
uv run sensor-data waites fetch --source mock --date 2025-07-11 --facility 679
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
uv run sensor-data cluster run --date YYYY-MM-DD --k 4 --source mock
uv run sensor-data drift compare --from YYYY-MM-DD --to YYYY-MM-DD --source mock
uv run sensor-data maximo asset-history --assetnum A119450 --source mock
```

As commands are implemented, move them from this planned section into the current section with examples.

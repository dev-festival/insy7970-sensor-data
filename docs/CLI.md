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
- `http://127.0.0.1:8000/api/trends?start_date=2025-07-09&end_date=2025-07-09`
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

### Build Sensor Snapshot

```powershell
uv run sensor-data snapshot build --source mock --date 2025-07-09
```

Reads raw Waites evidence for the selected date and writes:

```text
data/processed/snapshots/date=2025-07-09/sensor_snapshot.csv
data/processed/snapshots/date=2025-07-09/metadata.json
```

### Build Trends

```powershell
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-09
```

Reads processed snapshots and writes:

```text
data/processed/trends/start=2025-07-09_end=2025-07-09/sensor_trends.csv
data/processed/trends/start=2025-07-09_end=2025-07-09/equipment_trends.csv
data/processed/trends/start=2025-07-09_end=2025-07-09/metadata.json
```

## Current Test Command

```powershell
uv run pytest
uv run pytest --cov=insy_sensor_data --cov-report=term-missing
```

Runs the mock-mode test suite. Tests should not need Waites credentials, Maximo access, ODBC drivers, or plant network access.

## Planned Commands

These are not implemented yet, but they are the intended shape from the sprint plan.

```powershell
uv run sensor-data cluster run --date YYYY-MM-DD --k 4 --source mock
uv run sensor-data drift compare --from YYYY-MM-DD --to YYYY-MM-DD --source mock
uv run sensor-data maximo asset-history --assetnum A119450 --source mock
```

As commands are implemented, move them from this planned section into the current section with examples.

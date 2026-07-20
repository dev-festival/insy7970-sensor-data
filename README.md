# INSY Sensor Data

Lightweight vibration monitoring service for Waites sensor data and Maximo maintenance history.

The project is intentionally built around small, composable tools. The CLI is the canonical automation surface, FastAPI is the canonical service surface, and both should use the same core modules.

## Current Capabilities

Sprint `0.2.4` adds a disciplined raw evidence lifecycle on top of live Waites validation and API-source snapshot/trend processing:

- uv-managed Python package
- Typer CLI entry point
- FastAPI app factory and `/health`
- static browser app shell
- `.env.example` configuration contract
- pytest harness
- mock Waites fixtures
- raw evidence capture under `data/raw/waites/`
- processed Waites reference tables under `data/processed/waites/reference/`
- raw-run visibility through FastAPI
- daily sensor snapshots under `data/processed/snapshots/`
- trend-ready outputs under `data/processed/trends/`
- snapshot and trend visibility through FastAPI
- controlled mock trend dates for `2025-07-09` through `2025-07-11`
- opt-in live Waites raw evidence capture with secret-safe manifests
- raw Waites validation reports under `data/raw/waites/date=YYYY-MM-DD/validation.json`
- API-source snapshot and trend builds gated by validation and source metadata
- manifest byte counts and SHA-256 checksums for raw endpoint artifacts
- explicit raw evidence verification, gzip compression, and dry-run-first pruning

Clustering and Maximo integration begin in later sprints.

## Requirements

- Python 3.13
- uv

## Setup

```powershell
uv sync --dev
Copy-Item .env.example .env
```

Edit `.env` for local values. Keep `.env` out of Git.

## CLI

```powershell
uv run sensor-data --help
uv run sensor-data health
uv run sensor-data serve --source mock
uv run sensor-data waites fetch --source mock --date 2025-07-09 --facility 679
uv run sensor-data waites validate --source mock --date 2025-07-09
uv run sensor-data raw verify --source waites --date 2025-07-09
uv run sensor-data raw compress --source waites --date 2025-07-09
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
```

The health command prints JSON so it can be used by scripts.

Live Waites canary fetches are explicit and should use small date ranges:

```powershell
uv run sensor-data waites fetch --source api --date 2026-07-19 --facility 679
uv run sensor-data waites validate --source api --date 2026-07-19
uv run sensor-data raw verify --source waites --date 2026-07-19
uv run sensor-data raw compress --source waites --date 2026-07-19
uv run sensor-data snapshot build --source api --date 2026-07-19
uv run sensor-data trend build --source api --start-date 2026-07-19 --end-date 2026-07-19
```

The live workflow reads `WAITES_BASE_URL` and `WAITES_ACCESS_TOKEN` from `.env`, saves raw responses under `data/raw/waites/`, writes validation reports beside the raw run, records checksums in manifests, and omits secrets from manifests. Keep `.env` and `data/` out of Git.

For a visible mock trend, fetch and build snapshots for each supported mock trend date first:

```powershell
uv run sensor-data waites fetch --source mock --date 2025-07-09 --facility 679
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data waites fetch --source mock --date 2025-07-10 --facility 679
uv run sensor-data snapshot build --source mock --date 2025-07-10
uv run sensor-data waites fetch --source mock --date 2025-07-11 --facility 679
uv run sensor-data snapshot build --source mock --date 2025-07-11
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
```

## FastAPI Service

Start the local service:

```powershell
uv run sensor-data serve --source mock
```

Then open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/dates`
- `http://127.0.0.1:8000/api/waites/raw-runs`
- `http://127.0.0.1:8000/api/snapshots/2025-07-09`
- `http://127.0.0.1:8000/api/trends?start_date=2025-07-09&end_date=2025-07-11`
- `http://127.0.0.1:8000/docs`

## Source API

The first external API is the Waites data API.

- Base URL placeholder: `https://data.api.waites.net/v1_1`
- Documentation link placeholder: add official Waites API docs link here when available.

Example request shape planned for sprint `0.1.0`:

```text
GET /readings/rms?facility[]=679&start_date=YYYY-MM-DDT00:00:00Z&end_date=YYYY-MM-DDT23:59:59Z
```

The response contains timestamped sensor readings keyed by `installation_point_id`, axis, facility, and metric values. Raw responses are saved under `data/raw/`; processed outputs are saved under `data/processed/`.

Mock Waites ingestion writes:

```text
data/raw/waites/date=YYYY-MM-DD/
data/processed/waites/reference/
data/processed/snapshots/date=YYYY-MM-DD/
data/processed/trends/start=YYYY-MM-DD_end=YYYY-MM-DD/
```

Live Waites canary ingestion writes the same raw paths:

```text
data/raw/waites/date=YYYY-MM-DD/
```

Live response shape validation writes `validation.json` beside the raw files. Raw lifecycle commands can verify checksums, gzip endpoint JSON files, and dry-run prune old raw runs. API-source snapshot builds read the same raw directory once validation has no hard errors, including compressed `.json.gz` artifacts, and API-source trend builds only consume snapshots whose metadata source is `api`.

The mock trend dates are intentionally small and controlled:

| Sensor | Mock Behavior |
|---|---|
| `201300` | rising vibration |
| `201301` | stable vibration |
| `201303` | high vibration and temperature normalizing downward |
| `201307` | temperature spike on `2025-07-10` |
| `201305` | missing readings on `2025-07-10` |

## Tests

```powershell
uv run pytest
uv run pytest -m live
```

Tests should run in mock mode without API keys, ODBC drivers, DB2 access, or plant network access. Live tests are skipped unless `INSY_RUN_LIVE_TESTS=1` is set.

## Design Docs

- [CLI Reference](docs/CLI.md)
- [Grand Design](docs/GRAND_DESIGN.md)
- [Mock Data Contract](docs/MOCK_DATA_CONTRACT.md)
- [Sprint Plan](docs/sprints/README.md)

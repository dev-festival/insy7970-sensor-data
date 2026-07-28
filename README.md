# INSY Sensor Data

Lightweight vibration monitoring service for Waites sensor data and Maximo maintenance history.

The project is intentionally built around small, composable tools. The CLI is the canonical automation surface, FastAPI is the canonical service surface, and both should use the same core modules.

## Current Capabilities

Sprint `0.4.1a` adds named asset-tree equipment navigation on top of the read-only API and static web review surface:

- uv-managed Python package
- Typer CLI entry point
- FastAPI app factory and `/health`
- static browser review dashboard
- `.env.example` configuration contract
- pytest harness
- mock Waites fixtures
- raw evidence capture under `data/raw/waites/`
- processed Waites reference tables under `data/processed/waites/reference/`
- raw-run visibility through FastAPI
- daily sensor snapshots under `data/processed/snapshots/`
- trend-ready outputs under `data/processed/trends/`
- snapshot and trend visibility through FastAPI
- artifact discovery, equipment lookup, equipment tree, cluster, drift, and cluster-window visibility through FastAPI
- controlled mock trend dates for `2025-07-09` through `2025-07-11`
- opt-in live Waites raw evidence capture with secret-safe manifests
- raw Waites validation reports under `data/raw/waites/date=YYYY-MM-DD/validation.json`
- API-source snapshot and trend builds gated by validation and source metadata
- manifest byte counts and SHA-256 checksums for raw endpoint artifacts
- explicit raw evidence verification, gzip compression, and dry-run-first pruning
- SQLite native observation store under `data/processed/observations.sqlite`
- idempotent `store load-waites` command for validated Waites raw runs
- daily metric rollups for native RMS, temperature, and ImpactVue observations
- SQLite-backed snapshot and trend builds when explicitly requested
- human-readable `workflow mock-day`, `workflow mock-trend`, and `workflow api-day`
- `--json` workflow output for scripts that want combined structured summaries
- mock trend evidence reports under `reports/mock-trend/`
- deterministic sample CSVs, min/avg/max SVG trend charts, and expected-versus-observed checks
- no-Quarto fallback report generation with optional Quarto HTML rendering
- dimension-specific clustering feature previews under `data/processed/features/`
- `cluster features` command for X, Y, Z, and temperature feature matrices
- feature readiness rows in evidence reports when previews are available
- deterministic dimension-specific KMeans cluster runs under `data/processed/clusters/`
- scaled feature metrics, cluster summaries, and PCA coordinate outputs
- cluster drift comparison artifacts under `data/processed/drift/`
- `cluster run` and `cluster drift` commands
- centroid-aligned drift artifacts that distinguish label movement from likely behavior movement
- cluster window summaries under `data/processed/cluster_windows/`
- `cluster align-drift` and `cluster window` commands
- `workflow mock-range` and `workflow api-range` for date-window orchestration
- SQLite daily snapshot store mirrored from `sensor_snapshot.csv`
- Waites ingestion ledger with endpoint counts, validation status, checksums, and retention status
- workflow raw-retention modes: `keep`, `compress`, and `release`
- date-scoped staging purge after snapshot and ledger persistence are verified
- compact Waites reference tables with one row per asset tree, equipment, and sensor
- global browser context for source and date range
- named asset-tree, equipment, and sensor navigation with explicit scope state
- view-local controls for metric, dimension, and `k`
- URL-backed browser state for local refresh/share workflows
- Plotly-based snapshot, trend, cluster, and drift views over API responses

Maximo integration begins in later sprints.

## Requirements

- Python 3.13 or newer
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
uv run sensor-data store load-waites --source mock --date 2025-07-09
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data snapshot build --source mock --date 2025-07-09 --input sqlite
uv run sensor-data snapshot store --source mock --date 2025-07-09
uv run sensor-data store purge-native --source mock --date 2025-07-09 --dry-run
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11 --input sqlite
uv run sensor-data workflow mock-day --date 2025-07-09
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data cluster features --source mock --date 2025-07-09
uv run sensor-data cluster run --source mock --date 2025-07-09 --dimension x --k 4
uv run sensor-data cluster drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster align-drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster window --source mock --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
```

The leaf commands print JSON so they can be composed by scripts. The `workflow` commands print human-readable summaries by default and support `--json` when a combined structured result is useful.

Live Waites canary fetches are explicit and should use small date ranges:

```powershell
uv run sensor-data waites fetch --source api --date 2026-07-19 --facility 679
uv run sensor-data waites validate --source api --date 2026-07-19
uv run sensor-data raw verify --source waites --date 2026-07-19
uv run sensor-data store load-waites --source api --date 2026-07-19
uv run sensor-data snapshot build --source api --date 2026-07-19 --input sqlite
uv run sensor-data store purge-native --source api --date 2026-07-19 --confirm-delete
uv run sensor-data trend build --source api --input sqlite --start-date 2026-07-19 --end-date 2026-07-19
uv run sensor-data workflow api-day --date 2026-07-19 --facility 679 --raw-retention release
uv run sensor-data workflow api-range --start-date 2026-07-24 --end-date 2026-07-26 --facility 679 --raw-retention release --dimension x --k 4
```

The live workflow reads `WAITES_BASE_URL` and `WAITES_ACCESS_TOKEN` from `.env`, saves raw responses under `data/raw/waites/`, writes validation reports beside the raw run, stores the daily snapshot in CSV and SQLite, records a compact ingestion ledger, and defaults to releasing bulky raw/native/date-scoped staging rows after snapshot success. Use `--raw-retention keep --keep-native` when you need inspection/replay. Keep `.env` and `data/` out of Git.

For a visible mock trend, fetch and build snapshots for each supported mock trend date first:

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
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11
```

Or run the lower-level JSON commands yourself:

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
uv run sensor-data serve --source mock --host 127.0.0.1 --port 8000
```

Then open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/dates`
- `http://127.0.0.1:8000/api/artifacts`
- `http://127.0.0.1:8000/api/waites/raw-runs`
- `http://127.0.0.1:8000/api/equipment?source=mock`
- `http://127.0.0.1:8000/api/equipment-tree?source=mock`
- `http://127.0.0.1:8000/api/snapshots/2025-07-09?source=mock`
- `http://127.0.0.1:8000/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11`
- `http://127.0.0.1:8000/api/clusters?source=mock&date=2025-07-09&dimension=x&k=4`
- `http://127.0.0.1:8000/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10&dimension=x&k=4`
- `http://127.0.0.1:8000/api/cluster-windows?source=mock&start_date=2025-07-09&end_date=2025-07-11&dimension=x&k=4`
- `http://127.0.0.1:8000/docs`

The web and API are read-only over existing processed artifacts in sprint `0.4.1a`. Use the CLI workflows and cluster commands to create missing snapshots, trends, clusters, drift, or cluster-window artifacts before selecting those parameters in the browser.

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
data/processed/features/date=YYYY-MM-DD_source=SOURCE/
data/processed/clusters/date=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
data/processed/drift/from=YYYY-MM-DD_to=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
data/processed/cluster_windows/start=YYYY-MM-DD_end=YYYY-MM-DD_source=SOURCE_dimension=DIMENSION_k=K/
```

Live Waites canary ingestion writes the same raw paths:

```text
data/raw/waites/date=YYYY-MM-DD/
```

Live response shape validation writes `validation.json` beside the raw files. Raw lifecycle commands can verify checksums, gzip endpoint JSON files, and dry-run prune old raw runs. `store load-waites` loads validated raw runs into SQLite date-scoped staging tables while preserving source timestamps, and also upserts compact `waites_asset_tree_reference`, `waites_equipment_reference`, and `waites_installation_point_reference` tables. Snapshot builds write both `sensor_snapshot.csv` and `sensor_daily_snapshots` in `observations.sqlite`; `waites_ingestion_ledger` keeps endpoint counts, checksums, validation status, snapshot row count, and retention status. API-source trend builds only consume snapshots whose metadata source is `api`; `--input sqlite` reads the daily snapshot store, not timestamp-native observations.

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

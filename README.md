# INSY Sensor Data

Lightweight vibration monitoring service for Waites sensor data and Maximo maintenance history.

The project is intentionally built around small, composable tools. FastAPI and the
static browser are the canonical product surface. The CLI is a secondary maintenance
surface for data updates, patches, and automation, and both use the same core
modules.

## Current Capabilities

Sprint `0.6.4` reshapes the primary web app around Review, Fleet Trends, Cluster,
and Drift. Browser startup now uses compact service context, scope membership is
server-owned, Cluster projection rows load only on the standalone Cluster surface,
and Drift composes every active feature space plus explicit gaps in one response:

- uv-managed Python package
- Typer CLI entry point
- FastAPI app factory and `/health`
- static browser review dashboard
- compact, projected Trend and Snapshot Review responses with server-produced chart series
- independently bounded trend detail rows through `limit` and `offset`
- per-date Snapshot and registered-model readiness
- Waites action items retained through release-mode ingestion
- cross-day Waites event deduplication with provider status and first/last-seen state
- persisted event-date coverage, including explicit narrow re-fetch requirements
- SQLite-backed equipment hierarchy, Snapshot, Trend, Events, and registered-model reads
- typed missing, migration-required, unavailable, and corrupt-store API failures
- chronological Snapshot trends with continuous dashed gap connections, point-selected Snapshot dates, coverage badges, and per-sensor diagnostics
- `.env.example` configuration contract
- pytest harness
- mock Waites fixtures
- raw evidence capture under `data/raw/waites/`
- compact Waites reference tables in SQLite
- raw-run visibility through FastAPI
- fixed typed daily sensor facts in SQLite
- explicit snapshot and trend exports to operator-selected destinations
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
- registered cluster model grids under `data/processed/cluster_models/`
- registered centroid-aligned drift artifacts under `data/processed/cluster_model_drift/`
- SQLite cluster model runs, assignments, centroids, drift runs, drift assignments, and centroid alignment tables
- `cluster registry build-grid` for prebuilding feature-space models and adjacent-date drift
- `cluster registry rebuild-date` for rebuilding one date and only its touching drift pairs
- one versioned active model policy (`k=5`) with metric/dimension mapping owned by the service
- explicit ready, stale, missing, insufficient-data, and failed model states
- partial Drift windows with complete-pair and missing-pair coverage
- `workflow mock-range --cluster-models` and `workflow api-range --cluster-models`
- `workflow mock-range` and `workflow api-range` for date-window orchestration
- versioned `sensor_daily_facts` without duplicate row JSON or runtime schema changes
- on-demand `/api/trends` reads over the active SQLite snapshot authority
- reversible, audited side-by-side snapshot migration
- Waites ingestion ledger with endpoint counts, validation status, checksums, and retention status
- workflow raw-retention modes: `keep`, `compress`, and `release`
- date-scoped staging purge after snapshot and ledger persistence are verified
- compact Waites reference tables with one row per asset tree, equipment, and sensor
- compact browser context for configured source, date range, readiness, and revision
- named asset-tree, equipment, and sensor navigation with explicit scope state
- selected-scope Snapshot review with context, trends, cluster context, events, and measurements
- Snapshot review panes with independent scroll, pinned metadata, side-by-side charts, and collapsible detail tables
- view-local controls for metric and dimension; source, feature mapping, and model `k` are service-owned
- reduced URL-backed browser state with canonical scope identity
- first-party SVG snapshot, trend, cluster, and drift charts over API responses
- Maximo fixture-backed work-order history and an ODBC boundary for live DB2 access
- Asset Tree-scoped Maximo Events using Waites `customer_asset_id` to Maximo `assetnum`
- Maximo event-provider status that preserves Waites events if DB2 is unavailable
- diagnostic Maximo asset-history CLI and API lookup


## Requirements

- Python 3.13 or newer
- uv
- For live Maximo lookups: an installed DB2 ODBC driver and a server-managed DSN

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
uv run sensor-data maximo asset-history --source mock --assetnum LEVF454TS --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data serve --source mock
uv run sensor-data waites fetch --source mock --date 2025-07-09 --facility 679
uv run sensor-data waites validate --source mock --date 2025-07-09
uv run sensor-data raw verify --source waites --date 2025-07-09
uv run sensor-data raw compress --source waites --date 2025-07-09
uv run sensor-data store load-waites --source mock --date 2025-07-09
uv run sensor-data store backfill-events --source mock
uv run sensor-data store migrate-snapshots --source mock
uv run sensor-data snapshot build --source mock --date 2025-07-09
uv run sensor-data snapshot build --source mock --date 2025-07-09 --input sqlite
uv run sensor-data snapshot export --source mock --date 2025-07-09 --destination exports/snapshot.csv
uv run sensor-data store purge-native --source mock --date 2025-07-09 --dry-run
uv run sensor-data trend build --source mock --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data trend export --source mock --start-date 2025-07-09 --end-date 2025-07-11 --destination exports/trend
uv run sensor-data workflow mock-day --date 2025-07-09
uv run sensor-data workflow mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data report mock-trend --start-date 2025-07-09 --end-date 2025-07-11
uv run sensor-data cluster features --source mock --date 2025-07-09
uv run sensor-data cluster run --source mock --date 2025-07-09 --dimension x --k 4
uv run sensor-data cluster drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster align-drift --source mock --from-date 2025-07-09 --to-date 2025-07-10 --dimension x --k 4
uv run sensor-data cluster window --source mock --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
uv run sensor-data cluster registry build-grid --source mock --start-date 2025-07-09 --end-date 2025-07-11 --feature-spaces x_accel,y_vel,z_vel,temperature --ks 5
uv run sensor-data cluster registry rebuild-date --source mock --date 2025-07-10
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --dimension x --k 4
uv run sensor-data workflow mock-range --start-date 2025-07-09 --end-date 2025-07-11 --cluster-models
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
uv run sensor-data workflow api-range --start-date 2026-07-24 --end-date 2026-07-26 --facility 679 --raw-retention release --cluster-models
```

The live workflow reads `WAITES_BASE_URL` and `WAITES_ACCESS_TOKEN` from `.env`,
saves temporary raw responses under `data/raw/waites/`, validates them, and commits
references, events, fixed daily facts, revisions, and ingestion state atomically to
SQLite. It defaults to releasing bulky raw/high-frequency data after success. Use
`--raw-retention keep --keep-native` when you need inspection or replay, and use the
explicit export commands when a CSV is actually needed. Keep `.env` and `data/` out
of Git.

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
uv run sensor-data cluster registry build-grid --source mock --start-date 2025-07-09 --end-date 2025-07-11 --feature-spaces x_accel,y_vel,z_vel,temperature --ks 5
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
- `http://127.0.0.1:8000/api/maximo/asset-history?assetnum=LEVF454TS&start_date=2025-07-09&end_date=2025-07-11&source=mock`
- `http://127.0.0.1:8000/api/snapshots/2025-07-09?source=mock`
- `http://127.0.0.1:8000/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11`
- `http://127.0.0.1:8000/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11&scope=asset_tree&asset_tree_id=12440&metric=rms_accel&dimension=x&stat=mean`
- `http://127.0.0.1:8000/api/clusters?source=mock&date=2025-07-09&dimension=x`
- `http://127.0.0.1:8000/api/cluster-models?source=mock&start_date=2025-07-09&end_date=2025-07-11`
- `http://127.0.0.1:8000/api/clusters?source=mock&date=2025-07-09&feature_space=x_accel`
- `http://127.0.0.1:8000/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10&dimension=x`
- `http://127.0.0.1:8000/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10&feature_space=x_accel`
- `http://127.0.0.1:8000/api/cluster-windows?source=mock&start_date=2025-07-09&end_date=2025-07-11&dimension=x`
- `http://127.0.0.1:8000/api/cluster-windows?source=mock&start_date=2025-07-09&end_date=2025-07-11&feature_space=x_accel`
- `http://127.0.0.1:8000/docs`

The web and API are read-only over fixed SQLite daily facts, registered SQLite
cluster models, retained Waites events, and bounded Maximo work-order lookups.
Each service instance is bound to one configured source; start the live data
directory with `uv run sensor-data serve --source api`. A source mismatch or an
incomplete store migration is rejected at startup. Snapshot trend coverage reports finite values
for the selected metric field without imputing missing values; clicking an observed
trend point selects that Snapshot date. Build snapshots first; the Trend tab reads
projected, already-scoped rows from the active SQLite snapshot table and receives
chart series aggregated by the server. `/api/trends` bounds its detail collection with `limit`
(default `500`, maximum `2000`) and `offset`. Snapshot readiness is independent of
registered-model readiness, so a model-missing date can still render Snapshot,
Trend, Events, and Measurements. Maximo is queried only when an Asset Tree,
equipment, or sensor is selected; All Equipment never initiates a Maximo query.
Run `cluster registry build-grid` for a new range or `cluster registry rebuild-date`
after patching one snapshot. The browser never chooses a model `k`; an incompatible
compatibility parameter is rejected rather than silently selecting another model.

## Source API

The first external API is the Waites data API.

- Base URL placeholder: `https://data.api.waites.net/v1_1`
- Documentation link placeholder: add official Waites API docs link here when available.

Example request shape planned for sprint `0.1.0`:

```text
GET /readings/rms?facility[]=679&start_date=YYYY-MM-DDT00:00:00Z&end_date=YYYY-MM-DDT23:59:59Z
```

The response contains timestamped sensor readings keyed by `installation_point_id`, axis, facility, and metric values. Raw responses are saved under `data/raw/`; processed outputs are saved under `data/processed/`.

Routine mock and API ingestion uses:

```text
data/raw/waites/date=YYYY-MM-DD/
data/processed/observations.sqlite
```

Legacy feature, cluster, drift, and report commands may still create explicitly
requested investigation outputs. They are no longer routine coordination state.

Live Waites canary ingestion writes the same raw paths:

```text
data/raw/waites/date=YYYY-MM-DD/
```

Live response shape validation writes `validation.json` beside temporary raw files.
Raw lifecycle commands can verify checksums, gzip endpoint JSON files, and dry-run
prune old raw runs. The normal snapshot workflow aggregates validated payloads
directly and atomically upserts compact references, cross-day `waites_events`, fixed
`sensor_daily_facts`, snapshot revisions, and the ingestion ledger. The optional
`store load-waites` path retains native timestamps for inspection or replay. Normal
web reads use SQLite repositories and never silently fall back to CSV or JSON;
raw-run and legacy artifact readers remain explicit diagnostics and migration
inputs.

## Maximo History

The Snapshot Events pane always shows Waites action items. When an Asset Tree (or an
equipment/sensor within an Asset Tree) is selected, it also queries Maximo work orders
for the distinct, non-empty Waites customer asset numbers represented by that tree.
The query is read-only, bounded by the selected date range, and uses `REPORTDATE`.
The default All Equipment scope never queries Maximo.

Live lookup uses `MAXIMO_DSN`, `MAXIMO_SCHEMA`, `MAXIMO_SITE_ID`,
`MAXIMO_ASSETNUM_MAX_LENGTH`, and `MAXIMO_QUERY_TIMEOUT_SECONDS` from `.env`; the ODBC
DSN should be configured on the server. Asset values containing whitespace or longer
than the configured Maximo asset-number limit are reported as skipped while the
remaining assets are still queried.
`source=mock` uses the committed fixture and never imports an ODBC driver.

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
- [0.6.x Reshaping Phase](docs/sprints/README.md#06x-reshaping-phase)

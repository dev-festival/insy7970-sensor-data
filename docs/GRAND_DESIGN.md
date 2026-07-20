# Grand Design

This document keeps the project centered as implementation moves across sprints, chats, and development sessions. When a design choice is unclear, prefer the option that keeps the system small, observable, file-friendly, and easy to run from the command line.

## Product Aim

Build a lightweight vibration monitoring service that combines Waites sensor readings with Maximo maintenance history. The system should help users review vibration trends by equipment and sensor, cluster sensors by measured behavior, inspect maintenance records for aligned assets, and snapshot cluster drift over time.

The app has two distinct API concerns:

- Waites is an external source API that provides sensor data.
- FastAPI is this project's service API and the host for the static browser app.

The first durable milestone is a fully working mock-data version served by FastAPI. Real Waites API calls and Maximo DB2/ODBC access should plug into the same contracts after the mock path proves the workflow.

## Design Principles

1. Keep the CLI excellent. Every important workflow should be runnable from `uv run sensor-data ...`.
2. Keep FastAPI first-class. The service should start early, stay tested, and serve the static app from the same core contracts used by the CLI.
3. Keep tools small. Commands should do one job, write predictable artifacts, and compose with the next command.
4. Preserve evidence. Raw external responses belong in `data/raw/` before processing changes them.
5. Separate raw facts from derived facts. Processed outputs belong in `data/processed/` with metadata explaining how they were built.
6. Keep business logic out of the browser and out of FastAPI route handlers.
7. Make mock mode first-class. Local development and tests must work without API keys, ODBC drivers, DB2 access, or plant network access.
8. Prefer plain files until they hurt. CSV, JSON, and SQLite are enough for early phases.
9. Treat integrations as boundaries. Waites and Maximo adapters should be thin, testable, and replaceable.

## Unix Mindset

The project should feel like a set of reliable command-line tools, not one large application script.

Commands should:

- Accept explicit inputs such as dates, facility IDs, asset numbers, and source modes.
- Write outputs to stable paths.
- Print concise machine-readable summaries or output paths.
- Avoid hidden global state beyond documented config and data directories.
- Be safe to rerun when practical.
- Fail loudly with useful messages when required inputs are missing.
- Compose through files rather than in-memory handoffs.

Avoid commands that fetch, transform, cluster, serve, and export in one breath. A convenient pipeline command can come later, but only after the smaller commands exist and are tested.

## Command Shape

Use one CLI entry point:

```powershell
uv run sensor-data <domain> <action> [options]
```

Expected command families:

```powershell
uv run sensor-data health

uv run sensor-data waites fetch --source mock --date 2026-07-15 --facility 679
uv run sensor-data waites fetch --source api --date 2026-07-15 --facility 679

uv run sensor-data snapshot build --date 2026-07-15 --source mock
uv run sensor-data trend build --start-date 2026-07-01 --end-date 2026-07-15 --source mock

uv run sensor-data cluster run --date 2026-07-15 --k 4 --source mock
uv run sensor-data drift compare --from 2026-07-14 --to 2026-07-15 --source mock

uv run sensor-data maximo asset-history --assetnum A119450 --source mock

uv run sensor-data serve --source mock
```

The CLI should be the canonical automation surface. FastAPI should be the canonical service surface. Both should expose the same core capabilities, not invent separate behavior.

## FastAPI Service

FastAPI is not a bolt-on dashboard layer. It is the app host and HTTP boundary for browser users, CLI integrations that prefer HTTP, and future internal consumers.

FastAPI should:

- Start in sprint `0.0.0` with app creation, health checks, settings wiring, and test coverage.
- Serve static files for the browser app in local mock mode.
- Expose read-only JSON endpoints over raw manifests, processed snapshots, trends, clusters, drift, and maintenance history as those capabilities land.
- Return stable response shapes that are tested with FastAPI's `TestClient`.
- Delegate all business logic to core modules.
- Read artifacts through storage/query functions rather than constructing file paths in route handlers.
- Treat missing data as a normal state with clear 404 or 422 responses, not unhandled 500s.

Initial endpoint shape:

```text
GET /health
GET /api/dates
GET /api/waites/raw-runs
GET /api/snapshots/{date}
GET /api/trends?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
GET /api/clusters/{date}
GET /api/drift?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
GET /api/assets/{assetnum}/maintenance
```

The static web app should call these endpoints only. It should not read local data files directly.

## Artifact Contract

Artifacts are the backbone of the project. Each stage should read from known paths and write to known paths.

Suggested layout:

```text
data/
  raw/
    waites/
      date=YYYY-MM-DD/
        equipment.json
        installation-points.json
        readings-rms.json
        readings-impact-vue.json
        readings-temperature.json
        action-items.json
        manifest.json
    maximo/
      assetnum=VALUE/
        workorders.json
        manifest.json
  processed/
    waites/
      reference/
        equipment.csv
        installation_points.csv
    snapshots/
      date=YYYY-MM-DD/
        sensor_snapshot.csv
        metadata.json
    trends/
      start=YYYY-MM-DD_end=YYYY-MM-DD/
        sensor_trends.csv
        equipment_trends.csv
        metadata.json
    clusters/
      date=YYYY-MM-DD_k=N/
        sensor_clusters.csv
        cluster_summary.csv
        pca_coordinates.csv
        metrics.json
    drift/
      from=YYYY-MM-DD_to=YYYY-MM-DD/
        cluster_drift.csv
        metrics.json
```

Raw files should be as close to the external response as possible. Processed files should be optimized for downstream commands, tests, API responses, and web views.

## Core Architecture

Business logic belongs in a shared core package. Surfaces call the core; they do not own the rules.

```text
src/
  insy_sensor_data/
    config.py
    storage.py
    cli.py
    api/
      main.py
      routes/
        health.py
        waites.py
        snapshots.py
        trends.py
        clusters.py
        maximo.py
      static/
        index.html
        app.js
        styles.css
    waites/
      client.py
      fetch.py
      fixtures.py
    snapshots/
      build.py
      trends.py
    clustering/
      features.py
      model.py
      drift.py
    maximo/
      db.py
      queries.py
      fixtures.py
    joins.py
```

Keep modules narrow:

- `config.py` reads configuration.
- `storage.py` owns paths and directory creation.
- `api/main.py` creates the FastAPI app, mounts static files, and includes route modules.
- `api/routes/*` validates HTTP inputs and delegates to core functions.
- `waites/client.py` builds requests and performs real HTTP calls.
- `waites/fetch.py` writes raw evidence and manifests.
- `snapshots/build.py` transforms raw readings into daily rows.
- `clustering/model.py` runs feature scaling, PCA, KMeans, and metrics.
- `maximo/db.py` owns ODBC connection behavior.
- `joins.py` aligns Waites `customer_asset_id` to Maximo `assetnum`.

## Data Sources

### Waites

Waites is the first external API. The expected initial endpoints are:

- `equipment`
- `installation-points`
- `readings/rms`
- `readings/impact-vue`
- `readings/temperature`
- `action-items`

The access token must come from `.env`, never source files. The repository should include `.env.example` with placeholders only.

### Maximo

Maximo should be accessed through DB2/ODBC from the server side. Early implementation should use mock fixtures with the same row shape expected from query results. Query files should be parameterized SQL with positional ODBC markers.

The key alignment field is Waites `customer_asset_id` to Maximo `assetnum`.

## Mock Mode

Mock mode is not a demo shortcut. It is the test and development substrate.

Mock mode should:

- Use fixtures committed under `tests/fixtures/` or another explicit fixture directory.
- Exercise the same core code paths as real mode after the integration boundary.
- Produce the same artifact names as real mode.
- Include partial-data cases such as missing axes, missing temperature, missing asset alignment, and inactive sensors.
- Be accepted by API and web workflows.

The detailed fixture and artifact expectations live in [Mock Data Contract](MOCK_DATA_CONTRACT.md).

## Testing Strategy

Use pytest as the default test runner.

Test layers:

- Unit tests for config, storage paths, request construction, aggregation, feature selection, joins, and metric calculations.
- CLI tests with Typer's test runner and `tmp_path`.
- Fixture contract tests for raw mock responses and processed output schemas.
- API tests with FastAPI's test client from sprint `0.0.0` onward.
- Regression tests for known awkward records from the reference data.

Tests should not require network, API keys, DB2, ODBC drivers, or local plant access unless explicitly marked as integration tests.

## Sprint Strategy

The major sprint docs describe stable milestones. It is normal to have in-between implementation steps.

Expected in-between work between `0.1.0` and `0.2.0`:

- `0.1.1`: raw fixture cleanup, naming consistency, and manifest validation.
- `0.1.2`: schema checks for raw Waites endpoint shapes.
- `0.1.3`: processed reference tables for equipment and installation points.
- `0.1.4`: partial-data fixtures for missing axes, missing sensor metadata, and missing asset alignment.
- `0.1.5`: CLI output polish and rerun behavior.

These do not all need separate formal sprint docs unless the work grows. They are useful checkpoints before the snapshot builder starts trusting ingestion outputs.

Expected bridge work between `0.2.0` and `0.3.0`:

- `0.2.1`: multi-day mock trend data with deliberate stable, rising, falling, spiking, and missing-day behaviors.
- `0.2.2`: narrow live Waites canary that saves raw API evidence through the same artifact contract as mock mode.
- `0.2.3`: live raw shape validation and source-aware snapshot/trend processing for small, explicit date ranges.

The rule for this bridge is: mock data owns behavior, live data validates assumptions. Normal tests should remain offline, deterministic, and fixture-backed. Live tests or smoke checks should be opt-in and should never require secrets, plant network access, or large real datasets for the default development workflow.

## Definition of Done

A change is done when:

- The relevant command is runnable through `uv run`.
- Any relevant FastAPI endpoint is covered by `TestClient`.
- New outputs land in documented paths.
- Tests cover the behavior with mock data.
- Secrets remain out of Git.
- README or docs explain the command if a stranger would need it.
- The command can be rerun without surprising destructive behavior.
- Route handlers, web code, and orchestration layers stay thin.

## Non-Goals For Early Sprints

- Full replacement of Waites or Maximo.
- Real-time streaming.
- User authentication and authorization.
- Production deployment automation.
- Complex model management.
- Heavy dashboards before the CLI and artifact contracts are stable.

## Design North Star

The project should remain easy to reason about from the shell:

```text
fetch raw evidence
build processed snapshots
build trends
run clusters
compare drift
join maintenance history
serve results
```

If a future design makes that chain hard to see, simplify it.

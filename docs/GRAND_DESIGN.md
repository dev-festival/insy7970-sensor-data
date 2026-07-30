# Grand Design

This document keeps the project centered as implementation moves across sprints, chats, and development sessions. When a design choice is unclear, prefer the option that keeps the system small, observable, file-friendly, and easy to run from the command line.

> **0.6.x roadmap note:** The [0.6.x Reshaping Phase](sprints/README.md#06x-reshaping-phase)
> makes FastAPI and the browser the primary product surface, SQLite the operational
> data authority, and the CLI a supporting administration surface. Where the
> `0.6.0` through `0.6.6` sprint specifications conflict with the earlier
> CLI-canonical or artifact-backbone guidance below, the reshaping specifications
> govern the migration. This document will be fully reconciled in sprint `0.6.6`
> after compatibility paths have been retired.

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
uv run sensor-data waites validate --source api --date 2026-07-15
uv run sensor-data raw verify --source waites --date 2026-07-15
uv run sensor-data raw compress --source waites --date 2026-07-15
uv run sensor-data raw prune --source waites --older-than-days 30

uv run sensor-data store load-waites --source api --date 2026-07-15
uv run sensor-data store purge-native --source api --date 2026-07-15 --confirm-delete
uv run sensor-data snapshot build --date 2026-07-15 --source mock
uv run sensor-data snapshot store --date 2026-07-15 --source mock
uv run sensor-data trend build --start-date 2026-07-01 --end-date 2026-07-15 --source mock
uv run sensor-data workflow api-day --date 2026-07-15 --facility 679 --raw-retention release

uv run sensor-data cluster features --date 2026-07-15 --dimension x --source mock
uv run sensor-data cluster run --date 2026-07-15 --dimension x --k 4 --source mock
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

Future dashboard drilldowns should treat timestamp-level source readings as on-demand detail. The durable local layer should stay daily snapshots, SQLite-backed trend views, registered cluster models, drift outputs, reports, and ledger records; if a user clicks a suspicious daily point, the service can make a narrow live source request for that exact sensor/date/measurement/dimension and discard the detail unless explicitly retained.

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
        validation.json
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
    observations.sqlite
    trends/
      start=YYYY-MM-DD_end=YYYY-MM-DD/
        sensor_trends.csv
        equipment_trends.csv
        metadata.json
    features/
      date=YYYY-MM-DD_source=VALUE/
        feature_matrix_x.csv
        feature_summary_x.csv
        feature_matrix_y.csv
        feature_summary_y.csv
        feature_matrix_z.csv
        feature_summary_z.csv
        feature_matrix_temperature.csv
        feature_summary_temperature.csv
        metadata.json
    clusters/
      date=YYYY-MM-DD_source=VALUE_dimension=VALUE_k=N/
        sensor_clusters.csv
        cluster_summary.csv
        pca_coordinates.csv
        metrics.json
    drift/
      from=YYYY-MM-DD_to=YYYY-MM-DD_source=VALUE_dimension=VALUE_k=N/
        cluster_drift.csv
        centroid_drift.csv
        aligned_cluster_drift.csv
        centroid_alignment.csv
        aligned_metrics.json
        metrics.json
    cluster_windows/
      start=YYYY-MM-DD_end=YYYY-MM-DD_source=VALUE_dimension=VALUE_k=N/
        window_summary.csv
        quality_summary.csv
        aligned_drift_summary.csv
        centroid_alignment.csv
        metrics.json
```

Raw files should be as close to the external response as possible. Processed files should be optimized for downstream commands, tests, API responses, and web views.

Validation reports are the gate between raw evidence and processed outputs. They should describe source shape, record counts, warnings, and hard failures without transforming the raw files themselves.

Raw endpoint artifacts may be stored as plain `.json` or gzip `.json.gz`; the logical artifact identity remains the original endpoint JSON filename. Manifests should record artifact state, byte counts, SHA-256 checksums, compressed byte counts, and compressed checksums. For live operating workflows, raw endpoint payloads are short-lived proof. Once validation, ingestion ledger records, snapshot CSVs, and SQLite daily snapshot rows exist for a date, the default pipeline may release raw payload files, timestamp-native observation rows, and date-scoped equipment/installation staging rows unless the operator explicitly requests inspection retention. Compact equipment and installation-point reference tables may stay as the one-row-per-ID view.

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
      validate.py
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
- `waites/validate.py` checks raw evidence before downstream processing.
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
- `0.2.4`: raw evidence lifecycle with compression, checksums, verification, and explicit pruning.
- `0.2.5`: SQLite observation store for validated native measurements and query-backed daily facts.
- `0.2.6`: human-readable workflow wrappers over the JSON leaf commands.
- `0.2.7`: evidence reports with samples, min/avg/max charts, and expected-versus-observed checks.
- `0.2.8`: clustering feature matrix contract and readiness checks.
- `0.2.9`: SQLite daily snapshot store, ingestion ledger, and raw/native release policy.

The rule for this bridge is: mock data owns behavior, live data validates assumptions, and human-facing evidence earns trust before more advanced modeling is added. Normal tests should remain offline, deterministic, and fixture-backed. Live tests or smoke checks should be opt-in and should never require secrets, plant network access, or large real datasets for the default development workflow.

Raw evidence is not the long-term working set. Treat live JSON payloads like short-lived proof: preserve them first, checksum them, validate them, summarize them into an ingestion ledger, and then release or pack them according to the workflow retention mode. The long-term working layer should be daily snapshots in CSV and SQLite, compact source reference tables, SQLite trend queries over daily facts, registered cluster models, drift outputs, reports, and maintenance context. Timestamp-native SQLite observations and date-scoped source metadata copies are useful for inspection and replay, but they should not be required for the default clustering path after daily snapshots have been persisted.

Expected post-clustering hardening before `0.4.0`:

- `0.3.0`: deterministic dimension-specific clustering, PCA coordinates, metrics, and first drift artifacts.
- `0.3.1`: operating-window orchestration, cluster quality summaries, and centroid-aligned drift interpretation.
- `0.3.2`: deferred artifact packing and retention for raw and processed outputs after artifact volume justifies loader complexity.
- `0.4.3`: offline cluster model registry and SQLite-backed cluster/drift reads.
- `0.4.4`: on-demand SQLite trend queries over daily snapshots.
- `0.4.x`: on-demand live source drilldown from dashboard points without restoring raw detail as default local storage.

The rule for this phase is: pull narrow, persist immediately, process from SQLite or compact per-date artifacts, and interpret cluster movement before presenting drift as an operational signal. Range commands should never require loading a full operating window of raw endpoint JSON into memory. Packing should wait until processed artifacts are large enough or old enough to justify loader complexity.

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
build dimension-specific feature matrices
run clusters
compare drift
join maintenance history
serve results
```

If a future design makes that chain hard to see, simplify it.

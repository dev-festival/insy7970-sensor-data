# Sprint Plan

This folder tracks the project roadmap as small, testable sprints. Sprint numbers start at `0.0.0` and move from foundation work toward a lightweight service that can ingest Waites sensor data, process mock and real readings, cluster sensors, and overlay Maximo maintenance history.

Read the project-level design first: [Grand Design](../GRAND_DESIGN.md).

## Sequence

| Sprint | Theme | Working Outcome |
|---|---|---|
| [0.0.0](0.0.0-foundation.md) | Project and service foundation | Installable uv package with CLI, FastAPI health, static app shell, and test harness |
| [0.1.0](0.1.0-mock-waites-ingestion.md) | Mock Waites ingestion | Raw mock source API evidence saved under `data/raw/` and visible through service status endpoints |
| [0.2.0](0.2.0-snapshots-and-trends.md) | Mock snapshots, trends, and read API | Processed sensor outputs under `data/processed/` served through FastAPI |
| [0.2.1](0.2.1-multi-day-mock-trends.md) | Multi-day mock trends | Controlled mock date ranges that demonstrate visible trend movement |
| [0.2.2](0.2.2-live-waites-canary.md) | Live Waites canary | Narrow opt-in live API fetch that preserves raw evidence without changing downstream contracts |
| [0.2.3](0.2.3-live-shape-validation.md) | Live shape validation | Live raw response validation and source-aware snapshot/trend processing |
| [0.2.4](0.2.4-raw-evidence-lifecycle.md) | Raw evidence lifecycle | Compressed, verified, and explicitly pruned raw source evidence |
| [0.2.5](0.2.5-sqlite-observation-store.md) | SQLite observation store | Validated native observations loaded into a compact queryable store |
| [0.2.6](0.2.6-human-readable-workflows.md) | Human-readable workflows | Friendly workflow commands over the JSON leaf commands |
| [0.2.7](0.2.7-evidence-report.md) | Evidence report | Report artifacts with samples, min/avg/max charts, and expected-versus-observed checks |
| [0.2.8](0.2.8-clustering-feature-contract.md) | Clustering feature contract | Feature matrix preview and readiness checks before clustering |
| [0.2.9](0.2.9-daily-snapshot-store-and-raw-release.md) | Daily snapshot store and raw release | SQLite daily snapshots, ingestion ledger, and workflow raw-retention modes |
| [0.3.0](0.3.0-clustering.md) | Clustering | Clustered snapshots, metrics, PCA coordinates, and drift-ready artifacts |
| [0.3.1](0.3.1-operating-window-and-cluster-interpretation.md) | Operating window and cluster interpretation | Date-window orchestration, cluster quality summaries, and centroid-aligned drift |
| [0.3.2](0.3.2-artifact-packing-and-retention.md) | Deferred artifact packing and retention | Parking lot for future packing once artifact volume justifies loader complexity |
| [0.4.0](0.4.0-api-and-static-web.md) | API and web hardening | Dashboard polish, richer service responses, and browser workflow hardening |
| [0.4.1](0.4.1-navigation-and-parameter-model.md) | Navigation and parameter model | Global context, equipment/sensor navigation, and view-local controls |
| [0.4.1a](0.4.1a-equipment-tree-and-selection.md) | Equipment tree and selection | Named asset-tree navigation with explicit all, asset tree, equipment, and sensor scope semantics |
| [0.4.1b](0.4.1b-snapshot-review-workspace.md) | Snapshot review workspace | Selected equipment/sensor snapshot page with trends, cluster context, events, and measurements |
| [0.4.2](0.4.2-chart-rendering-and-ui-polish.md) | Chart rendering and UI polish | Stable chart layout, metric controls, and dependency-light rendering decisions |
| [0.4.3](0.4.3-offline-cluster-model-registry.md) | Offline cluster model registry | Prebuilt cluster model grids and drift results persisted to SQLite for app reads |
| [0.4.4](0.4.4-on-demand-sqlite-trends.md) | On-demand SQLite trends | Trend API responses computed from SQLite daily snapshots instead of required artifacts |
| [0.4.x](0.4.x-on-demand-source-drilldown.md) | On-demand source drilldown | Temporary live source detail for selected dashboard points without permanent raw storage |
| [0.5.0](0.5.0-maximo-integration.md) | Maximo integration | Asset maintenance records aligned to Waites equipment by asset number |

## Sprint Style

Each sprint should leave the repo runnable by someone else. Prefer CLI commands, plain files, fixture-backed tests, and narrow modules over large coupled flows.

The named sprints are stable milestones. Smaller `0.1.x` or `0.2.x` implementation checkpoints are expected when contracts, fixtures, or tests need hardening before the next milestone. Live data should enter first as a narrow canary, while mock data remains the default test substrate.

## Archived Specs

| Sprint | Reason |
|---|---|
| [Archived 0.3.1](archive/0.3.1-pipeline-memory-and-windowing.md) | Superseded by the snapshot-release operating path from `0.2.9`; replaced with cluster-window interpretation work |

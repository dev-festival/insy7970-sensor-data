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
| [0.3.0](0.3.0-clustering.md) | Clustering | Clustered snapshots, metrics, PCA coordinates, and drift-ready artifacts |
| [0.4.0](0.4.0-api-and-static-web.md) | API and web hardening | Dashboard polish, richer service responses, and browser workflow hardening |
| [0.5.0](0.5.0-maximo-integration.md) | Maximo integration | Asset maintenance records aligned to Waites equipment by asset number |

## Sprint Style

Each sprint should leave the repo runnable by someone else. Prefer CLI commands, plain files, fixture-backed tests, and narrow modules over large coupled flows.

The named sprints are stable milestones. Smaller `0.1.x` or `0.2.x` implementation checkpoints are expected when contracts, fixtures, or tests need hardening before the next milestone. Live data should enter first as a narrow canary, while mock data remains the default test substrate.

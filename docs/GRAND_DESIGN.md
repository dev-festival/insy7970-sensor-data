# Grand Design

This document is the architectural compass after the `0.6.x` reshape. Historical
sprint specifications preserve how the project arrived here; this document describes
the system new work must extend.

## Product aim

Build a fast, sustainable vibration-monitoring web service that combines Waites
sensor behavior with Maximo maintenance context. Users should be able to review a
date, explore fleet trends, browse behavioral clusters, and understand drift without
knowing how ingestion or model persistence works.

The standalone Cluster surface remains a first-class product direction. It can grow
toward a richer, starmap-like exploration experience, but that work should consume
the same registered model authority rather than adding a parallel pipeline.

## Non-negotiable boundaries

1. FastAPI and the browser are the primary service.
2. SQLite is the one operational authority.
3. One instance is bound to one source and one data directory.
4. The CLI is limited to serve, sync, rebuild, doctor, and export.
5. Raw provider evidence is lifecycle-managed input, not application state.
6. Explicit exports are outputs, never coordination inputs.
7. The browser does not choose source, feature space, or model `k`.
8. Mock mode stays offline, deterministic, and contract-equivalent.
9. Maximo remains a bounded, read-only integration.
10. Destructive maintenance is manifest-driven, backed up, rehearsed, and separately approved.

## Operating flow

```text
daily scheduler
      |
      v
    sync
      |
      +--> fetch and validate Waites evidence
      +--> atomically persist references, events, fixed daily facts, and ledger
      +--> build the service-owned registered model set and adjacent drift
      +--> verify and apply raw retention
      +--> advance the durable current-through cursor

browser --> FastAPI routes --> narrow services/repositories --> operational SQLite
                                  |
                                  +--> bounded Maximo history for selected assets
```

The process works one date at a time. This caps memory growth, makes failure recovery
specific, and lets an unattended invocation resume without recalculating its own
date range.

## Durable data model

`data/processed/observations.sqlite` contains the long-lived working set:

- fixed typed daily sensor facts and snapshot revisions;
- compact asset-tree, equipment, and installation references;
- provider-stable Waites events and per-date event coverage;
- ingestion ledger, run transitions, synchronization cursor, and administration audit;
- registered model runs, assignments, centroids, aligned drift, and readiness;
- writer lease and schema/migration evidence.

There is one daily sensor representation: `sensor_daily_facts`. Trends are SQL
projections over those facts. Review, Cluster, and Drift all resolve the active
model through one versioned policy and registry.

The database is intentionally not a native timestamp warehouse. Timestamp-level
drilldown may later be fetched narrowly for one sensor/date/measurement and discarded
unless a proven product need justifies durable storage.

## File contract

The routine file tree is small:

```text
data/
  raw/
    waites/date=YYYY-MM-DD/
      endpoint JSON or JSON.GZ
      manifest.json
      validation.json
    maximo/
  processed/
    observations.sqlite
```

Raw Waites files preserve provider evidence long enough to checksum, validate, and
derive durable facts. Retention may keep, compress, or release payloads after
verification. Manifests and validation records remain auditable. Explicit exports
go to an operator-selected path outside operational data.

Snapshot, trend, feature, cluster, drift, window, report, and processed-reference
file mirrors are retired. New code must not recreate them as hidden caches or handoff
state.

## Web service contract

Routes validate HTTP inputs and delegate to services/repositories. They do not build
filesystem paths, run ingestion, or contain analysis rules. Repositories either
return typed operational data or a clear unavailable/missing/migration-required
state; there is no silent file fallback.

Responses should be scoped and projected on the server. Keep representative payloads
under the established 2 MB budget, page large detail collections, preserve sparse
measurements, and return pre-aggregated chart series where that materially reduces
browser work.

The four browser workflows share global date/scope context while keeping metric and
dimension local to the view. Review can render sensor facts, trends, measurements,
and events even when a cluster model is missing. Cluster and Drift state must report
ready, stale, missing, insufficient-data, or failed explicitly.

## Administration contract

- `serve` starts the source-bound web instance and fails concisely on configuration/store mismatch.
- `sync` sustains daily data through yesterday and is safe for a scheduler.
- `rebuild` repairs acquired dates and requires explicit refetch authorization when evidence is absent.
- `doctor` performs read-only readiness and optional bounded Maximo diagnosis.
- `export` writes deliberate external copies of operational data.

Do not expose internal pipeline steps as public command families. If historical
maintenance cannot fit an existing command without muddying it, use a versioned
script with stronger safety gates rather than expanding the everyday CLI.

## Integration boundaries

Waites adapters own request construction, authentication, raw capture, and response
shape validation. Secrets never enter manifests, JSON summaries, or exceptions.

Maximo adapters own ODBC connections and parameterized DB2 queries. Queries are
read-only, date-bounded, asset-bounded, timeout-bounded, and skipped for All Equipment.
A Maximo outage must not hide already durable Waites events.

## Change rules

When adding a feature:

1. extend the existing operational authority before adding persistence;
2. measure response size, query time, memory, and disk impact;
3. preserve source ownership and active-model policy;
4. keep work date-scoped or query-bounded;
5. prove a cache, mirror, or new table is needed before introducing it;
6. add mock contract tests and explicit failure states;
7. document migration and rollback for schema changes.

Avoid authentication, deployment expansion, caches, or permanent timestamp detail
until a concrete use case and measurement justify their lifecycle cost.

## Maintenance and recovery

Schema maintenance belongs to the service operator. Stop the service or prove the
administrative writer lease is exclusive; verify integrity and source; generate an
exact dry-run manifest; create a SQLite-consistent backup outside cleanup targets;
restore it to a disposable path; and obtain explicit approval before deletion or
compaction. Raw evidence is outside legacy cleanup scope.

The system is healthy when a clean instance can sync, serve all four web workflows,
rebuild, diagnose, and export while `data/processed` contains only the operational
SQLite store.

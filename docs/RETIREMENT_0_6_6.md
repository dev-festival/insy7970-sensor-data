# 0.6.6 Retirement Inventory and Operating Decision

This inventory records the read/write audit behind sprint `0.6.6` Checkpoint A.
It separates code and schema that are physically retired from historical live data
that remains untouched until Checkpoint B receives explicit approval.

## Runtime code inventory

| Candidate | Pre-0.6.6 reader/writer | Surviving need | Decision |
|---|---|---|---|
| `artifact_views.py` | Compatibility API/file discovery and loaders | Operational context already queries SQLite | Delete module; routes use `store.context`, repositories, and services |
| `workflows.py` | Hidden CLI orchestration and retention | `sync`/`rebuild` still require retention | Extract `raw_lifecycle.apply_retention`; delete workflows |
| `reports.py` | Hidden mock evidence/Quarto command | No named consumer; explicit exports cover tabular data | Delete module and report tests |
| `clustering/features.py` | Legacy dimension file feature builds | Registered engine already owns feature scaling | Delete module |
| `clustering/model.py` | Legacy per-file model/drift writer | Registered registry already persists models in SQLite | Delete module |
| `clustering/window.py` | Legacy range/file orchestration | Registered window query remains in `store.models` | Delete module |
| Snapshot file loaders/writers | Compatibility snapshots and SQLite/file selection | Raw-to-fixed ingestion and explicit export | Remove file paths and input mode; keep pure row builder |
| Trend file loaders/writers | Compatibility trend artifacts | On-demand SQLite trend query and export | Remove build/load/write path; keep `equipment_trends` query helper |
| Processed Waites reference writer | Fetch-time CSV mirror | Durable reference tables written atomically at ingestion | Remove writer |
| Legacy event fallback | Backfill from `waites_loads`/`waites_action_items` | Retained raw or explicit refetch status is sufficient | Remove fallback; keep fail-visible coverage |
| `artifacts.py` | Raw JSON/GZip evidence and explicit export helpers | Still active | Keep; “artifact” here means raw/export file mechanics, not processed coordination |

Production import and repository searches show no surviving import of a deleted
module and no normal reader/writer for a retired processed directory.

## Processed-directory inventory

| Candidate under `data/processed/` | Runtime writer after 0.6.6 | Runtime reader after 0.6.6 | Live dry-run result | Decision |
|---|---|---|---:|---|
| `waites/reference/` | None | None | Included in exact manifest | Retire at Checkpoint B |
| `snapshots/` | None | None | Included | Retire at Checkpoint B |
| `trends/` | None | None | Included | Retire at Checkpoint B |
| `features/` | None | None | Included | Retire at Checkpoint B |
| `clusters/` | None | None | Included | Retire at Checkpoint B |
| `drift/` | None | None | Included | Retire at Checkpoint B |
| `cluster_windows/` | None | None | Included | Retire at Checkpoint B |
| `cluster_models/` | None | None | Included | Retire at Checkpoint B |
| `cluster_model_drift/` | None | None | Included | Retire at Checkpoint B |

The live dry run found 832 exact files totaling 87,802,546 bytes. Normal clean-store
initialization now creates only `raw/waites`, `raw/maximo`, `processed`, and the
operational SQLite database. Raw evidence is never a cleanup target.

## SQLite table inventory

The configured API store was schema 8 during the 2026-08-02 dry run.

| Candidate table | Live rows | Runtime reader/writer after 0.6.6 | Verification | Decision |
|---|---:|---|---|---|
| `sensor_daily_snapshots` | 18,445 | None | 18,436 API rows hash-match fixed facts; nine mock rows counted separately | Drop only through approved manifest |
| `waites_loads` | 25 | None | Event fallback removed | Drop only through approved manifest |
| `waites_equipment` | 18 | None | Durable reference table is authoritative | Drop only through approved manifest |
| `waites_installation_points` | 0 | None | Durable reference table is authoritative | Drop only through approved manifest |
| `waites_rms_observations` | 0 | None | Fixed daily facts are authoritative | Drop only through approved manifest |
| `waites_temperature_observations` | 0 | None | Fixed daily facts are authoritative | Drop only through approved manifest |
| `waites_impact_observations` | 0 | None | Fixed daily facts are authoritative | Drop only through approved manifest |
| `waites_action_items` | 0 | None | `waites_events` and coverage are authoritative | Drop only through approved manifest |
| `waites_daily_metric_rollups` | 0 | None | Fixed daily facts are authoritative | Drop only through approved manifest |

Clean schema 10 does not create these tables. The maintenance tool drops them only
after manifest/database/file identity, source ownership, writer state, integrity,
and snapshot parity are reverified following backup and restore rehearsal.

## Protected tables and column decisions

Protected state includes fixed facts; asset-tree/equipment/installation references;
events and coverage; ingestion ledger and transitions; snapshot revisions and
migration audit; registered model, assignment, centroid, drift, and alignment rows;
synchronization state; writer lease; and administration audit.

No protected-table column is approved for removal in `0.6.6`:

- `operational_store_state.snapshot_authority` is retained for old-store detection,
  but runtime reads require `sensor_daily_facts`; retirement pins it to that value.
- `migration_status`, `migration_version`, and `snapshot_migration_audit` remain
  rollback and release evidence.
- ingestion ledger raw/native retention fields remain historical audit fields;
  new writes record native retention as `not_applicable`.
- registered model/drift `artifact_dir` and historical output metadata remain because
  active exports/repository serialization still read them; new rows use `sqlite` and
  empty output maps.

This avoids risky protected-table rewrites merely to remove nullable historical
columns. A later schema sprint may reconsider them with its own measured migration.

## Interface inventory

The CLI now exposes exactly `serve`, `sync`, `rebuild`, `doctor`, and `export`.
The former `health`, `waites`, `raw`, `store`, `snapshot`, `trend`, `workflow`,
`report`, `cluster`, and `maximo` families and their formatters are deleted rather
than hidden.

The oversized compatibility `/api/artifacts` inventory and its duplicate context
builder are removed. `/api/context` is the sole browser bootstrap contract.

Configured source replaces the retired `source` query parameter on snapshots,
trends, equipment, equipment-tree, Review, Cluster, Drift, windows, models, and
Maximo routes. Active model policy replaces `feature_space` and `k` on Review,
Cluster, Drift, and window routes. Metric and dimension remain view-local inputs and
resolve through the versioned policy. OpenAPI tests prove the retired parameters are
absent.

## Checkpoint decision

Checkpoint A is implemented and rehearsed. The checked-in maintenance utility is
dry-run-first, and environment-specific evidence is kept under ignored
`maintenance/`. Checkpoint B preparation now produces one checksum-bound approval
bundle containing the manifest, verified SQLite backup/restore, and exact processed
artifact archive. Apply revalidates that bundle before deletion, and compacted-store
activation retains the displaced database. The frozen 2026-08-03 live package passed
backup, restore, archive, disposable compaction, protected-count, and representative
web-read checks. Checkpoint B was explicitly approved and completed on 2026-08-03:
all 832 manifested files and nine legacy tables were retired, the verified compacted
database was activated, and raw evidence was unchanged. The pre-retirement backup,
restore rehearsal, artifact archive, and displaced uncompacted database remain under
ignored `maintenance/` for release rollback.

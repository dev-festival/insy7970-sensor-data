# Service Administration CLI

FastAPI and the browser are the product surface. The CLI is the small supporting
surface for starting the service, synchronizing durable data, repairing derived
facts, diagnosing readiness, and writing explicit exports.

Run commands from the project root:

```powershell
uv run sensor-data --help
```

The public command tree contains only:

```text
serve
sync
rebuild
doctor
export
```

## Configuration

Commands read service configuration from `.env` by default. Use `--env-file` to
select another file. Normal commands do not accept a source switch; one configured
source and one data directory form one service instance.

```dotenv
INSY_SOURCE_MODE=api
INSY_DATA_DIR=data
INSY_SOURCE_TIMEZONE=America/Chicago
INSY_SYNC_START_DATE=2026-07-01
INSY_RAW_RETENTION=release

WAITES_BASE_URL=https://data.api.waites.net/v1_1
WAITES_ACCESS_TOKEN=replace-me
WAITES_FACILITY_ID=679
```

`INSY_SYNC_START_DATE` is the first date a fresh instance is responsible for. It is
required before the first automatic `sync`, unless an explicit date/range establishes
the boundary. The boundary and synchronization cursor are then persisted in SQLite;
a task scheduler never maintains its own last-run date.

`INSY_SOURCE_TIMEZONE` controls the source calendar and the meaning of yesterday.
`INSY_RAW_RETENTION` accepts `release`, `compress`, or `keep`. `release` is the
sustainable default: raw endpoint payloads are removed after durable facts, models,
and verification succeed. Durable events and the small manifest/validation record
remain.

Keep `.env` and `data/` out of Git. Secret values are not included in command JSON or
audit summaries.

## Serve

```powershell
uv run sensor-data serve
uv run sensor-data serve --host 127.0.0.1 --port 8000
uv run sensor-data serve --reload
```

`serve` uses the configured source and data directory. The default browser is
`http://127.0.0.1:8000/`; OpenAPI documentation is at
`http://127.0.0.1:8000/docs`.

## Sync

The normal unattended command is deliberately stable:

```powershell
uv run sensor-data sync
```

It calculates yesterday in the configured source timezone, reads the durable
synchronization cursor, and processes outstanding dates chronologically. The caller
does not calculate date ranges.

For each date, sync performs or safely reuses:

```text
fetch -> validate -> durable facts -> verify -> active models -> retention
```

Verified daily facts prevent unnecessary source fetching. Ready models prevent
unnecessary model builds. A failure remains recorded at its date/stage, the cursor
does not advance past it, and the next invocation resumes from durable state.

Controlled operations remain available:

```powershell
uv run sensor-data sync --date 2026-07-30
uv run sensor-data sync --start-date 2026-07-24 --end-date 2026-07-30
uv run sensor-data sync --max-days 7
uv run sensor-data sync --defer-models
uv run sensor-data sync --json
```

To refresh only the current Waites navigation references, without reading data,
action items, models, daily facts, or the synchronization cursor, use:

```powershell
uv run sensor-data sync --tree
uv run sensor-data sync --tree --json
```

`--tree` fetches and atomically updates the asset-tree, equipment, and
installation-point references. It is mutually exclusive with `--date`,
`--start-date`, `--end-date`, `--max-days`, and `--defer-models`. The latest
reference-capture metadata is kept at `data/raw/waites/reference-refresh.json`.

Automatic and explicit synchronization reject the current and future source dates.
`--max-days` bounds one invocation; a remaining backlog returns partial status rather
than claiming the instance is current. `--defer-models` advances durable sensor data
while recording an intentional model-readiness gap.

Exit behavior is scheduler-safe:

| Code | Meaning |
|---:|---|
| `0` | Already current or all selected work completed |
| `1` | Configuration, source, validation, persistence, model, retention, or writer failure |
| `2` | The optional date limit was reached and backlog remains |

Only one `sync` or `rebuild` writer may own the operational store. A concurrent
attempt fails without changing operational facts. Ownership is persisted with host,
process, heartbeat, and expiry information so `doctor` can diagnose an abandoned
lease.

### Task Scheduler contract

Sprint `0.6.5` provides the command contract, not operating-system task creation.
A daily Task Scheduler or cron entry should:

1. use the project directory as its working directory;
2. invoke `uv run sensor-data sync`;
3. capture stdout/stderr and the exit code;
4. alert or retry on codes `1` and `2` according to local policy;
5. never calculate or persist dates itself.

Running after the provider has finalized the prior source day is preferable to
running immediately at midnight.

## Rebuild

`rebuild` is the explicit patch path for dates already acquired:

```powershell
uv run sensor-data rebuild --date 2026-07-30 --component models
uv run sensor-data rebuild --date 2026-07-30 --component events
uv run sensor-data rebuild --start-date 2026-07-24 --end-date 2026-07-30 --component all
```

Components are `snapshots`, `events`, `models`, and `all`. Snapshot replacement and
active-model rebuilds are forced and audited. A model-only rebuild reads durable
daily facts and does not need raw evidence.

If snapshot or event evidence was released, source reacquisition is refused unless
the operator explicitly authorizes it:

```powershell
uv run sensor-data rebuild --date 2026-07-30 --component snapshots --allow-refetch
```

Administration audit rows record the operation, source, range, component, status,
times, and secret-safe summary.

## Doctor

```powershell
uv run sensor-data doctor
uv run sensor-data doctor --start-date 2026-07-24 --end-date 2026-07-30
uv run sensor-data doctor --json
uv run sensor-data doctor --check-maximo
```

`doctor` is read-only. It reports:

- configuration and operational database path/bytes;
- schema readiness;
- synchronization boundary, cursor, and current-through status;
- missing snapshots and incomplete synchronization runs;
- missing event coverage;
- missing or stale active-policy models;
- the current writer lease, if any;
- Maximo configuration and optional connectivity.

Maximo connectivity is opt-in so routine offline diagnosis remains fast and
deterministic. `--check-maximo` performs one read-only `VALUES 1` operation under the
configured query timeout.

## Export

Exports are the only routine path that writes CSV/JSON representations. Destinations
must be outside the configured operational data directory.

```powershell
uv run sensor-data export snapshots --date 2026-07-30 --output exports/snapshot.csv
uv run sensor-data export trends --start-date 2026-07-24 --end-date 2026-07-30 --output exports/trends
uv run sensor-data export events --start-date 2026-07-24 --end-date 2026-07-30 --output exports/events.csv
uv run sensor-data export models --start-date 2026-07-24 --end-date 2026-07-30 --output exports/models.json
```

Snapshot and trend exports support optional `--equipment-id`,
`--installation-point-id`, and `--customer-asset-id` scope filters where applicable.
Event exports support equipment and installation-point scope. Every export has
`--json` summary output and does not alter operational facts.

## Startup and deployment

Run the service from the project root with the intended `.env`. A service manager
should capture stdout/stderr and restart only after configuration errors are fixed.
`serve` performs source/store preflight before Uvicorn starts, including reload mode;
source mismatch and migration-required errors are concise and traceback-free.

Use a separate directory for every source/environment. The established API instance
is `INSY_SOURCE_MODE=api` with `INSY_DATA_DIR=data`; the example mock instance is
`INSY_SOURCE_MODE=mock` with `INSY_DATA_DIR=data-mock`.

## Schema maintenance

Everyday commands initialize a clean schema but never delete historical live data.
Legacy retirement is owned by the service operator and uses the versioned script:

```powershell
uv run python scripts/retire_0_6_6.py --manifest maintenance/0.6.6-manifest.json
```

The default is read-only. Inspect the manifest's source, database SHA-256, integrity,
parity, table counts, exact file targets, and manifest SHA-256. Store all maintenance
outputs outside `data/processed`.

With the service and scheduled writers stopped, preparation is still non-destructive:

```powershell
uv run python scripts/retire_0_6_6.py --prepare `
  --manifest maintenance/0.6.6-manifest.json `
  --confirm-manifest-sha256 EXACT_MANIFEST_HASH `
  --backup maintenance/observations-pre-0.6.6.sqlite `
  --restore-test maintenance/observations-restore-rehearsal.sqlite `
  --archive maintenance/0.6.6-retired-artifacts.zip `
  --approval-bundle maintenance/0.6.6-approval-bundle.json
```

Preparation rechecks the live targets, creates and verifies a SQLite-consistent
backup, proves a disposable restore, archives every exact file target, and binds all
checksums into the approval bundle. Keep the service stopped so that bundle remains
current. Applying it is destructive and requires separate approval naming both the
manifest and approval-bundle hashes:

```powershell
uv run python scripts/retire_0_6_6.py --apply `
  --manifest maintenance/0.6.6-manifest.json `
  --confirm-manifest-sha256 EXACT_MANIFEST_HASH `
  --approval-bundle maintenance/0.6.6-approval-bundle.json `
  --confirm-approval-sha256 EXACT_APPROVAL_BUNDLE_HASH `
  --vacuum-output maintenance/observations-compacted.sqlite `
  --result maintenance/0.6.6-apply-result.json
```

The apply step revalidates the complete bundle before dropping or deleting anything.
It produces a compacted candidate but does not activate it automatically. After the
apply result, compacted database, protected counts, representative reads, `doctor`,
and exports are verified, activate the exact compacted candidate while retaining the
post-retirement predecessor:

```powershell
uv run python scripts/retire_0_6_6.py --activate `
  --apply-result maintenance/0.6.6-apply-result.json `
  --confirm-result-sha256 EXACT_RESULT_HASH `
  --confirm-vacuum-sha256 EXACT_COMPACTED_HASH `
  --displaced-database maintenance/observations-post-retirement-uncompacted.sqlite `
  --activation-result maintenance/0.6.6-activation-result.json
```

Activation refuses active writer leases, SQLite sidecar files, checksum changes, or
count/source/integrity mismatches. Keep the backup, restore rehearsal, artifact
archive, and displaced predecessor until the release is accepted. Rollback stops the
service and restores the verified backup to the configured observations path. The
cleanup script never targets raw evidence.

The retired fetch, raw, store, snapshot, trend, workflow, report, clustering, health,
and Maximo diagnostic CLI families no longer exist. Use the five public commands or
the versioned maintenance script.

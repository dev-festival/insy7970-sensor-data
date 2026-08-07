# INSY Sensor Data

INSY Sensor Data is a lightweight web service for reviewing Waites vibration data,
registered behavior clusters, drift, Waites events, and bounded Maximo maintenance
history. FastAPI and the static browser are the product. The CLI exists to operate,
repair, diagnose, and export the service.

## Architecture

The normal path is intentionally short:

```text
Waites -> validated daily ingestion -> operational SQLite -> FastAPI -> browser
                                      -> registered models
Maximo ---------------------------------------> bounded event context
```

Each service instance owns one configured source and one data directory. Daily
sensor facts, references, events, ingestion state, synchronization state, and active
models are stored once in `data/processed/observations.sqlite`. Trends and web
responses are queried from that store; they are not coordinated through parallel
CSV/JSON trees. Files are reserved for source evidence and explicit exports.

The browser provides four workflows:

- Review: one selected date and scope with trend, cluster, event, and measurement context.
- Fleet Trends: bounded multi-day series over the selected scope.
- Cluster: standalone exploration of the registered model for a date.
- Drift: gap-aware movement between registered models.

Source, feature mapping, and model `k` are service-owned. Browser requests do not
select them.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- For live Maximo reads, a DB2 ODBC driver and server-managed DSN

The GitHub Release provides two package artifacts. Use the wheel to install and run
the packaged service. Use the source archive to run the complete quality suite and
rebuild the release evidence. The offline mock path requires no Waites token, ODBC
driver, DB2 connection, or plant network.

## Install and verify the wheel

Download `insy7970_sensor_data-0.7.1-py3-none-any.whl` from the GitHub Release into
an otherwise empty directory. From that directory, create a virtual environment and
install the wheel:

```powershell
uv venv --python 3.13
uv pip install .\insy7970_sensor_data-0.7.1-py3-none-any.whl
@'
INSY_SOURCE_MODE=mock
INSY_DATA_DIR=data-mock
'@ | Set-Content .env

.\.venv\Scripts\sensor-data.exe --help
.\.venv\Scripts\sensor-data.exe sync --start-date 2025-07-09 --end-date 2025-07-11
.\.venv\Scripts\sensor-data.exe doctor --json
```

Start the installed service:

```powershell
.\.venv\Scripts\sensor-data.exe serve --host 127.0.0.1 --port 8000
```

With the service running, use a second PowerShell window in the same directory to
verify health and one representative application read:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/snapshots/2025-07-09
```

On macOS or Linux, the installed executable is
`.venv/bin/sensor-data` instead of `.venv\Scripts\sensor-data.exe`.

## Evaluate the source archive

Download `insy7970_sensor_data-0.7.1.tar.gz` from the same GitHub Release, extract
it, and enter the extracted directory:

```powershell
tar -xf .\insy7970_sensor_data-0.7.1.tar.gz
Set-Location .\insy7970_sensor_data-0.7.1
uv sync --locked --dev
Copy-Item .env.example .env
```

Run the release quality gates and rebuild both package artifacts:

```powershell
uv run ruff check .
uv run pytest
uv build --no-sources
```

A successful build creates the wheel and source archive under `dist/`. The complete
test suite and build evidence are evaluated from the source archive; the clean wheel
procedure above proves that the installed package can operate without repository
files.

## Repository setup

From a repository checkout, create the locked development environment with:

```powershell
uv sync --locked --dev
Copy-Item .env.example .env
```

`.env.example` starts an isolated mock instance:

```env
INSY_SOURCE_MODE=mock
INSY_DATA_DIR=data-mock
```

Initialize a mock installation by loading the sample fixture with:

```powershell
uv run sensor-data sync --start-date 2025-07-09 --end-date 2025-07-11
```
before attempting to start the service.

The existing API-backed store uses this matched pair:

```env
INSY_SOURCE_MODE=api
INSY_DATA_DIR=data
```

Never point mock and API modes at the same directory. Startup rejects a mismatch
with a concise error instead of mixing facts. Keep `.env`, operational data, backups,
and exports containing sensitive data out of Git.

## Run the web app

```powershell
uv run sensor-data serve
uv run sensor-data serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. Health and OpenAPI are available at `/health` and
`/docs`.

Representative read endpoints are:

```text
GET /api/context
GET /api/dates
GET /api/equipment
GET /api/equipment-tree
GET /api/snapshots/2025-07-09
GET /api/trends?start_date=2025-07-09&end_date=2025-07-11
GET /api/snapshot-review/2025-07-09?start_date=2025-07-09&end_date=2025-07-11
GET /api/clusters?date=2025-07-09&metric=rms_accel&dimension=x
GET /api/cluster-models?start_date=2025-07-09&end_date=2025-07-11
GET /api/drift?from_date=2025-07-09&to_date=2025-07-10&metric=rms_accel&dimension=x
GET /api/cluster-windows?start_date=2025-07-09&end_date=2025-07-11&dimension=x
GET /api/maximo/asset-history?assetnum=LEVF454TS&start_date=2025-07-09&end_date=2025-07-11
```

Trend detail is paged with `limit` (default 500, maximum 2000) and `offset`.
Missing measurements remain missing; the service does not manufacture observations.
Maximo is queried only for selected asset-tree/equipment/sensor context, never for
the default All Equipment scope.

## Operate the service

The public CLI contains exactly five commands:

```powershell
uv run sensor-data serve
uv run sensor-data sync
uv run sensor-data rebuild --date 2026-07-30 --component models
uv run sensor-data doctor --json
uv run sensor-data export trends --start-date 2026-07-24 --end-date 2026-07-30 --output exports/trends
```

Bare `sync` is the Task Scheduler/cron contract. It calculates yesterday in
`INSY_SOURCE_TIMEZONE`, reads the durable cursor, catches up chronologically, and
resumes verified stages after failure. The scheduler does not maintain dates.
`rebuild` is the explicit patch path. `doctor` is read-only. `export` is the only
routine writer of snapshot/trend/event/model CSV or JSON copies.

See [Service Administration CLI](docs/CLI.md) for date controls, exit codes,
recovery, scheduler setup, and exports.

## Storage and retention

```text
data/
  raw/waites/date=YYYY-MM-DD/   source payloads, manifest, validation
  raw/maximo/                   optional source evidence
  processed/observations.sqlite
```

`INSY_RAW_RETENTION` accepts `release`, `compress`, or `keep`. Release removes raw
endpoint payloads only after fixed facts are verified; the small manifest and
validation record remain. Durable Waites events survive raw release. Raw evidence
is never part of the `0.6.6` legacy cleanup target.

Historical processed mirrors and duplicate SQLite tables are retired through
`scripts/retire_0_6_6.py`. Dry run is safe and required:

```powershell
uv run python scripts/retire_0_6_6.py --manifest maintenance/0.6.6-manifest.json
```

The script records exact paths, sizes, SHA-256 checksums, schema/source identity,
table counts, parity, integrity, and writer state. Its non-destructive `--prepare`
mode creates a SQLite-consistent backup, proves a disposable restore, archives the
exact historical files, and binds their checksums into one approval bundle. Live
deletion, compaction, and activation remain separately confirmed operations; neither
a dry run nor preparation authorizes them. See
[Service Administration CLI](docs/CLI.md#schema-maintenance) for the complete
operator workflow.

## Development

Mock mode is offline and deterministic. It uses controlled dates 2025-07-09 through
2025-07-11 and requires no Waites token, ODBC driver, DB2 connection, or plant
network.

```powershell
uv run pytest
uv run pytest -m live
```

Live tests remain skipped unless `INSY_RUN_LIVE_TESTS=1` is set.

## Design and release evidence

- [Grand Design](docs/GRAND_DESIGN.md)
- [Mock Data Contract](docs/MOCK_DATA_CONTRACT.md)
- [CLI and operations](docs/CLI.md)
- [Sprint specifications](docs/sprints/README.md)
- [Reshaping benchmarks](docs/benchmarks/)

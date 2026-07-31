from __future__ import annotations

from pathlib import Path
from typing import Annotated
from datetime import date
from dataclasses import replace
import json
import os

import typer

from insy_sensor_data.admin import (
    WriterBusyError,
    build_doctor_report,
    run_rebuild,
    run_sync,
)
from insy_sensor_data.clustering.features import VALID_FEATURE_DIMENSIONS, build_feature_preview
from insy_sensor_data.clustering.model import (
    DEFAULT_RANDOM_SEED,
    VALID_CLUSTER_DIMENSIONS,
    build_cluster_run,
    compare_cluster_drift,
)
from insy_sensor_data.clustering.registry import (
    DEFAULT_FEATURE_SPACES,
    DEFAULT_REGISTRY_KS,
    FEATURE_SPACE_SPECS,
    build_cluster_model_grid,
    rebuild_active_model_date,
)
from insy_sensor_data.clustering.window import align_cluster_drift, build_cluster_window
from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.health import build_health_report
from insy_sensor_data.maximo.db import MaximoDatabaseError
from insy_sensor_data.maximo.history import load_asset_history
from insy_sensor_data.observations import (
    VALID_RAW_RETENTION_MODES,
    load_waites_observations,
    purge_waites_native_observations,
)
from insy_sensor_data.raw_lifecycle import compress_raw_waites, prune_raw_waites, verify_raw_waites
from insy_sensor_data.reports import build_mock_trend_report
from insy_sensor_data.snapshots.build import VALID_SNAPSHOT_INPUT_MODES
from insy_sensor_data.snapshots.build import build_sensor_snapshot, store_existing_sensor_snapshot
from insy_sensor_data.snapshots.trends import VALID_TREND_INPUT_MODES
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.store.events import backfill_waites_events
from insy_sensor_data.store.errors import StoreMigrationRequiredError
from insy_sensor_data.store.exports import (
    export_active_models_json,
    export_events_csv,
    export_snapshot_csv,
    export_trend_csvs,
)
from insy_sensor_data.store.schema import (
    FIXED_SNAPSHOT_TABLE,
    LEGACY_SNAPSHOT_TABLE,
    migrate_snapshot_store,
    set_snapshot_authority,
)
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.client import WaitesApiError
from insy_sensor_data.waites.validate import validate_waites_raw, validation_summary
from insy_sensor_data.workflows import (
    format_workflow_summary,
    run_api_day_workflow,
    run_api_range_workflow,
    run_mock_day_workflow,
    run_mock_range_workflow,
    run_mock_trend_workflow,
)


app = typer.Typer(
    add_completion=False,
    help="Small command-line tools for the INSY sensor data service.",
)
waites_app = typer.Typer(help="Waites source data commands.")
raw_app = typer.Typer(help="Raw evidence lifecycle commands.")
store_app = typer.Typer(help="SQLite observation store commands.")
snapshot_app = typer.Typer(help="Processed sensor snapshot commands.")
trend_app = typer.Typer(help="Processed trend commands.")
workflow_app = typer.Typer(help="Human-readable workflow commands.")
report_app = typer.Typer(help="Evidence report commands.")
cluster_app = typer.Typer(help="Clustering preparation and model commands.")
cluster_registry_app = typer.Typer(help="SQLite cluster model registry commands.")
maximo_app = typer.Typer(help="Maximo maintenance history commands.")
export_app = typer.Typer(help="Write explicit operational data exports.")
app.add_typer(export_app, name="export")
app.add_typer(waites_app, name="waites", hidden=True)
app.add_typer(raw_app, name="raw", hidden=True)
app.add_typer(store_app, name="store", hidden=True)
app.add_typer(snapshot_app, name="snapshot", hidden=True)
app.add_typer(trend_app, name="trend", hidden=True)
app.add_typer(workflow_app, name="workflow", hidden=True)
app.add_typer(report_app, name="report", hidden=True)
app.add_typer(cluster_app, name="cluster", hidden=True)
app.add_typer(maximo_app, name="maximo", hidden=True)
cluster_app.add_typer(cluster_registry_app, name="registry")


EnvFileOption = Annotated[
    Path | None,
    typer.Option(
        "--env-file",
        help="Optional .env file to load before process environment values.",
    ),
]


def _register_deprecated_group(group: typer.Typer, name: str, replacement: str) -> None:
    @group.callback()
    def deprecated_group_callback() -> None:
        _deprecated(name, replacement)


for _legacy_group, _legacy_name, _replacement in (
    (waites_app, "waites", "sync or rebuild"),
    (raw_app, "raw", "sync or rebuild"),
    (store_app, "store", "doctor, rebuild, or export"),
    (snapshot_app, "snapshot", "sync, rebuild, or export snapshots"),
    (trend_app, "trend", "export trends"),
    (workflow_app, "workflow", "sync"),
    (report_app, "report", "export"),
    (cluster_app, "cluster", "rebuild --component models or export models"),
    (maximo_app, "maximo", "doctor or the web service"),
):
    _register_deprecated_group(_legacy_group, _legacy_name, _replacement)


@app.command(hidden=True)
def health(env_file: EnvFileOption = Path(".env")) -> None:
    """Deprecated alias for doctor."""
    _deprecated("health", "doctor --json")
    settings = AppSettings.from_env(env_file=env_file)
    typer.echo(json.dumps(build_health_report(settings), sort_keys=True))


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Host interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to bind.")] = 8000,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Reload the service when code changes."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Start the FastAPI service."""
    settings = AppSettings.from_env(env_file=env_file)

    import uvicorn
    from insy_sensor_data.api.main import create_app

    if reload:
        _publish_settings_environment(settings)
        application = "insy_sensor_data.api.main:create_app"
    else:
        application = create_app(settings)
    uvicorn.run(
        application,
        factory=reload,
        host=host,
        port=port,
        reload=reload,
    )


@app.command()
def sync(
    sync_date: Annotated[
        str | None,
        typer.Option("--date", help="Synchronize one source date in YYYY-MM-DD format."),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="First source date in an explicit range."),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Last source date in an explicit range."),
    ] = None,
    max_days: Annotated[
        int | None,
        typer.Option("--max-days", help="Optionally bound dates processed in this invocation."),
    ] = None,
    defer_models: Annotated[
        bool,
        typer.Option("--defer-models", help="Persist daily facts without building active models."),
    ] = False,
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="Keep timestamp-native staging when retention releases raw data."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable summary."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Catch up durable data and active models through yesterday."""
    settings = _admin_settings(env_file, "sync", json_output)
    try:
        summary = run_sync(
            settings,
            run_date=_parse_optional_run_date(sync_date, "date"),
            start_date=_parse_optional_run_date(start_date, "start-date"),
            end_date=_parse_optional_run_date(end_date, "end-date"),
            max_days=max_days,
            defer_models=defer_models,
            keep_native=keep_native,
        )
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        WaitesApiError,
        StoreMigrationRequiredError,
        WriterBusyError,
    ) as exc:
        _admin_fail("sync", exc, json_output)
    _emit_admin_summary(summary, json_output)
    if summary["status"] == "partial":
        raise typer.Exit(code=2)


@app.command()
def rebuild(
    rebuild_date: Annotated[
        str | None,
        typer.Option("--date", help="Rebuild one acquired source date."),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="First date in the rebuild range."),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Last date in the rebuild range."),
    ] = None,
    component: Annotated[
        str,
        typer.Option("--component", help="Component: snapshots, events, models, or all."),
    ] = "all",
    allow_refetch: Annotated[
        bool,
        typer.Option("--allow-refetch", help="Permit source reacquisition when retained raw evidence is absent."),
    ] = False,
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="Keep timestamp-native staging after snapshot rebuild."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable summary."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Repair selected durable facts without routine source reacquisition."""
    settings = _admin_settings(env_file, "rebuild", json_output)
    try:
        summary = run_rebuild(
            settings,
            run_date=_parse_optional_run_date(rebuild_date, "date"),
            start_date=_parse_optional_run_date(start_date, "start-date"),
            end_date=_parse_optional_run_date(end_date, "end-date"),
            component=component,
            allow_refetch=allow_refetch,
            keep_native=keep_native,
        )
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        WaitesApiError,
        StoreMigrationRequiredError,
        WriterBusyError,
    ) as exc:
        _admin_fail("rebuild", exc, json_output)
    _emit_admin_summary(summary, json_output)


@app.command()
def doctor(
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Optional first date for readiness diagnosis."),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="Optional last date for readiness diagnosis."),
    ] = None,
    check_maximo: Annotated[
        bool,
        typer.Option("--check-maximo", help="Run one bounded read-only Maximo connectivity check."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable report."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Diagnose store, synchronization, event, and active-model readiness."""
    settings = _admin_settings(env_file, "doctor", json_output)
    try:
        report = build_doctor_report(
            settings,
            start_date=_parse_optional_run_date(start_date, "start-date"),
            end_date=_parse_optional_run_date(end_date, "end-date"),
            check_maximo=check_maximo,
        )
    except (FileNotFoundError, ValueError, RuntimeError, StoreMigrationRequiredError) as exc:
        _admin_fail("doctor", exc, json_output)
    _emit_admin_summary(report, json_output)
    if report["status"] == "error":
        raise typer.Exit(code=1)


@export_app.command("snapshots")
def export_snapshots(
    export_date: Annotated[str, typer.Option("--date", help="Snapshot source date.")],
    output: Annotated[Path, typer.Option("--output", help="Destination CSV path.")],
    equipment_id: Annotated[str | None, typer.Option("--equipment-id")] = None,
    installation_point_id: Annotated[str | None, typer.Option("--installation-point-id")] = None,
    customer_asset_id: Annotated[str | None, typer.Option("--customer-asset-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Export one snapshot from the operational store."""
    settings = _admin_settings(env_file, "export snapshots", json_output)
    try:
        summary = export_snapshot_csv(
            settings,
            run_date=_parse_run_date(export_date),
            source=settings.source_mode,
            destination=output,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            customer_asset_id=customer_asset_id,
        )
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _admin_fail("export snapshots", exc, json_output)
    _emit_admin_summary(summary, json_output)


@export_app.command("trends")
def export_trends(
    start_date: Annotated[str, typer.Option("--start-date")],
    end_date: Annotated[str, typer.Option("--end-date")],
    output: Annotated[Path, typer.Option("--output", help="Destination directory.")],
    equipment_id: Annotated[str | None, typer.Option("--equipment-id")] = None,
    installation_point_id: Annotated[str | None, typer.Option("--installation-point-id")] = None,
    customer_asset_id: Annotated[str | None, typer.Option("--customer-asset-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Export trend rows from the operational store."""
    settings = _admin_settings(env_file, "export trends", json_output)
    try:
        summary = export_trend_csvs(
            settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=settings.source_mode,
            destination=output,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            customer_asset_id=customer_asset_id,
        )
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _admin_fail("export trends", exc, json_output)
    _emit_admin_summary(summary, json_output)


@export_app.command("events")
def export_events(
    start_date: Annotated[str, typer.Option("--start-date")],
    end_date: Annotated[str, typer.Option("--end-date")],
    output: Annotated[Path, typer.Option("--output", help="Destination CSV path.")],
    equipment_id: Annotated[str | None, typer.Option("--equipment-id")] = None,
    installation_point_id: Annotated[str | None, typer.Option("--installation-point-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Export durable Waites events."""
    settings = _admin_settings(env_file, "export events", json_output)
    try:
        summary = export_events_csv(
            settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=settings.source_mode,
            destination=output,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
        )
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _admin_fail("export events", exc, json_output)
    _emit_admin_summary(summary, json_output)


@export_app.command("models")
def export_models(
    start_date: Annotated[str, typer.Option("--start-date")],
    end_date: Annotated[str, typer.Option("--end-date")],
    output: Annotated[Path, typer.Option("--output", help="Destination JSON path.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Export active-model registry summaries."""
    settings = _admin_settings(env_file, "export models", json_output)
    try:
        summary = export_active_models_json(
            settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=settings.source_mode,
            destination=output,
        )
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _admin_fail("export models", exc, json_output)
    _emit_admin_summary(summary, json_output)


@waites_app.command("fetch")
def waites_fetch(
    fetch_date: Annotated[
        str,
        typer.Option("--date", help="Source date to fetch in YYYY-MM-DD format."),
    ],
    facility: Annotated[
        int,
        typer.Option("--facility", help="Waites facility ID."),
    ] = 679,
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Fetch Waites source data and preserve raw evidence."""
    source_mode = source.strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise typer.BadParameter(f"source must be one of: {allowed}")

    settings = AppSettings.from_env(env_file=env_file)
    run_date = _parse_run_date(fetch_date)
    try:
        summary = fetch_waites(settings=settings, run_date=run_date, facility_id=facility, source=source_mode)
    except (FileNotFoundError, NotImplementedError, ValueError, WaitesApiError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@waites_app.command("validate")
def waites_validate(
    validate_date: Annotated[
        str,
        typer.Option("--date", help="Raw Waites run date to validate in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Expected source mode: mock or api."),
    ] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Validate raw Waites evidence before processing it."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    run_date = _parse_run_date(validate_date)
    try:
        report = validate_waites_raw(settings=settings, run_date=run_date, source=source_mode)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))

    typer.echo(json.dumps(validation_summary(report), sort_keys=True))
    if report["error_count"]:
        _fail(f"raw Waites validation failed; see {report['validation_path']}")


@maximo_app.command("asset-history")
def maximo_asset_history(
    assetnum: Annotated[
        str,
        typer.Option("--assetnum", help="Maximo asset number to look up."),
    ],
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="First REPORTDATE to include, YYYY-MM-DD."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Last REPORTDATE to include, YYYY-MM-DD."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Provider mode: mock fixture or live DB2/ODBC API."),
    ] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Print read-only Maximo work-order history as JSON."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        history = load_asset_history(
            settings=settings,
            assetnums=[assetnum],
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=source_mode,
        )
    except (FileNotFoundError, MaximoDatabaseError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(history, sort_keys=True))


@raw_app.command("compress")
def raw_compress(
    compress_date: Annotated[
        str,
        typer.Option("--date", help="Raw run date to compress in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Raw source system: waites."),
    ] = "waites",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Compress raw evidence files without changing their logical artifact names."""
    _validate_raw_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = compress_raw_waites(settings=settings, run_date=_parse_run_date(compress_date))
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@raw_app.command("verify")
def raw_verify(
    verify_date: Annotated[
        str,
        typer.Option("--date", help="Raw run date to verify in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Raw source system: waites."),
    ] = "waites",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Verify raw evidence checksums, byte counts, and readable storage files."""
    _validate_raw_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = verify_raw_waites(settings=settings, run_date=_parse_run_date(verify_date))
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))
    if summary["error_count"]:
        raise typer.Exit(code=1)


@raw_app.command("prune")
def raw_prune(
    older_than_days: Annotated[
        int,
        typer.Option("--older-than-days", help="Select raw runs older than this many days."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Raw source system: waites."),
    ] = "waites",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--delete", help="Preview deletions by default; use --delete to remove files."),
    ] = True,
    confirm_delete: Annotated[
        bool,
        typer.Option("--confirm-delete", help="Required with --delete before raw run directories are removed."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """List or delete old raw evidence runs after manifest verification."""
    _validate_raw_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = prune_raw_waites(
            settings=settings,
            older_than_days=older_than_days,
            dry_run=dry_run,
            confirm_delete=confirm_delete,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@store_app.command("load-waites")
def store_load_waites(
    load_date: Annotated[
        str,
        typer.Option("--date", help="Validated raw Waites run date to load in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    replace: Annotated[
        bool,
        typer.Option("--replace/--no-replace", help="Replace any existing load for this source date."),
    ] = True,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Load validated raw Waites evidence into SQLite native observation tables."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = load_waites_observations(
            settings=settings,
            run_date=_parse_run_date(load_date),
            source=source_mode,
            replace=replace,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@store_app.command("migrate-snapshots")
def store_migrate_snapshots(
    source: Annotated[
        str,
        typer.Option("--source", help="Single operational source to migrate: mock or api."),
    ],
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Copy, verify, and activate the fixed 0.6.2 snapshot table."""
    source_mode = _validate_source(source)
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), source_mode)
    try:
        summary = migrate_snapshot_store(settings, source_mode)
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@store_app.command("snapshot-authority")
def store_snapshot_authority(
    authority: Annotated[
        str,
        typer.Option("--authority", help="Read authority: fixed or legacy."),
    ],
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Select the verified fixed table or retained legacy rollback table."""
    selected = authority.strip().lower()
    mapping = {"fixed": FIXED_SNAPSHOT_TABLE, "legacy": LEGACY_SNAPSHOT_TABLE}
    if selected not in mapping:
        raise typer.BadParameter("authority must be one of: fixed, legacy")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = set_snapshot_authority(settings, mapping[selected])
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@store_app.command("backfill-events")
def store_backfill_events(
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode to migrate: mock or api."),
    ] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Backfill durable Waites events and report dates that need a narrow re-fetch."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = backfill_waites_events(
            settings=settings,
            source=source_mode,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@store_app.command("purge-native")
def store_purge_native(
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode whose ledger/snapshot must exist: mock or api."),
    ],
    purge_date: Annotated[
        str | None,
        typer.Option("--date", help="Single source date to purge in YYYY-MM-DD format."),
    ] = None,
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="Start date for a native-row purge range."),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="End date for a native-row purge range."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview native-row purge candidates without deleting."),
    ] = False,
    confirm_delete: Annotated[
        bool,
        typer.Option("--confirm-delete", help="Required to delete timestamp-native SQLite rows."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Delete timestamp-native SQLite Waites rows after snapshot persistence is verified."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = purge_waites_native_observations(
            settings=settings,
            source=source_mode,
            run_date=_parse_optional_run_date(purge_date, "date"),
            start_date=_parse_optional_run_date(start_date, "start-date"),
            end_date=_parse_optional_run_date(end_date, "end-date"),
            dry_run=dry_run,
            confirm_delete=confirm_delete,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@snapshot_app.command("build")
def snapshot_build(
    snapshot_date: Annotated[
        str,
        typer.Option("--date", help="Snapshot date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    input_mode: Annotated[
        str,
        typer.Option("--input", help="Snapshot input mode: raw or sqlite."),
    ] = "raw",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build a processed sensor snapshot from raw Waites evidence."""
    source_mode = _validate_source(source)
    snapshot_input = _validate_input_mode(input_mode, VALID_SNAPSHOT_INPUT_MODES, "snapshot input")
    settings = AppSettings.from_env(env_file=env_file)
    run_date = _parse_run_date(snapshot_date)
    try:
        summary = build_sensor_snapshot(
            settings=settings,
            run_date=run_date,
            source=source_mode,
            input_mode=snapshot_input,
        )
    except (FileNotFoundError, NotImplementedError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@snapshot_app.command("store")
def snapshot_store(
    snapshot_date: Annotated[
        str,
        typer.Option("--date", help="Existing snapshot date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Store an existing sensor_snapshot.csv in the SQLite daily snapshot table."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = store_existing_sensor_snapshot(
            settings=settings,
            run_date=_parse_run_date(snapshot_date),
            source=source_mode,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@snapshot_app.command("export")
def snapshot_export(
    snapshot_date: Annotated[str, typer.Option("--date", help="Snapshot date in YYYY-MM-DD format.")],
    destination: Annotated[Path, typer.Option("--destination", help="Explicit output CSV path.")],
    source: Annotated[str, typer.Option("--source", help="Source mode: mock or api.")] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Export one store-backed daily snapshot to an explicit CSV path."""
    source_mode = _validate_source(source)
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), source_mode)
    try:
        summary = export_snapshot_csv(
            settings,
            run_date=_parse_run_date(snapshot_date),
            source=source_mode,
            destination=destination,
        )
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@trend_app.command("build")
def trend_build(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Trend start date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Trend end date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    input_mode: Annotated[
        str,
        typer.Option("--input", help="Trend input mode: snapshots or sqlite."),
    ] = "sqlite",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build lightweight trend-ready outputs from processed snapshots."""
    source_mode = _validate_source(source)
    trend_input = _validate_input_mode(input_mode, VALID_TREND_INPUT_MODES, "trend input")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = build_trends(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=source_mode,
            input_mode=trend_input,
        )
    except (FileNotFoundError, NotImplementedError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@trend_app.command("export")
def trend_export(
    start_date: Annotated[str, typer.Option("--start-date", help="Trend start date in YYYY-MM-DD format.")],
    end_date: Annotated[str, typer.Option("--end-date", help="Trend end date in YYYY-MM-DD format.")],
    destination: Annotated[Path, typer.Option("--destination", help="Explicit output directory.")],
    source: Annotated[str, typer.Option("--source", help="Source mode: mock or api.")] = "mock",
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Export store-backed sensor and equipment trends to an explicit directory."""
    source_mode = _validate_source(source)
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), source_mode)
    try:
        summary = export_trend_csvs(
            settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=source_mode,
            destination=destination,
        )
    except (FileNotFoundError, ValueError, StoreMigrationRequiredError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


@workflow_app.command("mock-day")
def workflow_mock_day(
    workflow_date: Annotated[
        str,
        typer.Option("--date", help="Mock workflow date in YYYY-MM-DD format."),
    ],
    facility: Annotated[
        int,
        typer.Option("--facility", help="Waites facility ID."),
    ] = 679,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the combined workflow summary as JSON."),
    ] = False,
    raw_retention: Annotated[
        str,
        typer.Option("--raw-retention", help="Raw retention mode after snapshot success: keep, compress, or release."),
    ] = "keep",
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="With release mode, keep timestamp-native SQLite rows for inspection."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run the friendly one-day mock workflow."""
    retention_mode = _validate_input_mode(raw_retention, VALID_RAW_RETENTION_MODES, "raw retention")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = run_mock_day_workflow(
            settings=settings,
            run_date=_parse_run_date(workflow_date),
            facility_id=facility,
            raw_retention=retention_mode,
            keep_native=keep_native,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_workflow_summary(summary, json_output)


@workflow_app.command("mock-trend")
def workflow_mock_trend(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Trend start date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Trend end date in YYYY-MM-DD format."),
    ],
    facility: Annotated[
        int,
        typer.Option("--facility", help="Waites facility ID."),
    ] = 679,
    input_mode: Annotated[
        str,
        typer.Option("--input", help="Trend input mode: snapshots or sqlite."),
    ] = "sqlite",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the combined workflow summary as JSON."),
    ] = False,
    raw_retention: Annotated[
        str,
        typer.Option("--raw-retention", help="Raw retention mode after each snapshot success: keep, compress, or release."),
    ] = "keep",
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="With release mode, keep timestamp-native SQLite rows for inspection."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run the friendly multi-day mock trend workflow."""
    trend_input = _validate_input_mode(input_mode, VALID_TREND_INPUT_MODES, "trend input")
    retention_mode = _validate_input_mode(raw_retention, VALID_RAW_RETENTION_MODES, "raw retention")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = run_mock_trend_workflow(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            facility_id=facility,
            trend_input=trend_input,
            raw_retention=retention_mode,
            keep_native=keep_native,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_workflow_summary(summary, json_output)


@workflow_app.command("mock-range")
def workflow_mock_range(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Range start date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Range end date in YYYY-MM-DD format."),
    ],
    facility: Annotated[
        int,
        typer.Option("--facility", help="Waites facility ID."),
    ] = 679,
    input_mode: Annotated[
        str,
        typer.Option("--input", help="Trend input mode: snapshots or sqlite."),
    ] = "sqlite",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Primary clustering dimension: x, y, z, or temperature."),
    ] = "x",
    dimensions: Annotated[
        str | None,
        typer.Option("--dimensions", help="Comma-separated clustering dimensions, e.g. x,y,z,temperature."),
    ] = None,
    k: Annotated[
        int,
        typer.Option("--k", help="Number of KMeans clusters for cluster windows."),
    ] = 4,
    cluster_models: Annotated[
        bool,
        typer.Option(
            "--cluster-models/--legacy-clusters",
            help="Build the SQLite cluster model registry instead of legacy cluster-window artifacts.",
        ),
    ] = False,
    feature_spaces: Annotated[
        str | None,
        typer.Option(
            "--feature-spaces",
            help="Comma-separated registered feature spaces, e.g. x_accel,y_vel,z_vel,temperature.",
        ),
    ] = None,
    ks: Annotated[
        str | None,
        typer.Option("--ks", help="Comma-separated registered k values. Defaults to 5 for cluster models."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the combined workflow summary as JSON."),
    ] = False,
    raw_retention: Annotated[
        str,
        typer.Option("--raw-retention", help="Raw retention mode after each snapshot success: keep, compress, or release."),
    ] = "keep",
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="With release mode, keep timestamp-native SQLite rows for inspection."),
    ] = False,
    skip_fetch: Annotated[
        bool,
        typer.Option("--skip-fetch", help="Reuse existing snapshots or raw evidence without fetching."),
    ] = False,
    skip_cluster: Annotated[
        bool,
        typer.Option("--skip-cluster", help="Build snapshots/trends without cluster windows."),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option("--resume/--no-resume", help="Reuse valid existing artifacts by default."),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild valid existing artifacts deliberately."),
    ] = False,
    max_days: Annotated[
        int,
        typer.Option("--max-days", help="Refuse ranges larger than this many dates."),
    ] = 31,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run the friendly mock operating-window workflow."""
    trend_input = _validate_input_mode(input_mode, VALID_TREND_INPUT_MODES, "trend input")
    retention_mode = _validate_input_mode(raw_retention, VALID_RAW_RETENTION_MODES, "raw retention")
    cluster_dimensions = _parse_dimensions(dimension, dimensions)
    registry_feature_spaces = _parse_feature_spaces(feature_spaces)
    registry_ks = _parse_ks(ks)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = run_mock_range_workflow(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            facility_id=facility,
            trend_input=trend_input,
            raw_retention=retention_mode,
            keep_native=keep_native,
            dimensions=cluster_dimensions,
            k=k,
            cluster_models=cluster_models,
            feature_spaces=registry_feature_spaces,
            ks=registry_ks,
            skip_fetch=skip_fetch,
            skip_cluster=skip_cluster,
            force=force or not resume,
            max_days=max_days,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_workflow_summary(summary, json_output)


@workflow_app.command("api-day")
def workflow_api_day(
    workflow_date: Annotated[
        str,
        typer.Option("--date", help="API workflow date in YYYY-MM-DD format."),
    ],
    facility: Annotated[
        int,
        typer.Option("--facility", help="Waites facility ID."),
    ] = 679,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the combined workflow summary as JSON."),
    ] = False,
    raw_retention: Annotated[
        str,
        typer.Option("--raw-retention", help="Raw retention mode after snapshot success: release, compress, or keep."),
    ] = "release",
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="With release mode, keep timestamp-native SQLite rows for inspection."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run the friendly one-day live Waites canary workflow."""
    retention_mode = _validate_input_mode(raw_retention, VALID_RAW_RETENTION_MODES, "raw retention")
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), "api")
    try:
        summary = run_api_day_workflow(
            settings=settings,
            run_date=_parse_run_date(workflow_date),
            facility_id=facility,
            raw_retention=retention_mode,
            keep_native=keep_native,
        )
    except (FileNotFoundError, ValueError, WaitesApiError) as exc:
        _fail(str(exc))
    _emit_workflow_summary(summary, json_output)


@workflow_app.command("api-range")
def workflow_api_range(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Range start date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Range end date in YYYY-MM-DD format."),
    ],
    facility: Annotated[
        int,
        typer.Option("--facility", help="Waites facility ID."),
    ] = 679,
    input_mode: Annotated[
        str,
        typer.Option("--input", help="Trend input mode: snapshots or sqlite."),
    ] = "sqlite",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Primary clustering dimension: x, y, z, or temperature."),
    ] = "x",
    dimensions: Annotated[
        str | None,
        typer.Option("--dimensions", help="Comma-separated clustering dimensions, e.g. x,y,z,temperature."),
    ] = None,
    k: Annotated[
        int,
        typer.Option("--k", help="Number of KMeans clusters for cluster windows."),
    ] = 4,
    cluster_models: Annotated[
        bool,
        typer.Option(
            "--cluster-models/--legacy-clusters",
            help="Build the SQLite cluster model registry instead of legacy cluster-window artifacts.",
        ),
    ] = False,
    feature_spaces: Annotated[
        str | None,
        typer.Option(
            "--feature-spaces",
            help="Comma-separated registered feature spaces, e.g. x_accel,y_vel,z_vel,temperature.",
        ),
    ] = None,
    ks: Annotated[
        str | None,
        typer.Option("--ks", help="Comma-separated registered k values. Defaults to 5 for cluster models."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the combined workflow summary as JSON."),
    ] = False,
    raw_retention: Annotated[
        str,
        typer.Option("--raw-retention", help="Raw retention mode after each snapshot success: release, compress, or keep."),
    ] = "release",
    keep_native: Annotated[
        bool,
        typer.Option("--keep-native", help="With release mode, keep timestamp-native SQLite rows for inspection."),
    ] = False,
    skip_fetch: Annotated[
        bool,
        typer.Option("--skip-fetch", help="Reuse existing snapshots or raw evidence without fetching."),
    ] = False,
    skip_cluster: Annotated[
        bool,
        typer.Option("--skip-cluster", help="Build snapshots/trends without cluster windows."),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option("--resume/--no-resume", help="Reuse valid existing artifacts by default."),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild valid existing artifacts deliberately."),
    ] = False,
    max_days: Annotated[
        int,
        typer.Option("--max-days", help="Refuse ranges larger than this many dates."),
    ] = 31,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run the friendly live Waites operating-window workflow."""
    trend_input = _validate_input_mode(input_mode, VALID_TREND_INPUT_MODES, "trend input")
    retention_mode = _validate_input_mode(raw_retention, VALID_RAW_RETENTION_MODES, "raw retention")
    cluster_dimensions = _parse_dimensions(dimension, dimensions)
    registry_feature_spaces = _parse_feature_spaces(feature_spaces)
    registry_ks = _parse_ks(ks)
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), "api")
    try:
        summary = run_api_range_workflow(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            facility_id=facility,
            trend_input=trend_input,
            raw_retention=retention_mode,
            keep_native=keep_native,
            dimensions=cluster_dimensions,
            k=k,
            cluster_models=cluster_models,
            feature_spaces=registry_feature_spaces,
            ks=registry_ks,
            skip_fetch=skip_fetch,
            skip_cluster=skip_cluster,
            force=force or not resume,
            max_days=max_days,
        )
    except (FileNotFoundError, ValueError, WaitesApiError) as exc:
        _fail(str(exc))
    _emit_workflow_summary(summary, json_output)


@report_app.command("mock-trend")
def report_mock_trend(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Report start date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Report end date in YYYY-MM-DD format."),
    ],
    render_quarto: Annotated[
        bool,
        typer.Option("--render/--no-render", help="Render HTML with Quarto when available."),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the report summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build an evidence report for the controlled mock trend range."""
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = build_mock_trend_report(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            render_quarto=render_quarto,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_report_summary(summary, json_output)


@cluster_app.command("features")
def cluster_features(
    feature_date: Annotated[
        str,
        typer.Option("--date", help="Snapshot date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Feature dimension to build: x, y, z, temperature, or all."),
    ] = "all",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the feature preview summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build dimension-specific feature matrix previews without running clustering."""
    source_mode = _validate_source(source)
    feature_dimension = _validate_input_mode(dimension, VALID_FEATURE_DIMENSIONS, "dimension")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = build_feature_preview(
            settings=settings,
            run_date=_parse_run_date(feature_date),
            source=source_mode,
            axis=feature_dimension,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_feature_summary(summary, json_output)


@cluster_app.command("run")
def cluster_run(
    cluster_date: Annotated[
        str,
        typer.Option("--date", help="Snapshot date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Clustering dimension: x, y, z, or temperature."),
    ] = "x",
    k: Annotated[
        int,
        typer.Option("--k", help="Number of KMeans clusters."),
    ] = 4,
    random_seed: Annotated[
        int,
        typer.Option("--random-seed", help="Deterministic KMeans initialization seed."),
    ] = DEFAULT_RANDOM_SEED,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the cluster run summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run deterministic dimension-specific KMeans clustering."""
    source_mode = _validate_source(source)
    cluster_dimension = _validate_input_mode(dimension, VALID_CLUSTER_DIMENSIONS, "dimension")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = build_cluster_run(
            settings=settings,
            run_date=_parse_run_date(cluster_date),
            source=source_mode,
            dimension=cluster_dimension,
            k=k,
            random_seed=random_seed,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_cluster_summary(summary, json_output)


@cluster_app.command("drift")
def cluster_drift(
    from_date: Annotated[
        str,
        typer.Option("--from-date", help="Earlier cluster date in YYYY-MM-DD format."),
    ],
    to_date: Annotated[
        str,
        typer.Option("--to-date", help="Later cluster date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Clustering dimension: x, y, z, or temperature."),
    ] = "x",
    k: Annotated[
        int,
        typer.Option("--k", help="Cluster count used by both cluster runs."),
    ] = 4,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the drift summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Compare cluster assignments and centroids between two processed dates."""
    source_mode = _validate_source(source)
    cluster_dimension = _validate_input_mode(dimension, VALID_CLUSTER_DIMENSIONS, "dimension")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = compare_cluster_drift(
            settings=settings,
            from_date=_parse_run_date(from_date),
            to_date=_parse_run_date(to_date),
            source=source_mode,
            dimension=cluster_dimension,
            k=k,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_cluster_drift_summary(summary, json_output)


@cluster_app.command("align-drift")
def cluster_align_drift(
    from_date: Annotated[
        str,
        typer.Option("--from-date", help="Earlier cluster date in YYYY-MM-DD format."),
    ],
    to_date: Annotated[
        str,
        typer.Option("--to-date", help="Later cluster date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Clustering dimension: x, y, z, or temperature."),
    ] = "x",
    k: Annotated[
        int,
        typer.Option("--k", help="Cluster count used by both cluster runs."),
    ] = 4,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild existing aligned drift artifacts."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the aligned drift summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Compare cluster drift after centroid-aligning labels between dates."""
    source_mode = _validate_source(source)
    cluster_dimension = _validate_input_mode(dimension, VALID_CLUSTER_DIMENSIONS, "dimension")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = align_cluster_drift(
            settings=settings,
            from_date=_parse_run_date(from_date),
            to_date=_parse_run_date(to_date),
            source=source_mode,
            dimension=cluster_dimension,
            k=k,
            force=force,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_aligned_drift_summary(summary, json_output)


@cluster_app.command("window")
def cluster_window(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Window start date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Window end date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    dimension: Annotated[
        str,
        typer.Option("--dimension", "--axis", help="Clustering dimension: x, y, z, or temperature."),
    ] = "x",
    k: Annotated[
        int,
        typer.Option("--k", help="Number of KMeans clusters."),
    ] = 4,
    random_seed: Annotated[
        int,
        typer.Option("--random-seed", help="Deterministic KMeans initialization seed."),
    ] = DEFAULT_RANDOM_SEED,
    resume: Annotated[
        bool,
        typer.Option("--resume/--no-resume", help="Reuse valid existing artifacts by default."),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild valid existing artifacts deliberately."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the cluster window summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Run or reuse per-date clusters and build centroid-aligned window interpretation."""
    source_mode = _validate_source(source)
    cluster_dimension = _validate_input_mode(dimension, VALID_CLUSTER_DIMENSIONS, "dimension")
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = build_cluster_window(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=source_mode,
            dimension=cluster_dimension,
            k=k,
            random_seed=random_seed,
            force=force or not resume,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_cluster_window_summary(summary, json_output)


@cluster_registry_app.command("build-grid")
def cluster_registry_build_grid(
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="First model date in YYYY-MM-DD format."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Last model date in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    feature_spaces: Annotated[
        str,
        typer.Option(
            "--feature-spaces",
            help="Comma-separated feature spaces: x_accel,y_vel,z_vel,temperature.",
        ),
    ] = ",".join(DEFAULT_FEATURE_SPACES),
    ks: Annotated[
        str,
        typer.Option("--ks", help="Comma-separated k values for registered models."),
    ] = ",".join(str(value) for value in DEFAULT_REGISTRY_KS),
    random_seed: Annotated[
        int,
        typer.Option("--random-seed", help="Deterministic KMeans initialization seed."),
    ] = DEFAULT_RANDOM_SEED,
    resume: Annotated[
        bool,
        typer.Option("--resume/--no-resume", help="Reuse complete registered models by default."),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild complete registered models deliberately."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the registry grid summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build the offline SQLite cluster model registry over a date range."""
    source_mode = _validate_source(source)
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), source_mode)
    try:
        summary = build_cluster_model_grid(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=source_mode,
            feature_spaces=_parse_feature_spaces(feature_spaces),
            ks=_parse_ks(ks),
            random_seed=random_seed,
            force=force or not resume,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    _emit_cluster_registry_summary(summary, json_output)


@cluster_registry_app.command("rebuild-date")
def cluster_registry_rebuild_date(
    rebuild_date: Annotated[
        str,
        typer.Option("--date", help="Snapshot date to rebuild in YYYY-MM-DD format."),
    ],
    source: Annotated[
        str,
        typer.Option("--source", help="Source mode: mock or api."),
    ] = "mock",
    feature_spaces: Annotated[
        str,
        typer.Option(
            "--feature-spaces",
            help="Comma-separated active feature spaces; defaults to all.",
        ),
    ] = ",".join(DEFAULT_FEATURE_SPACES),
    force: Annotated[
        bool,
        typer.Option("--force/--resume", help="Rebuild the selected date by default."),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the rebuild summary as JSON."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Rebuild one active model date and only its adjacent drift pairs."""
    source_mode = _validate_source(source)
    settings = _settings_for_source(AppSettings.from_env(env_file=env_file), source_mode)
    try:
        summary = rebuild_active_model_date(
            settings,
            run_date=_parse_run_date(rebuild_date),
            source=source_mode,
            feature_spaces=_parse_feature_spaces(feature_spaces),
            force=force,
        )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise typer.BadParameter(f"source must be one of: {allowed}")
    return source_mode


def _deprecated(command: str, replacement: str) -> None:
    typer.echo(
        f"Deprecated command '{command}'; use 'sensor-data {replacement}'.",
        err=True,
    )


def _admin_fail(operation: str, exc: Exception, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "operation": operation,
                    "status": "failed",
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
    else:
        typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1)


def _admin_settings(
    env_file: Path | None,
    operation: str,
    json_output: bool,
) -> AppSettings:
    try:
        return AppSettings.from_env(env_file=env_file)
    except ValueError as exc:
        _admin_fail(operation, exc, json_output)


def _publish_settings_environment(settings: AppSettings) -> None:
    values = {
        "INSY_APP_ENV": settings.app_env,
        "INSY_SOURCE_MODE": settings.source_mode,
        "INSY_DATA_DIR": str(settings.data_dir),
        "INSY_SOURCE_TIMEZONE": settings.source_timezone,
        "INSY_RAW_RETENTION": settings.raw_retention_mode,
        "WAITES_BASE_URL": settings.waites_base_url,
        "WAITES_ACCESS_TOKEN": settings.waites_access_token,
        "WAITES_FACILITY_ID": str(settings.waites_facility_id),
        "MAXIMO_DSN": settings.maximo_dsn,
        "MAXIMO_SCHEMA": settings.maximo_schema,
        "MAXIMO_SITE_ID": settings.maximo_site_id,
        "MAXIMO_ASSETNUM_MAX_LENGTH": str(settings.maximo_assetnum_max_length),
        "MAXIMO_QUERY_TIMEOUT_SECONDS": str(settings.maximo_query_timeout_seconds),
    }
    if settings.sync_start_date is not None:
        values["INSY_SYNC_START_DATE"] = settings.sync_start_date.isoformat()
    os.environ.update(values)


def _emit_admin_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True, default=str))
        return
    operation = str(summary.get("operation") or "export").replace("_", " ").title()
    status = str(summary.get("status") or "complete").replace("_", " ").upper()
    typer.echo(f"{operation}: {status}")
    if summary.get("source"):
        typer.echo(f"Source: {summary['source']}")
    start_date = summary.get("start_date") or summary.get("date")
    end_date = summary.get("end_date")
    if start_date and end_date and start_date != end_date:
        typer.echo(f"Dates: {start_date} to {end_date}")
    elif start_date:
        typer.echo(f"Date: {start_date}")
    if summary.get("current_through"):
        typer.echo(f"Current through: {summary['current_through']}")
    if summary.get("completed_date_count") is not None:
        typer.echo(f"Completed dates: {summary['completed_date_count']}")
    if summary.get("remaining_date_count"):
        typer.echo(f"Remaining dates: {summary['remaining_date_count']}")
    if summary.get("destination"):
        typer.echo(f"Output: {summary['destination']}")
    if summary.get("row_count") is not None:
        typer.echo(f"Rows: {summary['row_count']}")
    synchronization = summary.get("synchronization")
    if isinstance(synchronization, dict):
        typer.echo(f"Current through: {synchronization.get('current_through') or 'not established'}")
        typer.echo(f"Readiness issues: {synchronization.get('issue_count', 0)}")


def _settings_for_source(settings: AppSettings, source: str) -> AppSettings:
    return settings if settings.source_mode == source else replace(settings, source_mode=source)


def _validate_raw_source(source: str) -> str:
    source_system = source.strip().lower()
    if source_system != "waites":
        raise typer.BadParameter("raw source must be: waites")
    return source_system


def _validate_input_mode(input_mode: str, allowed_modes: set[str], label: str) -> str:
    normalized = input_mode.strip().lower()
    if normalized not in allowed_modes:
        allowed = ", ".join(sorted(allowed_modes))
        raise typer.BadParameter(f"{label} must be one of: {allowed}")
    return normalized


def _parse_run_date(raw_date: str) -> date:
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise typer.BadParameter("date must be in YYYY-MM-DD format") from exc


def _parse_optional_run_date(raw_date: str | None, label: str) -> date | None:
    if raw_date is None:
        return None
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise typer.BadParameter(f"{label} must be in YYYY-MM-DD format") from exc


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _parse_dimensions(dimension: str, dimensions: str | None) -> list[str]:
    raw_values = dimensions.split(",") if dimensions else [dimension]
    parsed = [
        _validate_input_mode(raw_value.strip(), VALID_CLUSTER_DIMENSIONS, "dimension")
        for raw_value in raw_values
        if raw_value.strip()
    ]
    if not parsed:
        raise typer.BadParameter("at least one dimension is required")
    return parsed


def _parse_feature_spaces(feature_spaces: str | None) -> list[str] | None:
    if feature_spaces is None:
        return None
    parsed = []
    for raw_value in feature_spaces.split(","):
        value = raw_value.strip().lower()
        if not value:
            continue
        if value not in FEATURE_SPACE_SPECS:
            allowed = ", ".join(FEATURE_SPACE_SPECS)
            raise typer.BadParameter(f"feature_space must be one of: {allowed}")
        parsed.append(value)
    if not parsed:
        raise typer.BadParameter("at least one feature space is required")
    return parsed


def _parse_ks(ks: str | None) -> list[int] | None:
    if ks is None:
        return None
    parsed = []
    for raw_value in ks.split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            k = int(value)
        except ValueError as exc:
            raise typer.BadParameter("ks must be comma-separated integers") from exc
        if k < 1:
            raise typer.BadParameter("k must be at least 1")
        parsed.append(k)
    if not parsed:
        raise typer.BadParameter("at least one k value is required")
    return parsed


def _emit_workflow_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
    else:
        typer.echo(format_workflow_summary(summary))


def _emit_report_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    failed = int(summary.get("failed_check_count") or 0)
    total = int(summary.get("check_count") or 0)
    passed = total - failed
    typer.echo(f"Mock trend evidence report: {summary['start_date']} to {summary['end_date']}")
    typer.echo("")
    typer.echo(f"Report directory: {summary['report_dir']}")
    typer.echo(f"Markdown: {summary['report_md_path']}")
    typer.echo(f"HTML: {summary['report_html_path']}")
    typer.echo(f"Checks: {passed} passed, {failed} failed")
    typer.echo(f"Samples: {len(summary.get('sample_paths', {}))}")
    typer.echo(f"Charts: {len(summary.get('chart_paths', {}))}")
    if failed:
        raise typer.Exit(code=1)


def _emit_feature_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    dimensions = summary.get("dimensions", {})
    typer.echo(f"Feature preview: {summary['date']} ({summary['source']})")
    typer.echo("")
    typer.echo(f"Policy: {summary['feature_policy']}")
    typer.echo(f"Output: {summary['feature_dir']}")
    for dimension_name, dimension_summary in dimensions.items():
        typer.echo("")
        typer.echo(f"Dimension {dimension_name}: {dimension_summary['status']}")
        typer.echo(f"           Rows: {dimension_summary['row_count']}")
        typer.echo(f"       Features: {dimension_summary['feature_count']}")
        typer.echo(f"         Matrix: {dimension_summary['matrix_path']}")
        warning_count = len(dimension_summary.get("warnings", []))
        typer.echo(f"      Warnings: {warning_count}")


def _emit_cluster_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    typer.echo(f"Cluster run: {summary['date']} ({summary['source']}, {summary['dimension']}, k={summary['k']})")
    typer.echo("")
    typer.echo(f"Output: {summary['cluster_dir']}")
    typer.echo(f"Rows: {summary['row_count']}")
    typer.echo(f"Features: {summary['feature_count']}")
    typer.echo(f"Inertia: {summary['inertia']}")
    typer.echo(f"Silhouette: {summary.get('silhouette_score')}")
    typer.echo(f"Calinski-Harabasz: {summary.get('calinski_harabasz_score')}")
    typer.echo(f"Sensor clusters: {summary['sensor_clusters_path']}")
    typer.echo(f"Cluster summary: {summary['cluster_summary_path']}")
    typer.echo(f"PCA coordinates: {summary['pca_coordinates_path']}")


def _emit_cluster_drift_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    typer.echo(
        f"Cluster drift: {summary['from_date']} to {summary['to_date']} "
        f"({summary['source']}, {summary['dimension']}, k={summary['k']})"
    )
    typer.echo("")
    typer.echo(f"Output: {summary['drift_dir']}")
    typer.echo(f"Matched sensors: {summary['matched_sensor_count']}")
    typer.echo(f"Changed sensors: {summary['changed_sensor_count']}")
    typer.echo(f"Changed ratio: {summary.get('changed_ratio')}")
    typer.echo(f"Assignment drift: {summary['cluster_drift_path']}")
    typer.echo(f"Centroid drift: {summary['centroid_drift_path']}")


def _emit_aligned_drift_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    typer.echo(
        f"Aligned cluster drift: {summary['from_date']} to {summary['to_date']} "
        f"({summary['source']}, {summary['dimension']}, k={summary['k']})"
    )
    typer.echo("")
    typer.echo(f"Output: {summary['drift_dir']}")
    typer.echo(f"Matched sensors: {summary['matched_sensor_count']}")
    typer.echo(f"Raw label changes: {summary['raw_label_changed_count']}")
    typer.echo(f"Aligned changes: {summary['aligned_changed_count']}")
    typer.echo(f"Aligned changed ratio: {summary.get('aligned_changed_ratio')}")
    typer.echo(f"Interpretation: {summary.get('interpretation')}")
    typer.echo(f"Aligned sensor drift: {summary['aligned_cluster_drift_path']}")
    typer.echo(f"Centroid alignment: {summary['centroid_alignment_path']}")


def _emit_cluster_window_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    typer.echo(
        f"Cluster window: {summary['start_date']} to {summary['end_date']} "
        f"({summary['source']}, {summary['dimension']}, k={summary['k']})"
    )
    typer.echo("")
    typer.echo(f"Output: {summary['cluster_window_dir']}")
    typer.echo(f"Dates: {summary['date_count']}")
    typer.echo(f"Adjacent drift pairs: {summary['pair_count']}")
    typer.echo(f"Warnings: {summary['warning_count']}")
    typer.echo(f"Window summary: {summary['window_summary_path']}")
    typer.echo(f"Quality summary: {summary['quality_summary_path']}")
    typer.echo(f"Aligned drift summary: {summary['aligned_drift_summary_path']}")
    for run in summary.get("date_runs", []):
        typer.echo(
            f"{run['date']}: {run['quality_level']} "
            f"(rows={run['row_count']}, silhouette={run.get('silhouette_score')})"
        )
    for pair in summary.get("aligned_pairs", []):
        typer.echo(
            f"{pair['from_date']} -> {pair['to_date']}: "
            f"raw={pair['raw_label_changed_count']}, aligned={pair['aligned_changed_count']}"
        )


def _emit_cluster_registry_summary(summary: dict[str, object], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    typer.echo("Cluster model grid complete")
    typer.echo("")
    typer.echo(f"Source: {summary['source']}")
    typer.echo(f"Dates: {summary['start_date']} to {summary['end_date']}")
    typer.echo(f"Feature spaces: {', '.join(summary.get('feature_spaces', []))}")
    typer.echo(f"k values: {', '.join(str(value) for value in summary.get('ks', []))}")
    typer.echo(f"Models built: {summary.get('models_built', 0)}")
    typer.echo(f"Models reused: {summary.get('models_reused', 0)}")
    typer.echo(f"Models failed: {summary.get('models_failed', 0)}")
    typer.echo(
        f"Models insufficient data: {summary.get('models_insufficient_data', 0)}"
    )
    typer.echo(f"Drift pairs built: {summary.get('drift_built', 0)}")
    typer.echo(f"Drift pairs reused: {summary.get('drift_reused', 0)}")
    typer.echo(f"Drift pairs skipped: {summary.get('drift_skipped', 0)}")
    typer.echo(f"SQLite registry: {summary.get('database_path')}")

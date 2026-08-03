from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated
import json
import os

import typer

from insy_sensor_data.admin import (
    WriterBusyError,
    build_doctor_report,
    run_rebuild,
    run_sync,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.errors import StoreMigrationRequiredError
from insy_sensor_data.store.exports import (
    export_active_models_json,
    export_events_csv,
    export_snapshot_csv,
    export_trend_csvs,
)
from insy_sensor_data.waites.client import WaitesApiError


app = typer.Typer(
    add_completion=False,
    help="Administration commands for the INSY sensor data web service.",
)
export_app = typer.Typer(help="Write explicit copies of operational data.")
app.add_typer(export_app, name="export")


EnvFileOption = Annotated[
    Path | None,
    typer.Option(
        "--env-file",
        help="Optional .env file to load before process environment values.",
    ),
]


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
    """Start the FastAPI web service."""
    settings = _admin_settings(env_file, "serve", False)

    import uvicorn
    from insy_sensor_data.api.main import create_app

    try:
        validated_application = create_app(settings)
    except (FileNotFoundError, ValueError, RuntimeError, StoreMigrationRequiredError) as exc:
        _admin_fail("serve", exc, False)

    if reload:
        _publish_settings_environment(settings)
        application = "insy_sensor_data.api.main:create_app"
    else:
        application = validated_application
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
    tree: Annotated[
        bool,
        typer.Option(
            "--tree",
            help="Refresh current asset-tree, equipment, and installation-point references only.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable summary."),
    ] = False,
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Catch up daily data, or refresh current Waites references with --tree."""
    settings = _admin_settings(env_file, "sync", json_output)
    try:
        summary = run_sync(
            settings,
            run_date=_parse_optional_run_date(sync_date, "date"),
            start_date=_parse_optional_run_date(start_date, "start-date"),
            end_date=_parse_optional_run_date(end_date, "end-date"),
            max_days=max_days,
            defer_models=defer_models,
            tree=tree,
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
        typer.Option(
            "--allow-refetch",
            help="Permit source reacquisition when retained raw evidence is absent.",
        ),
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


def _admin_fail(operation: str, exc: Exception, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                {"operation": operation, "status": "failed", "error": str(exc)},
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
    if summary.get("mode"):
        typer.echo(f"Mode: {summary['mode']}")
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
    row_counts = summary.get("row_counts")
    if isinstance(row_counts, dict):
        formatted_counts = ", ".join(
            f"{key}={row_counts[key]}" for key in sorted(row_counts)
        )
        typer.echo(f"Reference rows: {formatted_counts}")
    if summary.get("capture_manifest"):
        typer.echo(f"Capture: {summary['capture_manifest']}")
    synchronization = summary.get("synchronization")
    if isinstance(synchronization, dict):
        typer.echo(
            f"Current through: {synchronization.get('current_through') or 'not established'}"
        )
        typer.echo(f"Readiness issues: {synchronization.get('issue_count', 0)}")


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

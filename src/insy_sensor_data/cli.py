from __future__ import annotations

from pathlib import Path
from typing import Annotated
from datetime import date
import json
import os

import typer

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.health import build_health_report
from insy_sensor_data.raw_lifecycle import compress_raw_waites, prune_raw_waites, verify_raw_waites
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.client import WaitesApiError
from insy_sensor_data.waites.validate import validate_waites_raw, validation_summary


app = typer.Typer(
    add_completion=False,
    help="Small command-line tools for the INSY sensor data service.",
)
waites_app = typer.Typer(help="Waites source data commands.")
raw_app = typer.Typer(help="Raw evidence lifecycle commands.")
snapshot_app = typer.Typer(help="Processed sensor snapshot commands.")
trend_app = typer.Typer(help="Processed trend commands.")
app.add_typer(waites_app, name="waites")
app.add_typer(raw_app, name="raw")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(trend_app, name="trend")


EnvFileOption = Annotated[
    Path | None,
    typer.Option(
        "--env-file",
        help="Optional .env file to load before process environment values.",
    ),
]


@app.command()
def health(env_file: EnvFileOption = Path(".env")) -> None:
    """Print service health and configuration status as JSON."""
    settings = AppSettings.from_env(env_file=env_file)
    typer.echo(json.dumps(build_health_report(settings), sort_keys=True))


@app.command()
def serve(
    source: Annotated[
        str,
        typer.Option("--source", help="Data source mode for the service: mock or api."),
    ] = "mock",
    host: Annotated[str, typer.Option("--host", help="Host interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to bind.")] = 8000,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Reload the service when code changes."),
    ] = False,
) -> None:
    """Start the FastAPI service."""
    source_mode = source.strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise typer.BadParameter(f"source must be one of: {allowed}")

    os.environ["INSY_SOURCE_MODE"] = source_mode

    import uvicorn

    uvicorn.run(
        "insy_sensor_data.api.main:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


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
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build a processed sensor snapshot from raw Waites evidence."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    run_date = _parse_run_date(snapshot_date)
    try:
        summary = build_sensor_snapshot(settings=settings, run_date=run_date, source=source_mode)
    except (FileNotFoundError, NotImplementedError, ValueError) as exc:
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
    env_file: EnvFileOption = Path(".env"),
) -> None:
    """Build lightweight trend-ready outputs from processed snapshots."""
    source_mode = _validate_source(source)
    settings = AppSettings.from_env(env_file=env_file)
    try:
        summary = build_trends(
            settings=settings,
            start_date=_parse_run_date(start_date),
            end_date=_parse_run_date(end_date),
            source=source_mode,
        )
    except (FileNotFoundError, NotImplementedError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(summary, sort_keys=True))


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise typer.BadParameter(f"source must be one of: {allowed}")
    return source_mode


def _validate_raw_source(source: str) -> str:
    source_system = source.strip().lower()
    if source_system != "waites":
        raise typer.BadParameter("raw source must be: waites")
    return source_system


def _parse_run_date(raw_date: str) -> date:
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise typer.BadParameter("date must be in YYYY-MM-DD format") from exc


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)

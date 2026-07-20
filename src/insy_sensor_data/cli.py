from __future__ import annotations

from pathlib import Path
from typing import Annotated
import json
import os

import typer

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.health import build_health_report


app = typer.Typer(
    add_completion=False,
    help="Small command-line tools for the INSY sensor data service.",
)


EnvFileOption = Annotated[
    Path | None,
    typer.Option(
        "--env-file",
        help="Optional .env file to load before process environment values.",
    ),
]


@app.command()
def health(env_file: EnvFileOption = None) -> None:
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

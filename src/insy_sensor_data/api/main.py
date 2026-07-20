from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from insy_sensor_data import __version__
from insy_sensor_data.api.routes import health
from insy_sensor_data.config import AppSettings


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or AppSettings.from_env()
    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        summary="Service API for vibration monitoring data.",
    )
    app.state.settings = app_settings

    app.include_router(health.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app

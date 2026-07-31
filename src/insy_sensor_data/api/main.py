from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from insy_sensor_data import __version__
from insy_sensor_data.api.routes import artifacts, dates, health, maximo, snapshots, trends, waites
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.errors import (
    StoreCorruptError,
    StoreMigrationRequiredError,
    StoreNotFoundError,
    StoreUnavailableError,
)
from insy_sensor_data.store.connection import read_store, store_path


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or AppSettings.from_env()
    if store_path(app_settings).is_file():
        try:
            with read_store(app_settings):
                pass
        except StoreMigrationRequiredError:
            raise
        except (StoreCorruptError, StoreUnavailableError):
            pass
    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        summary="Service API for vibration monitoring data.",
    )
    app.state.settings = app_settings

    @app.exception_handler(StoreNotFoundError)
    async def store_not_found_handler(_request, exc: StoreNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(StoreMigrationRequiredError)
    async def store_migration_handler(
        _request,
        exc: StoreMigrationRequiredError,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(StoreCorruptError)
    async def store_corrupt_handler(_request, exc: StoreCorruptError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(StoreUnavailableError)
    async def store_unavailable_handler(
        _request,
        exc: StoreUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    app.include_router(health.router)
    app.include_router(dates.router)
    app.include_router(artifacts.router)
    app.include_router(maximo.router)
    app.include_router(waites.router)
    app.include_router(snapshots.router)
    app.include_router(trends.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app

from __future__ import annotations

from typing import Any

from insy_sensor_data import __version__
from insy_sensor_data.config import AppSettings


def build_health_report(settings: AppSettings) -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.app_env,
        "source_mode": settings.source_mode,
        "data_dir": str(settings.data_dir),
        "waites": {
            "base_url": settings.waites_base_url,
            "facility_id": settings.waites_facility_id,
            "token_configured": settings.waites_token_configured,
        },
        "maximo": {
            "dsn": settings.maximo_dsn,
            "schema": settings.maximo_schema,
            "query_timeout_seconds": settings.maximo_query_timeout_seconds,
        },
    }

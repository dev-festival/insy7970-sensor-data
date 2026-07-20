from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from insy_sensor_data.health import build_health_report


router = APIRouter(tags=["health"])


@router.get("/health")
def read_health(request: Request) -> dict[str, Any]:
    return build_health_report(request.app.state.settings)

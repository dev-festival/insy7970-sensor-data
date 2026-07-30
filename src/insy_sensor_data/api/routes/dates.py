from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from insy_sensor_data.store.context import operational_dates


router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/dates")
def read_dates(request: Request) -> dict[str, Any]:
    return operational_dates(request.app.state.settings)

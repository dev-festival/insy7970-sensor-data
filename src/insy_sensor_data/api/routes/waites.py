from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from insy_sensor_data.waites.fetch import list_raw_waites_runs


router = APIRouter(prefix="/api/waites", tags=["waites"])


@router.get("/raw-runs")
def read_raw_waites_runs(request: Request) -> dict[str, Any]:
    runs = list_raw_waites_runs(request.app.state.settings)
    return {"runs": runs, "count": len(runs)}

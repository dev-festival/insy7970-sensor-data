from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from insy_sensor_data.snapshots.build import list_snapshot_dates
from insy_sensor_data.snapshots.trends import list_trend_ranges
from insy_sensor_data.waites.fetch import list_raw_waites_runs


router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/dates")
def read_dates(request: Request) -> dict[str, Any]:
    raw_runs = list_raw_waites_runs(request.app.state.settings)
    return {
        "raw_waites": [run["date"] for run in raw_runs],
        "snapshots": list_snapshot_dates(request.app.state.settings),
        "trends": list_trend_ranges(request.app.state.settings),
    }

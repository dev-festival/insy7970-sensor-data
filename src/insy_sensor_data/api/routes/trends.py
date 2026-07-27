from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from insy_sensor_data.artifact_views import load_trend_view


router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("")
def read_trends(
    start_date: str,
    end_date: str,
    request: Request,
    source: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        payload = load_trend_view(
            settings=request.app.state.settings,
            start_date=start,
            end_date=end,
            source=source,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            customer_asset_id=customer_asset_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload

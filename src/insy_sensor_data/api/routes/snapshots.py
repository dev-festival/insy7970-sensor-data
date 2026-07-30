from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from insy_sensor_data.store.snapshots import load_snapshot_view


router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])


@router.get("/{snapshot_date}")
def read_snapshot(
    snapshot_date: str,
    request: Request,
    source: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    try:
        run_date = date.fromisoformat(snapshot_date)
        payload = load_snapshot_view(
            settings=request.app.state.settings,
            run_date=run_date,
            source=source,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            customer_asset_id=customer_asset_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload

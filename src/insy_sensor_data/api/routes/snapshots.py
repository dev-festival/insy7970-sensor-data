from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from insy_sensor_data.snapshots.build import load_snapshot


router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])


@router.get("/{snapshot_date}")
def read_snapshot(snapshot_date: str, request: Request) -> dict[str, Any]:
    try:
        run_date = date.fromisoformat(snapshot_date)
        payload = load_snapshot(request.app.state.settings, run_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="snapshot_date must be YYYY-MM-DD") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload

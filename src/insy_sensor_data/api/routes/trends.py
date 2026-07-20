from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from insy_sensor_data.snapshots.trends import load_trends


router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("")
def read_trends(start_date: str, end_date: str, request: Request) -> dict[str, Any]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        payload = load_trends(request.app.state.settings, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="start_date and end_date must be YYYY-MM-DD") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload

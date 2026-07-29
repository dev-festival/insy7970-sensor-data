from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from insy_sensor_data.maximo.db import MaximoDatabaseError
from insy_sensor_data.maximo.history import load_asset_history


router = APIRouter(prefix="/api/maximo", tags=["maximo"])


@router.get("/asset-history")
def read_asset_history(
    request: Request,
    assetnum: str,
    start_date: str,
    end_date: str,
    source: str | None = None,
) -> dict[str, Any]:
    try:
        return load_asset_history(
            settings=request.app.state.settings,
            assetnums=[assetnum],
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            source=source or request.app.state.settings.source_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MaximoDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

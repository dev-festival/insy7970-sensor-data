from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from insy_sensor_data.artifact_views import (
    discover_artifacts,
    list_equipment_tree_view,
    list_equipment_view,
    load_cluster_view,
    load_cluster_window_view,
    load_drift_view,
)


router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/artifacts")
def read_artifacts(request: Request) -> dict[str, Any]:
    return discover_artifacts(request.app.state.settings)


@router.get("/equipment")
def read_equipment(
    request: Request,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    try:
        return list_equipment_view(
            request.app.state.settings,
            source=source,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/equipment-tree")
def read_equipment_tree(
    request: Request,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    try:
        return list_equipment_tree_view(
            request.app.state.settings,
            source=source,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/clusters")
def read_clusters(
    request: Request,
    cluster_date: str = Query(..., alias="date"),
    source: str | None = None,
    dimension: str = "x",
    k: int = 4,
) -> dict[str, Any]:
    try:
        return load_cluster_view(
            settings=request.app.state.settings,
            run_date=date.fromisoformat(cluster_date),
            source=source or request.app.state.settings.source_mode,
            dimension=dimension,
            k=k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/drift")
def read_drift(
    request: Request,
    from_date: str,
    to_date: str,
    source: str | None = None,
    dimension: str = "x",
    k: int = 4,
) -> dict[str, Any]:
    try:
        return load_drift_view(
            settings=request.app.state.settings,
            from_date=date.fromisoformat(from_date),
            to_date=date.fromisoformat(to_date),
            source=source or request.app.state.settings.source_mode,
            dimension=dimension,
            k=k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/cluster-windows")
def read_cluster_windows(
    request: Request,
    start_date: str,
    end_date: str,
    source: str | None = None,
    dimension: str = "x",
    k: int = 4,
) -> dict[str, Any]:
    try:
        return load_cluster_window_view(
            settings=request.app.state.settings,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            source=source or request.app.state.settings.source_mode,
            dimension=dimension,
            k=k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

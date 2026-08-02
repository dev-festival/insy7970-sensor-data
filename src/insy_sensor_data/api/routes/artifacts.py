from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from insy_sensor_data.services.exploration import (
    load_cluster_explorer,
    load_drift_overview,
)
from insy_sensor_data.services.review import load_snapshot_review
from insy_sensor_data.store.context import browser_context
from insy_sensor_data.store.models import (
    list_models,
    load_cluster,
    load_cluster_window,
    load_drift,
)
from insy_sensor_data.store.references import list_equipment, list_equipment_tree


router = APIRouter(prefix="/api", tags=["service"])


@router.get("/context")
def read_context(request: Request) -> dict[str, Any]:
    return browser_context(request.app.state.settings)


@router.get("/equipment")
def read_equipment(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    try:
        return list_equipment(
            request.app.state.settings,
            source=request.app.state.settings.source_mode,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/equipment-tree")
def read_equipment_tree(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    try:
        return list_equipment_tree(
            request.app.state.settings,
            source=request.app.state.settings.source_mode,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/snapshot-review/{snapshot_date}")
def read_snapshot_review(
    snapshot_date: str,
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    scope: str = "all",
    scope_type: str | None = None,
    scope_id: str | None = None,
    asset_tree_id: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
    metric: str = "rms_vel",
    dimension: str = "x",
    stat: str = "mean",
) -> dict[str, Any]:
    try:
        return load_snapshot_review(
            settings=request.app.state.settings,
            run_date=date.fromisoformat(snapshot_date),
            source=request.app.state.settings.source_mode,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            scope=scope_type or scope,
            scope_id=scope_id,
            asset_tree_id=asset_tree_id,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            sensor_id=sensor_id,
            metric=metric,
            dimension=dimension,
            stat=stat,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cluster-explorer")
def read_cluster_explorer(
    request: Request,
    cluster_date: str = Query(..., alias="date"),
    metric: str = "rms_vel",
    dimension: str = "x",
    scope_type: str = "all",
    scope_id: str | None = None,
) -> dict[str, Any]:
    try:
        return load_cluster_explorer(
            request.app.state.settings,
            run_date=date.fromisoformat(cluster_date),
            metric=metric,
            dimension=dimension,
            scope_type=scope_type,
            scope_id=scope_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/drift-overview")
def read_drift_overview(
    request: Request,
    start_date: str,
    end_date: str,
    scope_type: str = "all",
    scope_id: str | None = None,
) -> dict[str, Any]:
    try:
        return load_drift_overview(
            request.app.state.settings,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            scope_type=scope_type,
            scope_id=scope_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/clusters")
def read_clusters(
    request: Request,
    cluster_date: str = Query(..., alias="date"),
    dimension: str = "x",
    metric: str | None = None,
) -> dict[str, Any]:
    try:
        return load_cluster(
            settings=request.app.state.settings,
            run_date=date.fromisoformat(cluster_date),
            source=request.app.state.settings.source_mode,
            metric=metric,
            dimension=dimension,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/drift")
def read_drift(
    request: Request,
    from_date: str,
    to_date: str,
    dimension: str = "x",
    metric: str | None = None,
) -> dict[str, Any]:
    try:
        return load_drift(
            settings=request.app.state.settings,
            from_date=date.fromisoformat(from_date),
            to_date=date.fromisoformat(to_date),
            source=request.app.state.settings.source_mode,
            metric=metric,
            dimension=dimension,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cluster-windows")
def read_cluster_windows(
    request: Request,
    start_date: str,
    end_date: str,
    dimension: str = "x",
    metric: str | None = None,
) -> dict[str, Any]:
    try:
        return load_cluster_window(
            settings=request.app.state.settings,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            source=request.app.state.settings.source_mode,
            metric=metric,
            dimension=dimension,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cluster-models")
def read_cluster_models(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    try:
        return list_models(
            settings=request.app.state.settings,
            source=request.app.state.settings.source_mode,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

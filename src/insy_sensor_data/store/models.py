from __future__ import annotations

from datetime import date
from typing import Any

from insy_sensor_data.clustering.registry import (
    list_registered_cluster_models,
    load_registered_cluster_view,
    load_registered_cluster_window_view,
    load_registered_drift_view,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.errors import (
    StoreMigrationRequiredError,
    StoreNotFoundError,
)
from insy_sensor_data.store.revision import data_revision
from insy_sensor_data.store.schema import resolve_configured_source


def list_models(
    settings: AppSettings,
    *,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    resolved_source = resolve_configured_source(settings, source)
    with read_store(
        settings,
        required_tables=(
            "cluster_model_runs",
            "waites_ingestion_ledger",
        ),
    ) as connection:
        revision = data_revision(
            connection,
            resolved_source,
            start_date=start_date,
            end_date=end_date,
        )
    payload = list_registered_cluster_models(
        settings,
        source=resolved_source,
        start_date=start_date,
        end_date=end_date,
    )
    return {**payload, "data_revision": revision}


def load_cluster(
    settings: AppSettings,
    *,
    run_date: date,
    source: str,
    feature_space: str | None,
    k: int,
) -> dict[str, Any]:
    selected_k = _validate_k(k)
    selected_feature_space = _require_feature_space(feature_space)
    try:
        payload = load_registered_cluster_view(
            settings=settings,
            run_date=run_date,
            source=source,
            feature_space=selected_feature_space,
            k=selected_k,
        )
    except FileNotFoundError as exc:
        raise StoreNotFoundError(str(exc)) from exc
    return {
        **payload,
        "data_revision": _revision_for_dates(settings, source, run_date, run_date),
    }


def load_drift(
    settings: AppSettings,
    *,
    from_date: date,
    to_date: date,
    source: str,
    feature_space: str | None,
    k: int,
) -> dict[str, Any]:
    selected_k = _validate_k(k)
    selected_feature_space = _require_feature_space(feature_space)
    try:
        payload = load_registered_drift_view(
            settings=settings,
            from_date=from_date,
            to_date=to_date,
            source=source,
            feature_space=selected_feature_space,
            k=selected_k,
        )
    except FileNotFoundError as exc:
        raise StoreNotFoundError(str(exc)) from exc
    return {
        **payload,
        "data_revision": _revision_for_dates(settings, source, from_date, to_date),
    }


def load_cluster_window(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    source: str,
    feature_space: str | None,
    k: int,
) -> dict[str, Any]:
    selected_k = _validate_k(k)
    selected_feature_space = _require_feature_space(feature_space)
    try:
        payload = load_registered_cluster_window_view(
            settings=settings,
            start_date=start_date,
            end_date=end_date,
            source=source,
            feature_space=selected_feature_space,
            k=selected_k,
        )
    except FileNotFoundError as exc:
        raise StoreNotFoundError(str(exc)) from exc
    return {
        **payload,
        "data_revision": _revision_for_dates(settings, source, start_date, end_date),
    }


def _revision_for_dates(
    settings: AppSettings,
    source: str,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    with read_store(
        settings,
        required_tables=("waites_ingestion_ledger",),
    ) as connection:
        return data_revision(
            connection,
            source,
            start_date=start_date,
            end_date=end_date,
        )


def _require_feature_space(feature_space: str | None) -> str:
    if feature_space in (None, ""):
        raise StoreMigrationRequiredError(
            "Legacy file-backed cluster views are diagnostic-only. "
            "Select a registered feature_space."
        )
    return str(feature_space)


def _validate_k(k: int) -> int:
    if k < 1:
        raise ValueError("k must be at least 1")
    return k

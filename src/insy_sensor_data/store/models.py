from __future__ import annotations

from datetime import date
from typing import Any

from insy_sensor_data.clustering.policy import ACTIVE_MODEL_POLICY
from insy_sensor_data.clustering.registry import (
    list_registered_cluster_models,
    load_registered_cluster_view,
    load_registered_cluster_window_view,
    load_registered_drift_view,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.errors import (
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
    metric: str | None = None,
    dimension: str = "x",
    feature_space: str | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    resolved_source = resolve_configured_source(settings, source)
    selected_k = ACTIVE_MODEL_POLICY.validate_k(k)
    selected_feature_space = ACTIVE_MODEL_POLICY.feature_space_for(
        metric=metric,
        dimension=dimension,
        requested=feature_space,
    )
    try:
        payload = load_registered_cluster_view(
            settings=settings,
            run_date=run_date,
            source=resolved_source,
            feature_space=selected_feature_space.name,
            k=selected_k,
        )
    except FileNotFoundError as exc:
        raise StoreNotFoundError(str(exc)) from exc
    return {
        **payload,
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "data_revision": _revision_for_dates(
            settings,
            resolved_source,
            run_date,
            run_date,
        ),
    }


def load_drift(
    settings: AppSettings,
    *,
    from_date: date,
    to_date: date,
    source: str,
    metric: str | None = None,
    dimension: str = "x",
    feature_space: str | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    resolved_source = resolve_configured_source(settings, source)
    selected_k = ACTIVE_MODEL_POLICY.validate_k(k)
    selected_feature_space = ACTIVE_MODEL_POLICY.feature_space_for(
        metric=metric,
        dimension=dimension,
        requested=feature_space,
    )
    try:
        payload = load_registered_drift_view(
            settings=settings,
            from_date=from_date,
            to_date=to_date,
            source=resolved_source,
            feature_space=selected_feature_space.name,
            k=selected_k,
        )
    except FileNotFoundError as exc:
        raise StoreNotFoundError(str(exc)) from exc
    return {
        **payload,
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "data_revision": _revision_for_dates(
            settings,
            resolved_source,
            from_date,
            to_date,
        ),
    }


def load_cluster_window(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    source: str,
    metric: str | None = None,
    dimension: str = "x",
    feature_space: str | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    resolved_source = resolve_configured_source(settings, source)
    selected_k = ACTIVE_MODEL_POLICY.validate_k(k)
    selected_feature_space = ACTIVE_MODEL_POLICY.feature_space_for(
        metric=metric,
        dimension=dimension,
        requested=feature_space,
    )
    try:
        payload = load_registered_cluster_window_view(
            settings=settings,
            start_date=start_date,
            end_date=end_date,
            source=resolved_source,
            feature_space=selected_feature_space.name,
            k=selected_k,
        )
    except FileNotFoundError as exc:
        raise StoreNotFoundError(str(exc)) from exc
    return {
        **payload,
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "data_revision": _revision_for_dates(
            settings,
            resolved_source,
            start_date,
            end_date,
        ),
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

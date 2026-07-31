from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from insy_sensor_data.clustering.policy import ACTIVE_MODEL_POLICY
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.models import load_cluster, load_cluster_window
from insy_sensor_data.store.references import public_scope, resolve_scope


def load_cluster_explorer(
    settings: AppSettings,
    *,
    run_date: date,
    metric: str = "rms_vel",
    dimension: str = "x",
    scope_type: str = "all",
    scope_id: str | None = None,
) -> dict[str, Any]:
    """Load one server-scoped model view for the standalone Cluster surface."""
    scope = resolve_scope(
        settings,
        source=settings.source_mode,
        start_date=run_date,
        end_date=run_date,
        scope=scope_type,
        scope_id=scope_id,
    )
    installation_ids = (
        None
        if scope["type"] == "all"
        else set(scope.get("installation_point_ids", set()))
    )
    payload = load_cluster(
        settings,
        run_date=run_date,
        source=settings.source_mode,
        metric=metric,
        dimension=dimension,
        installation_point_ids=installation_ids,
    )
    return {
        **payload,
        "scope": public_scope(scope),
        "scope_applied": scope["type"] != "all",
    }


def load_drift_overview(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    scope_type: str = "all",
    scope_id: str | None = None,
) -> dict[str, Any]:
    """Compose every active feature space into one gap-aware Drift response."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    scope = resolve_scope(
        settings,
        source=settings.source_mode,
        start_date=start_date,
        end_date=end_date,
        scope=scope_type,
        scope_id=scope_id,
    )
    installation_ids = (
        None
        if scope["type"] == "all"
        else set(scope.get("installation_point_ids", set()))
    )
    spaces: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    data_revision: dict[str, Any] | None = None
    specs = list(ACTIVE_MODEL_POLICY.feature_spaces)
    with ThreadPoolExecutor(max_workers=len(specs)) as executor:
        payloads = list(
            executor.map(
                lambda spec: load_cluster_window(
                    settings,
                    start_date=start_date,
                    end_date=end_date,
                    source=settings.source_mode,
                    feature_space=spec.name,
                    installation_point_ids=installation_ids,
                ),
                specs,
            )
        )
    for spec, payload in zip(specs, payloads, strict=True):
        data_revision = data_revision or payload.get("data_revision")
        feature_pairs = [
            {
                **row,
                "feature_space": spec.name,
                "feature_space_label": spec.label,
                "dimension": spec.dimension,
            }
            for row in payload.get("aligned_drift_rows", [])
        ]
        feature_gaps = [
            {
                **row,
                "feature_space": spec.name,
                "feature_space_label": spec.label,
                "dimension": spec.dimension,
            }
            for row in payload.get("missing_pairs", [])
        ]
        pairs.extend(feature_pairs)
        gaps.extend(feature_gaps)
        spaces.append(
            {
                "name": spec.name,
                "label": spec.label,
                "dimension": spec.dimension,
                "status": payload.get("status"),
                "date_count": payload.get("metrics", {}).get("date_count", 0),
                "ready_date_count": payload.get("metrics", {}).get(
                    "ready_date_count", 0
                ),
                "pair_count": payload.get("metrics", {}).get("pair_count", 0),
                "complete_pair_count": len(feature_pairs),
                "missing_pair_count": len(feature_gaps),
            }
        )
    return {
        "source": settings.source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "status": "complete" if not gaps else "partial",
        "scope": public_scope(scope),
        "scope_applied": scope["type"] != "all",
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "feature_spaces": spaces,
        "pairs": pairs,
        "gaps": gaps,
        "summary": {
            "feature_space_count": len(spaces),
            "complete_pair_count": len(pairs),
            "missing_pair_count": len(gaps),
            "warning_count": sum(
                int(row.get("warning_count") or 0) for row in pairs
            ),
        },
        "data_revision": data_revision,
    }

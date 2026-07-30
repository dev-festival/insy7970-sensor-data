from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite, sqrt
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from insy_sensor_data.artifacts import read_csv_rows, write_csv_rows, write_json
from insy_sensor_data.clustering.features import IDENTIFIER_FIELDS
from insy_sensor_data.clustering.model import (
    CLUSTER_SCHEMA_VERSION,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_RANDOM_SEED,
    DEFAULT_TOLERANCE,
    PCA_FIELDS,
    SENSOR_CLUSTER_FIELDS,
    _cluster_counts,
    _cluster_metrics,
    _cluster_summary_fields,
    _cluster_summary_rows,
    _feature_columns,
    _feature_matrix_path,
    _float,
    _kmeans,
    _numeric_matrix,
    _pca_coordinates,
    _pca_rows,
    _sensor_cluster_rows,
    _standard_scale,
    _ensure_feature_matrix,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store, observation_db_path
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.store.connection import read_store


REGISTRY_SCHEMA_VERSION = 1
FEATURE_POLICY_VERSION = "feature_space_daily_stats_v1"
SCALER_POLICY = "standard_zscore_v1"
ALGORITHM = "deterministic_kmeans"
ALIGNMENT_POLICY = "nearest_scaled_centroid_v1"
DEFAULT_FEATURE_SPACES = ("x_accel", "y_vel", "z_vel", "temperature")
DEFAULT_REGISTRY_KS = (5,)
VALID_REGISTRY_SOURCES = {"mock", "api"}

MODEL_ARTIFACT_FIELDS = [
    *SENSOR_CLUSTER_FIELDS,
]

CENTROID_ALIGNMENT_FIELDS = [
    "from_date",
    "to_date",
    "from_cluster",
    "to_cluster",
    "centroid_distance",
    "from_sensor_count",
    "to_sensor_count",
    "mapping_confidence",
]

ALIGNED_SENSOR_DRIFT_FIELDS = [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
    "status",
    "from_cluster",
    "to_cluster",
    "aligned_to_cluster",
    "raw_label_changed",
    "aligned_changed",
    "from_distance_to_centroid",
    "to_distance_to_centroid",
    "distance_delta",
]


@dataclass(frozen=True)
class FeatureSpaceSpec:
    name: str
    label: str
    dimension: str
    prefix: str
    axis: str | None = None


FEATURE_SPACE_SPECS: dict[str, FeatureSpaceSpec] = {
    "x_accel": FeatureSpaceSpec(
        name="x_accel",
        label="X Acceleration",
        dimension="x",
        prefix="rms_accel_",
        axis="x",
    ),
    "y_vel": FeatureSpaceSpec(
        name="y_vel",
        label="Y Velocity",
        dimension="y",
        prefix="rms_vel_",
        axis="y",
    ),
    "z_vel": FeatureSpaceSpec(
        name="z_vel",
        label="Z Velocity",
        dimension="z",
        prefix="rms_vel_",
        axis="z",
    ),
    "temperature": FeatureSpaceSpec(
        name="temperature",
        label="Temperature",
        dimension="temperature",
        prefix="temp_sensor_",
    ),
}


def build_cluster_model_grid(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str = "mock",
    feature_spaces: Iterable[str] | None = None,
    ks: Iterable[int] | None = None,
    random_seed: int = DEFAULT_RANDOM_SEED,
    force: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    selected_feature_spaces = _validate_feature_spaces(feature_spaces or DEFAULT_FEATURE_SPACES)
    selected_ks = _validate_ks(ks or DEFAULT_REGISTRY_KS)
    dates = _date_range(start_date, end_date)

    models: list[dict[str, Any]] = []
    for run_date in dates:
        for feature_space in selected_feature_spaces:
            for k in selected_ks:
                try:
                    models.append(
                        build_cluster_model_run(
                            settings=settings,
                            run_date=run_date,
                            source=source_mode,
                            feature_space=feature_space,
                            k=k,
                            random_seed=random_seed,
                            force=force,
                        )
                    )
                except (FileNotFoundError, ValueError) as exc:
                    models.append(
                        _record_failed_model_run(
                            settings=settings,
                            run_date=run_date,
                            source=source_mode,
                            feature_space=feature_space,
                            k=k,
                            random_seed=random_seed,
                            error=str(exc),
                        )
                    )

    drift_runs: list[dict[str, Any]] = []
    complete_models = {
        (
            model["date"],
            model["feature_space"],
            int(model["k"]),
        ): model
        for model in models
        if model.get("status") == "complete"
    }
    for from_date, to_date in zip(dates, dates[1:], strict=False):
        for feature_space in selected_feature_spaces:
            for k in selected_ks:
                if (
                    (from_date.isoformat(), feature_space, k) not in complete_models
                    or (to_date.isoformat(), feature_space, k) not in complete_models
                ):
                    drift_runs.append(
                        {
                            "source": source_mode,
                            "from_date": from_date.isoformat(),
                            "to_date": to_date.isoformat(),
                            "feature_space": feature_space,
                            "k": k,
                            "status": "skipped",
                            "action": "skipped",
                            "reason": "missing_complete_model",
                        }
                    )
                    continue
                try:
                    drift_runs.append(
                        build_registered_cluster_drift(
                            settings=settings,
                            from_date=from_date,
                            to_date=to_date,
                            source=source_mode,
                            feature_space=feature_space,
                            k=k,
                            random_seed=random_seed,
                            force=force,
                        )
                    )
                except (FileNotFoundError, ValueError) as exc:
                    drift_runs.append(
                        {
                            "source": source_mode,
                            "from_date": from_date.isoformat(),
                            "to_date": to_date.isoformat(),
                            "feature_space": feature_space,
                            "k": k,
                            "status": "failed",
                            "action": "failed",
                            "error": str(exc),
                        }
                    )

    model_actions = _action_counts(models)
    drift_actions = _action_counts(drift_runs)
    return {
        "source": source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "feature_spaces": selected_feature_spaces,
        "ks": selected_ks,
        "random_seed": random_seed,
        "feature_policy_version": FEATURE_POLICY_VERSION,
        "scaler_policy": SCALER_POLICY,
        "algorithm": ALGORITHM,
        "database_path": observation_db_path(settings).as_posix(),
        "date_count": len(dates),
        "model_count": len(models),
        "models_built": model_actions.get("built", 0),
        "models_reused": model_actions.get("reused", 0),
        "models_failed": model_actions.get("failed", 0),
        "drift_count": len(drift_runs),
        "drift_built": drift_actions.get("built", 0),
        "drift_reused": drift_actions.get("reused", 0),
        "drift_failed": drift_actions.get("failed", 0),
        "drift_skipped": drift_actions.get("skipped", 0),
        "models": models,
        "drift_runs": drift_runs,
    }


def build_cluster_model_run(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
    feature_space: str = "x_accel",
    k: int = 5,
    random_seed: int = DEFAULT_RANDOM_SEED,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    force: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    if k < 1:
        raise ValueError("k must be at least 1")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    model_run_id = cluster_model_run_id(
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    if not force:
        existing = _find_model_run(settings, model_run_id=model_run_id, status="complete")
        if existing is not None:
            return _model_summary_from_run(existing, action="reused")

    computed = _compute_feature_space_model(
        settings=settings,
        run_date=run_date,
        source=source_mode,
        spec=spec,
        k=k,
        random_seed=random_seed,
        max_iterations=max_iterations,
    )
    artifact_dir = _write_model_artifacts(settings, computed)
    completed_at = _utc_now()
    metrics = {
        **computed["metrics"],
        "outputs": {
            "sensor_clusters": (artifact_dir / "sensor_clusters.csv").as_posix(),
            "cluster_summary": (artifact_dir / "cluster_summary.csv").as_posix(),
            "pca_coordinates": (artifact_dir / "pca_coordinates.csv").as_posix(),
            "metrics": (artifact_dir / "metrics.json").as_posix(),
        },
    }
    write_json(artifact_dir / "metrics.json", metrics)
    computed = {
        **computed,
        "artifact_dir": artifact_dir.as_posix(),
        "metrics": metrics,
        "completed_at": completed_at,
    }
    _persist_complete_model_run(settings, computed)
    return _model_summary_from_computed(computed, action="built")


def build_registered_cluster_drift(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str = "mock",
    feature_space: str = "x_accel",
    k: int = 5,
    random_seed: int = DEFAULT_RANDOM_SEED,
    force: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    if to_date < from_date:
        raise ValueError("to_date must be on or after from_date")
    if k < 1:
        raise ValueError("k must be at least 1")
    from_model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=from_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    to_model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=to_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    drift_run_id = cluster_drift_run_id(from_model["model_run_id"], to_model["model_run_id"])
    if not force:
        existing = _find_drift_run(settings, drift_run_id=drift_run_id, status="complete")
        if existing is not None:
            return _drift_summary_from_run(existing, action="reused")

    from_rows = _model_assignment_rows(settings, from_model["model_run_id"])
    to_rows = _model_assignment_rows(settings, to_model["model_run_id"])
    from_centroids = _model_centroid_summary_rows(settings, from_model["model_run_id"])
    to_centroids = _model_centroid_summary_rows(settings, to_model["model_run_id"])
    alignment_rows = _centroid_alignment_rows(
        from_summary=from_centroids,
        to_summary=to_centroids,
        from_date=from_date,
        to_date=to_date,
    )
    alignment = {str(row["from_cluster"]): str(row["to_cluster"]) for row in alignment_rows}
    drift_rows = _aligned_sensor_drift_rows(from_rows, to_rows, alignment)
    matched_rows = [row for row in drift_rows if row["status"] == "matched"]
    raw_changed_count = sum(1 for row in matched_rows if row["raw_label_changed"] == "true")
    aligned_changed_count = sum(1 for row in matched_rows if row["aligned_changed"] == "true")
    matched_count = len(matched_rows)
    warnings = _aligned_drift_warnings(alignment_rows, raw_changed_count, aligned_changed_count)
    interpretation = _drift_interpretation(
        matched_count=matched_count,
        raw_changed_count=raw_changed_count,
        aligned_changed_count=aligned_changed_count,
        warnings=warnings,
    )
    artifact_dir = _write_drift_artifacts(
        settings=settings,
        from_date=from_date,
        to_date=to_date,
        source=source_mode,
        feature_space=spec.name,
        k=k,
        drift_rows=drift_rows,
        alignment_rows=alignment_rows,
    )
    metrics = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "alignment_policy": ALIGNMENT_POLICY,
        "from_model_run_id": from_model["model_run_id"],
        "to_model_run_id": to_model["model_run_id"],
        "matched_sensor_count": matched_count,
        "raw_label_changed_count": raw_changed_count,
        "aligned_changed_count": aligned_changed_count,
        "raw_label_changed_ratio": raw_changed_count / matched_count if matched_count else None,
        "aligned_changed_ratio": aligned_changed_count / matched_count if matched_count else None,
        "warning_count": len(warnings),
        "warnings": warnings,
        "interpretation": interpretation,
        "artifact_dir": artifact_dir.as_posix(),
        "outputs": {
            "aligned_cluster_drift": (artifact_dir / "aligned_cluster_drift.csv").as_posix(),
            "centroid_alignment": (artifact_dir / "centroid_alignment.csv").as_posix(),
            "aligned_metrics": (artifact_dir / "aligned_metrics.json").as_posix(),
        },
        "built_at": _utc_now(),
    }
    write_json(artifact_dir / "aligned_metrics.json", metrics)
    _persist_complete_drift_run(
        settings=settings,
        drift_run_id=drift_run_id,
        from_model=from_model,
        to_model=to_model,
        metrics=metrics,
        warnings=warnings,
        drift_rows=drift_rows,
        alignment_rows=alignment_rows,
    )
    return _drift_summary_from_metrics(drift_run_id, metrics, action="built")


def list_registered_cluster_models(
    settings: AppSettings,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source) if source else None
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    clauses: list[str] = []
    params: list[Any] = []
    if source_mode is not None:
        clauses.append("source = ?")
        params.append(source_mode)
    if start_date is not None:
        clauses.append("source_date >= ?")
        params.append(start_date.isoformat())
    if end_date is not None:
        clauses.append("source_date <= ?")
        params.append(end_date.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_store(settings, required_tables=("cluster_model_runs",)) as connection:
        rows = _query_dicts(
            connection,
            f"""
            SELECT
                model_run_id,
                source,
                source_date,
                feature_space,
                k,
                algorithm,
                random_seed,
                feature_policy_version,
                feature_columns_json,
                scaler_policy,
                input_snapshot_hash,
                input_snapshot_row_count,
                feature_row_count,
                feature_count,
                status,
                created_at,
                completed_at,
                artifact_dir,
                metrics_json,
                warnings_json
            FROM cluster_model_runs
            {where}
            ORDER BY source_date, feature_space, k, created_at
            """,
            params,
        )
    models = [_model_summary_from_run(row, action=row.get("status", "")) for row in rows]
    complete = [model for model in models if model.get("status") == "complete"]
    return {
        "source": source_mode,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "feature_spaces": sorted({str(model["feature_space"]) for model in complete}),
        "ks": sorted({int(model["k"]) for model in complete}),
        "models": models,
        "count": len(models),
        "complete_count": len(complete),
    }


def load_registered_cluster_view(
    settings: AppSettings,
    run_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    rows = _model_assignment_rows(settings, model["model_run_id"])
    pca_rows = [
        {
            **_identifier_projection(row),
            "cluster": row["cluster"],
            "pc1": row.get("pc1"),
            "pc2": row.get("pc2"),
            "distance_to_centroid": row.get("distance_to_centroid"),
        }
        for row in rows
    ]
    cluster_rows = _model_centroid_summary_rows(settings, model["model_run_id"])
    metrics = _json_loads(model.get("metrics_json"), {})
    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "source_date": run_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": int(model["k"]),
        "model_run_id": model["model_run_id"],
        "artifact_dir": model.get("artifact_dir") or "sqlite",
        "registered": True,
        "metrics": metrics,
        "row_count": len(rows),
        "cluster_row_count": len(cluster_rows),
        "pca_row_count": len(pca_rows),
        "rows": rows,
        "cluster_rows": cluster_rows,
        "pca_rows": pca_rows,
    }


def load_registered_drift_view(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    from_model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=from_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    to_model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=to_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    drift_id = cluster_drift_run_id(from_model["model_run_id"], to_model["model_run_id"])
    drift = _find_drift_run(settings, drift_run_id=drift_id, status="complete")
    if drift is None:
        raise FileNotFoundError(
            f"Missing registered drift run for source {source_mode} "
            f"{from_date.isoformat()} to {to_date.isoformat()} feature_space={spec.name} k={k}."
        )
    metrics = _json_loads(drift.get("metrics_json"), {})
    rows = _drift_assignment_rows(settings, drift_id)
    alignment_rows = _centroid_alignment_for_drift(settings, drift_id)
    return {
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "drift_run_id": drift_id,
        "artifact_dir": metrics.get("artifact_dir") or "sqlite",
        "registered": True,
        "metrics": metrics,
        "aligned_metrics": metrics,
        "raw_rows": [
            {**row, "changed": row["raw_label_changed"]}
            for row in rows
        ],
        "centroid_rows": alignment_rows,
        "aligned_rows": rows,
        "alignment_rows": alignment_rows,
    }


def load_registered_cluster_window_view(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    dates = _date_range(start_date, end_date)
    models = [
        _require_model_run(
            settings=settings,
            source=source_mode,
            source_date=run_date.isoformat(),
            feature_space=spec.name,
            k=k,
            random_seed=random_seed,
        )
        for run_date in dates
    ]
    drift_views = [
        load_registered_drift_view(
            settings=settings,
            from_date=from_date,
            to_date=to_date,
            source=source_mode,
            feature_space=spec.name,
            k=k,
            random_seed=random_seed,
        )
        for from_date, to_date in zip(dates, dates[1:], strict=False)
    ]
    quality_rows = [_quality_row_from_model(model) for model in models]
    aligned_rows = [_window_drift_row(view) for view in drift_views]
    alignment_rows = [
        row
        for view in drift_views
        for row in view.get("alignment_rows", [])
    ]
    warning_count = sum(int(row.get("warning_count") or 0) for row in aligned_rows)
    metrics = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "source": source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "date_count": len(dates),
        "pair_count": len(drift_views),
        "warning_count": warning_count,
        "model_run_ids": [model["model_run_id"] for model in models],
        "drift_run_ids": [view["drift_run_id"] for view in drift_views],
    }
    return {
        "source": source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "artifact_dir": "sqlite",
        "registered": True,
        "metrics": metrics,
        "window_rows": quality_rows,
        "quality_rows": quality_rows,
        "aligned_drift_rows": aligned_rows,
        "alignment_rows": alignment_rows,
    }


def cluster_model_run_id(
    source: str,
    source_date: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> str:
    return (
        f"{source}:{source_date}:{feature_space}:k{k}:"
        f"{ALGORITHM}:seed{random_seed}:{FEATURE_POLICY_VERSION}"
    )


def cluster_drift_run_id(from_model_run_id: str, to_model_run_id: str) -> str:
    return f"{from_model_run_id}->{to_model_run_id}:{ALIGNMENT_POLICY}"


def feature_space_dimension(feature_space: str) -> str:
    return _validate_feature_space(feature_space).dimension


def _compute_feature_space_model(
    settings: AppSettings,
    run_date: date,
    source: str,
    spec: FeatureSpaceSpec,
    k: int,
    random_seed: int,
    max_iterations: int,
) -> dict[str, Any]:
    feature_summary = _ensure_feature_matrix(
        settings=settings,
        run_date=run_date,
        source=source,
        dimension=spec.dimension,
    )
    feature_matrix_path = _feature_matrix_path(settings, run_date, source, spec.dimension)
    feature_rows_all = read_csv_rows(feature_matrix_path)
    all_feature_columns = _feature_columns(feature_rows_all)
    feature_columns = _feature_space_columns(all_feature_columns, spec)
    if not feature_rows_all:
        raise ValueError("feature matrix has no rows")
    if not feature_columns:
        raise ValueError(f"feature space {spec.name} has no matching feature columns")
    if k > len(feature_rows_all):
        raise ValueError("k cannot exceed feature row count")

    feature_rows = [
        {
            **{field: row.get(field, "") for field in IDENTIFIER_FIELDS},
            **{field: row.get(field, "") for field in feature_columns},
        }
        for row in feature_rows_all
    ]
    matrix = _numeric_matrix(feature_rows, feature_columns)
    scaled = _standard_scale(matrix, feature_columns)
    kmeans = _kmeans(
        scaled.values,
        k=k,
        random_seed=random_seed,
        max_iterations=max_iterations,
    )
    pca = _pca_coordinates(scaled.values)
    metrics = _cluster_metrics(scaled.values, kmeans)
    cluster_counts = _cluster_counts(kmeans.labels, k)
    sensor_rows = _sensor_cluster_rows(feature_rows, feature_columns, kmeans)
    summary_rows = _cluster_summary_rows(
        feature_rows=feature_rows,
        feature_columns=feature_columns,
        scaled=scaled,
        kmeans=kmeans,
        k=k,
    )
    pca_rows = _pca_rows(feature_rows, kmeans, pca)
    snapshot_path = get_storage_paths(settings.data_dir).snapshot_dir(run_date.isoformat()) / "sensor_snapshot.csv"
    built_at = _utc_now()
    model_run_id = cluster_model_run_id(
        source=source,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    warnings = _model_warnings(row_count=len(feature_rows), feature_count=len(feature_columns), k=k, metrics=metrics)
    return {
        "model_run_id": model_run_id,
        "source": source,
        "date": run_date.isoformat(),
        "source_date": run_date.isoformat(),
        "feature_space": spec.name,
        "feature_space_label": spec.label,
        "dimension": spec.dimension,
        "k": k,
        "algorithm": ALGORITHM,
        "random_seed": random_seed,
        "feature_policy_version": FEATURE_POLICY_VERSION,
        "scaler_policy": SCALER_POLICY,
        "feature_columns": feature_columns,
        "input_snapshot_hash": _sha256(snapshot_path) if snapshot_path.exists() else None,
        "input_snapshot_row_count": len(feature_rows_all),
        "feature_row_count": len(feature_rows),
        "feature_count": len(feature_columns),
        "created_at": built_at,
        "sensor_rows": sensor_rows,
        "cluster_rows": summary_rows,
        "pca_rows": pca_rows,
        "warnings": warnings,
        "metrics": {
            "schema_version": CLUSTER_SCHEMA_VERSION,
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "source": source,
            "date": run_date.isoformat(),
            "source_date": run_date.isoformat(),
            "feature_space": spec.name,
            "feature_space_label": spec.label,
            "dimension": spec.dimension,
            "k": k,
            "algorithm": ALGORITHM,
            "random_seed": random_seed,
            "feature_policy_version": FEATURE_POLICY_VERSION,
            "scaler_policy": SCALER_POLICY,
            "built_at": built_at,
            "feature_matrix_path": feature_matrix_path.as_posix(),
            "feature_summary_path": feature_summary.get("summary_path"),
            "feature_status": feature_summary.get("status"),
            "input_snapshot_hash": _sha256(snapshot_path) if snapshot_path.exists() else None,
            "input_snapshot_row_count": len(feature_rows_all),
            "row_count": len(feature_rows),
            "feature_row_count": len(feature_rows),
            "feature_count": len(feature_columns),
            "features": feature_columns,
            "cluster_counts": cluster_counts,
            "scaler": {
                "means": scaled.means,
                "scales": scaled.scales,
            },
            "kmeans": {
                "algorithm": ALGORITHM,
                "iterations": kmeans.iterations,
                "converged": kmeans.converged,
                "inertia": kmeans.inertia,
                "max_iterations": max_iterations,
                "tolerance": DEFAULT_TOLERANCE,
            },
            "metrics": metrics,
            "pca": {
                "available": pca["available"],
                "explained_variance_ratio": pca["explained_variance_ratio"],
            },
            "warning_count": len(warnings),
            "warnings": warnings,
        },
    }


def _write_model_artifacts(settings: AppSettings, computed: dict[str, Any]) -> Path:
    storage = get_storage_paths(settings.data_dir)
    artifact_dir = storage.cluster_models_dir / _model_dir_name(
        source_date=computed["source_date"],
        source=computed["source"],
        feature_space=computed["feature_space"],
        k=int(computed["k"]),
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    feature_columns = computed["feature_columns"]
    write_csv_rows(
        artifact_dir / "sensor_clusters.csv",
        computed["sensor_rows"],
        [*MODEL_ARTIFACT_FIELDS, *feature_columns],
    )
    write_csv_rows(
        artifact_dir / "cluster_summary.csv",
        computed["cluster_rows"],
        _cluster_summary_fields(feature_columns),
    )
    write_csv_rows(artifact_dir / "pca_coordinates.csv", computed["pca_rows"], PCA_FIELDS)
    write_json(artifact_dir / "metrics.json", computed["metrics"])
    return artifact_dir


def _write_drift_artifacts(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    feature_space: str,
    k: int,
    drift_rows: list[dict[str, Any]],
    alignment_rows: list[dict[str, Any]],
) -> Path:
    storage = get_storage_paths(settings.data_dir)
    artifact_dir = storage.cluster_model_drift_dir / _drift_dir_name(
        from_date=from_date,
        to_date=to_date,
        source=source,
        feature_space=feature_space,
        k=k,
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_csv_rows(artifact_dir / "aligned_cluster_drift.csv", drift_rows, ALIGNED_SENSOR_DRIFT_FIELDS)
    write_csv_rows(artifact_dir / "centroid_alignment.csv", alignment_rows, CENTROID_ALIGNMENT_FIELDS)
    return artifact_dir


def _persist_complete_model_run(settings: AppSettings, computed: dict[str, Any]) -> None:
    with connect_observation_store(settings) as connection:
        with connection:
            _delete_model_run(connection, computed["model_run_id"])
            connection.execute(
                """
                INSERT INTO cluster_model_runs (
                    model_run_id,
                    source,
                    source_date,
                    feature_space,
                    k,
                    algorithm,
                    random_seed,
                    feature_policy_version,
                    feature_columns_json,
                    scaler_policy,
                    input_snapshot_hash,
                    input_snapshot_row_count,
                    feature_row_count,
                    feature_count,
                    status,
                    created_at,
                    completed_at,
                    artifact_dir,
                    metrics_json,
                    warnings_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    computed["model_run_id"],
                    computed["source"],
                    computed["source_date"],
                    computed["feature_space"],
                    computed["k"],
                    computed["algorithm"],
                    computed["random_seed"],
                    computed["feature_policy_version"],
                    json.dumps(computed["feature_columns"], sort_keys=True),
                    computed["scaler_policy"],
                    computed.get("input_snapshot_hash"),
                    computed["input_snapshot_row_count"],
                    computed["feature_row_count"],
                    computed["feature_count"],
                    "complete",
                    computed["created_at"],
                    computed["completed_at"],
                    computed.get("artifact_dir"),
                    json.dumps(computed["metrics"], sort_keys=True),
                    json.dumps(computed["warnings"], sort_keys=True),
                ),
            )
            _insert_model_assignments(connection, computed)
            _insert_model_centroids(connection, computed)


def _record_failed_model_run(
    settings: AppSettings,
    run_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int,
    error: str,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    model_run_id = cluster_model_run_id(
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    now = _utc_now()
    warning = {"level": "error", "code": "model_build_failed", "message": error}
    with connect_observation_store(settings) as connection:
        with connection:
            _delete_model_run(connection, model_run_id)
            connection.execute(
                """
                INSERT INTO cluster_model_runs (
                    model_run_id,
                    source,
                    source_date,
                    feature_space,
                    k,
                    algorithm,
                    random_seed,
                    feature_policy_version,
                    feature_columns_json,
                    scaler_policy,
                    input_snapshot_hash,
                    input_snapshot_row_count,
                    feature_row_count,
                    feature_count,
                    status,
                    created_at,
                    completed_at,
                    artifact_dir,
                    metrics_json,
                    warnings_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_run_id,
                    source_mode,
                    run_date.isoformat(),
                    spec.name,
                    k,
                    ALGORITHM,
                    random_seed,
                    FEATURE_POLICY_VERSION,
                    "[]",
                    SCALER_POLICY,
                    None,
                    0,
                    0,
                    0,
                    "failed",
                    now,
                    now,
                    None,
                    json.dumps({"error": error}, sort_keys=True),
                    json.dumps([warning], sort_keys=True),
                ),
            )
    return {
        "model_run_id": model_run_id,
        "source": source_mode,
        "date": run_date.isoformat(),
        "source_date": run_date.isoformat(),
        "feature_space": spec.name,
        "k": k,
        "status": "failed",
        "action": "failed",
        "error": error,
    }


def _persist_complete_drift_run(
    settings: AppSettings,
    drift_run_id: str,
    from_model: dict[str, Any],
    to_model: dict[str, Any],
    metrics: dict[str, Any],
    warnings: list[dict[str, Any]],
    drift_rows: list[dict[str, Any]],
    alignment_rows: list[dict[str, Any]],
) -> None:
    with connect_observation_store(settings) as connection:
        with connection:
            connection.execute("DELETE FROM cluster_drift_assignments WHERE drift_run_id = ?", (drift_run_id,))
            connection.execute("DELETE FROM cluster_centroid_alignment WHERE drift_run_id = ?", (drift_run_id,))
            connection.execute("DELETE FROM cluster_drift_runs WHERE drift_run_id = ?", (drift_run_id,))
            connection.execute(
                """
                INSERT INTO cluster_drift_runs (
                    drift_run_id,
                    from_model_run_id,
                    to_model_run_id,
                    source,
                    from_date,
                    to_date,
                    feature_space,
                    k,
                    alignment_policy,
                    matched_sensor_count,
                    raw_changed_sensor_count,
                    aligned_changed_sensor_count,
                    status,
                    metrics_json,
                    warnings_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    drift_run_id,
                    from_model["model_run_id"],
                    to_model["model_run_id"],
                    metrics["source"],
                    metrics["from_date"],
                    metrics["to_date"],
                    metrics["feature_space"],
                    metrics["k"],
                    ALIGNMENT_POLICY,
                    metrics["matched_sensor_count"],
                    metrics["raw_label_changed_count"],
                    metrics["aligned_changed_count"],
                    "complete",
                    json.dumps(metrics, sort_keys=True),
                    json.dumps(warnings, sort_keys=True),
                    metrics["built_at"],
                ),
            )
            connection.executemany(
                """
                INSERT INTO cluster_drift_assignments (
                    drift_run_id,
                    installation_point_id,
                    from_cluster,
                    to_cluster,
                    aligned_to_cluster,
                    status,
                    raw_changed,
                    aligned_changed,
                    distance_delta
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        drift_run_id,
                        row["installation_point_id"],
                        _optional_int(row.get("from_cluster")),
                        _optional_int(row.get("to_cluster")),
                        _optional_int(row.get("aligned_to_cluster")),
                        row["status"],
                        1 if row["raw_label_changed"] == "true" else 0,
                        1 if row["aligned_changed"] == "true" else 0,
                        _optional_float(row.get("distance_delta")),
                    )
                    for row in drift_rows
                ],
            )
            connection.executemany(
                """
                INSERT INTO cluster_centroid_alignment (
                    drift_run_id,
                    from_cluster,
                    to_cluster,
                    distance,
                    mapping_confidence
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        drift_run_id,
                        _optional_int(row.get("from_cluster")),
                        _optional_int(row.get("to_cluster")),
                        _optional_float(row.get("centroid_distance")),
                        row.get("mapping_confidence"),
                    )
                    for row in alignment_rows
                ],
            )


def _insert_model_assignments(connection: sqlite3.Connection, computed: dict[str, Any]) -> None:
    pca_by_id = {str(row["installation_point_id"]): row for row in computed["pca_rows"]}
    feature_columns = computed["feature_columns"]
    rows = []
    for row in computed["sensor_rows"]:
        installation_id = str(row.get("installation_point_id") or "")
        pca = pca_by_id.get(installation_id, {})
        rows.append(
            (
                computed["model_run_id"],
                installation_id,
                _text(row.get("sensor_id")),
                _text(row.get("equipment_id")),
                _text(row.get("equipment_name")),
                _text(row.get("customer_asset_id")),
                _text(row.get("installation_point_name")),
                _optional_int(row.get("cluster")) or 0,
                _optional_float(row.get("distance_to_centroid")),
                _optional_float(pca.get("pc1")),
                _optional_float(pca.get("pc2")),
                json.dumps({field: _optional_float(row.get(field)) for field in feature_columns}, sort_keys=True),
            )
        )
    connection.executemany(
        """
        INSERT INTO cluster_model_assignments (
            model_run_id,
            installation_point_id,
            sensor_id,
            equipment_id,
            equipment_name,
            customer_asset_id,
            installation_point_name,
            cluster,
            distance_to_centroid,
            pca_x,
            pca_y,
            features_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_model_centroids(connection: sqlite3.Connection, computed: dict[str, Any]) -> None:
    pca_by_cluster = _cluster_pca_centers(computed["pca_rows"])
    feature_columns = computed["feature_columns"]
    rows = []
    for row in computed["cluster_rows"]:
        cluster = _optional_int(row.get("cluster")) or 0
        rows.append(
            (
                computed["model_run_id"],
                cluster,
                _optional_int(row.get("sensor_count")) or 0,
                json.dumps(
                    {
                        field: _optional_float(row.get(f"centroid_scaled_{field}"))
                        for field in feature_columns
                    },
                    sort_keys=True,
                ),
                pca_by_cluster.get(cluster, {}).get("pca_x"),
                pca_by_cluster.get(cluster, {}).get("pca_y"),
                json.dumps(row, sort_keys=True),
            )
        )
    connection.executemany(
        """
        INSERT INTO cluster_model_centroids (
            model_run_id,
            cluster,
            sensor_count,
            centroid_json,
            pca_x,
            pca_y,
            summary_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _delete_model_run(connection: sqlite3.Connection, model_run_id: str) -> None:
    connection.execute("DELETE FROM cluster_drift_assignments WHERE drift_run_id IN (SELECT drift_run_id FROM cluster_drift_runs WHERE from_model_run_id = ? OR to_model_run_id = ?)", (model_run_id, model_run_id))
    connection.execute("DELETE FROM cluster_centroid_alignment WHERE drift_run_id IN (SELECT drift_run_id FROM cluster_drift_runs WHERE from_model_run_id = ? OR to_model_run_id = ?)", (model_run_id, model_run_id))
    connection.execute("DELETE FROM cluster_drift_runs WHERE from_model_run_id = ? OR to_model_run_id = ?", (model_run_id, model_run_id))
    connection.execute("DELETE FROM cluster_model_assignments WHERE model_run_id = ?", (model_run_id,))
    connection.execute("DELETE FROM cluster_model_centroids WHERE model_run_id = ?", (model_run_id,))
    connection.execute("DELETE FROM cluster_model_runs WHERE model_run_id = ?", (model_run_id,))


def _find_model_run(
    settings: AppSettings,
    model_run_id: str,
    status: str | None = None,
) -> dict[str, Any] | None:
    clauses = ["model_run_id = ?"]
    params: list[Any] = [model_run_id]
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    with read_store(settings, required_tables=("cluster_model_runs",)) as connection:
        row = connection.execute(
            f"""
            SELECT
                model_run_id,
                source,
                source_date,
                feature_space,
                k,
                algorithm,
                random_seed,
                feature_policy_version,
                feature_columns_json,
                scaler_policy,
                input_snapshot_hash,
                input_snapshot_row_count,
                feature_row_count,
                feature_count,
                status,
                created_at,
                completed_at,
                artifact_dir,
                metrics_json,
                warnings_json
            FROM cluster_model_runs
            WHERE {" AND ".join(clauses)}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row is not None else None


def _find_drift_run(
    settings: AppSettings,
    drift_run_id: str,
    status: str | None = None,
) -> dict[str, Any] | None:
    clauses = ["drift_run_id = ?"]
    params: list[Any] = [drift_run_id]
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    with read_store(settings, required_tables=("cluster_drift_runs",)) as connection:
        row = connection.execute(
            f"""
            SELECT
                drift_run_id,
                from_model_run_id,
                to_model_run_id,
                source,
                from_date,
                to_date,
                feature_space,
                k,
                alignment_policy,
                matched_sensor_count,
                raw_changed_sensor_count,
                aligned_changed_sensor_count,
                status,
                metrics_json,
                warnings_json,
                created_at
            FROM cluster_drift_runs
            WHERE {" AND ".join(clauses)}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row is not None else None


def _require_model_run(
    settings: AppSettings,
    source: str,
    source_date: str,
    feature_space: str,
    k: int,
    random_seed: int,
) -> dict[str, Any]:
    model_run_id = cluster_model_run_id(
        source=source,
        source_date=source_date,
        feature_space=feature_space,
        k=k,
        random_seed=random_seed,
    )
    model = _find_model_run(settings, model_run_id=model_run_id, status="complete")
    if model is None:
        raise FileNotFoundError(
            f"Missing registered cluster model for source {source} date {source_date} "
            f"feature_space={feature_space} k={k}."
        )
    return model


def _model_assignment_rows(settings: AppSettings, model_run_id: str) -> list[dict[str, Any]]:
    with read_store(
        settings,
        required_tables=("cluster_model_assignments",),
    ) as connection:
        rows = _query_dicts(
            connection,
            """
            SELECT
                model_run_id,
                installation_point_id,
                sensor_id,
                equipment_id,
                equipment_name,
                customer_asset_id,
                installation_point_name,
                cluster,
                distance_to_centroid,
                pca_x,
                pca_y,
                features_json
            FROM cluster_model_assignments
            WHERE model_run_id = ?
            ORDER BY CAST(installation_point_id AS INTEGER), installation_point_id
            """,
            (model_run_id,),
        )
    output = []
    for row in rows:
        features = _json_loads(row.get("features_json"), {})
        output.append(
            {
                "installation_point_id": row.get("installation_point_id"),
                "installation_point_name": row.get("installation_point_name"),
                "equipment_id": row.get("equipment_id"),
                "equipment_name": row.get("equipment_name"),
                "sensor_id": row.get("sensor_id"),
                "customer_asset_id": row.get("customer_asset_id"),
                "cluster": row.get("cluster"),
                "distance_to_centroid": row.get("distance_to_centroid"),
                "pc1": row.get("pca_x"),
                "pc2": row.get("pca_y"),
                **features,
            }
        )
    return output


def _model_centroid_summary_rows(settings: AppSettings, model_run_id: str) -> list[dict[str, Any]]:
    with read_store(
        settings,
        required_tables=("cluster_model_centroids",),
    ) as connection:
        rows = _query_dicts(
            connection,
            """
            SELECT
                model_run_id,
                cluster,
                sensor_count,
                centroid_json,
                pca_x,
                pca_y,
                summary_json
            FROM cluster_model_centroids
            WHERE model_run_id = ?
            ORDER BY cluster
            """,
            (model_run_id,),
        )
    output = []
    for row in rows:
        summary = _json_loads(row.get("summary_json"), {})
        summary.setdefault("cluster", row.get("cluster"))
        summary.setdefault("sensor_count", row.get("sensor_count"))
        summary.setdefault("pca_x", row.get("pca_x"))
        summary.setdefault("pca_y", row.get("pca_y"))
        output.append(summary)
    return output


def _drift_assignment_rows(settings: AppSettings, drift_run_id: str) -> list[dict[str, Any]]:
    with read_store(
        settings,
        required_tables=(
            "cluster_drift_assignments",
            "cluster_drift_runs",
            "cluster_model_assignments",
        ),
    ) as connection:
        rows = _query_dicts(
            connection,
            """
            SELECT
                assignment.drift_run_id,
                assignment.installation_point_id,
                assignment.from_cluster,
                assignment.to_cluster,
                assignment.aligned_to_cluster,
                assignment.status,
                assignment.raw_changed,
                assignment.aligned_changed,
                assignment.distance_delta,
                model_rows.equipment_id,
                model_rows.equipment_name,
                model_rows.sensor_id,
                model_rows.customer_asset_id
            FROM cluster_drift_assignments AS assignment
            LEFT JOIN cluster_drift_runs AS drift
              ON drift.drift_run_id = assignment.drift_run_id
            LEFT JOIN cluster_model_assignments AS model_rows
              ON model_rows.model_run_id = drift.to_model_run_id
             AND model_rows.installation_point_id = assignment.installation_point_id
            WHERE assignment.drift_run_id = ?
            ORDER BY CAST(assignment.installation_point_id AS INTEGER), assignment.installation_point_id
            """,
            (drift_run_id,),
        )
    return [
        {
            "installation_point_id": row.get("installation_point_id"),
            "equipment_id": row.get("equipment_id"),
            "equipment_name": row.get("equipment_name"),
            "sensor_id": row.get("sensor_id"),
            "customer_asset_id": row.get("customer_asset_id"),
            "status": row.get("status"),
            "from_cluster": _text(row.get("from_cluster")),
            "to_cluster": _text(row.get("to_cluster")),
            "aligned_to_cluster": _text(row.get("aligned_to_cluster")),
            "raw_label_changed": "true" if row.get("raw_changed") else "false",
            "aligned_changed": "true" if row.get("aligned_changed") else "false",
            "distance_delta": row.get("distance_delta"),
        }
        for row in rows
    ]


def _centroid_alignment_for_drift(settings: AppSettings, drift_run_id: str) -> list[dict[str, Any]]:
    with read_store(
        settings,
        required_tables=("cluster_centroid_alignment",),
    ) as connection:
        rows = _query_dicts(
            connection,
            """
            SELECT
                drift_run_id,
                from_cluster,
                to_cluster,
                distance,
                mapping_confidence
            FROM cluster_centroid_alignment
            WHERE drift_run_id = ?
            ORDER BY from_cluster
            """,
            (drift_run_id,),
        )
    return [
        {
            "from_cluster": _text(row.get("from_cluster")),
            "to_cluster": _text(row.get("to_cluster")),
            "centroid_distance": row.get("distance"),
            "mapping_confidence": row.get("mapping_confidence"),
        }
        for row in rows
    ]


def _feature_space_columns(columns: list[str], spec: FeatureSpaceSpec) -> list[str]:
    if spec.axis is None:
        return [column for column in columns if column.startswith(spec.prefix)]
    suffix = f"_{spec.axis}"
    return [
        column
        for column in columns
        if column.startswith(spec.prefix) and column.endswith(suffix)
    ]


def _model_warnings(row_count: int, feature_count: int, k: int, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if row_count < max(k * 5, 1):
        warnings.append(
            {
                "level": "warning",
                "code": "small_sample_contract_only",
                "message": "This run is useful for contract testing, not cluster-quality judgment.",
            }
        )
    if feature_count < 2:
        warnings.append(
            {
                "level": "warning",
                "code": "low_feature_count",
                "message": "The feature space has too few features for strong clustering interpretation.",
            }
        )
    silhouette = (metrics.get("silhouette_score") or {}).get("value")
    if silhouette is None:
        warnings.append(
            {
                "level": "warning",
                "code": "silhouette_unavailable",
                "message": "Silhouette score is unavailable for this row count and k.",
            }
        )
    elif float(silhouette) < 0.2:
        warnings.append(
            {
                "level": "warning",
                "code": "low_silhouette",
                "value": silhouette,
                "message": "Cluster separation is weak for this run.",
            }
        )
    return warnings


def _cluster_pca_centers(pca_rows: list[dict[str, Any]]) -> dict[int, dict[str, float | None]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in pca_rows:
        cluster = _optional_int(row.get("cluster"))
        if cluster is None:
            continue
        grouped.setdefault(cluster, []).append(row)
    output = {}
    for cluster, rows in grouped.items():
        pc1 = [_optional_float(row.get("pc1")) for row in rows]
        pc2 = [_optional_float(row.get("pc2")) for row in rows]
        pc1_values = [value for value in pc1 if value is not None]
        pc2_values = [value for value in pc2 if value is not None]
        output[cluster] = {
            "pca_x": sum(pc1_values) / len(pc1_values) if pc1_values else None,
            "pca_y": sum(pc2_values) / len(pc2_values) if pc2_values else None,
        }
    return output


def _centroid_alignment_rows(
    from_summary: list[dict[str, Any]],
    to_summary: list[dict[str, Any]],
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    centroid_columns = _centroid_columns(from_summary, to_summary)
    if not centroid_columns:
        raise ValueError("cluster summaries do not contain compatible centroid columns")

    remaining_to = {str(row["cluster"]) for row in to_summary}
    output: list[dict[str, Any]] = []
    for from_row in sorted(from_summary, key=lambda row: _sort_key(row["cluster"])):
        from_cluster = str(from_row["cluster"])
        candidates = [
            (
                _centroid_distance(from_row, to_row, centroid_columns),
                str(to_row["cluster"]),
                to_row,
            )
            for to_row in to_summary
        ]
        candidates.sort(key=lambda item: (item[0], _sort_key(item[1])))
        available = [candidate for candidate in candidates if candidate[1] in remaining_to]
        best_distance, to_cluster, to_row = available[0] if available else candidates[0]
        remaining_to.discard(to_cluster)
        second_distance = candidates[1][0] if len(candidates) > 1 else None
        output.append(
            {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "from_cluster": from_cluster,
                "to_cluster": to_cluster,
                "centroid_distance": best_distance,
                "from_sensor_count": from_row.get("sensor_count", 0),
                "to_sensor_count": to_row.get("sensor_count", 0),
                "mapping_confidence": _mapping_confidence(best_distance, second_distance, from_row, to_row),
            }
        )
    return output


def _aligned_sensor_drift_rows(
    from_rows: list[dict[str, Any]],
    to_rows: list[dict[str, Any]],
    alignment: dict[str, str],
) -> list[dict[str, Any]]:
    from_by_id = {str(row["installation_point_id"]): row for row in from_rows}
    to_by_id = {str(row["installation_point_id"]): row for row in to_rows}
    output: list[dict[str, Any]] = []
    for installation_id in sorted(set(from_by_id) | set(to_by_id), key=_sort_key):
        from_row = from_by_id.get(installation_id, {})
        to_row = to_by_id.get(installation_id, {})
        status = "matched" if from_row and to_row else "from_only" if from_row else "to_only"
        from_cluster = _text(from_row.get("cluster"))
        to_cluster = _text(to_row.get("cluster"))
        aligned_to_cluster = alignment.get(from_cluster, "")
        raw_changed = status == "matched" and from_cluster != to_cluster
        aligned_changed = status == "matched" and aligned_to_cluster != to_cluster
        from_distance = _optional_float(from_row.get("distance_to_centroid"))
        to_distance = _optional_float(to_row.get("distance_to_centroid"))
        output.append(
            {
                "installation_point_id": installation_id,
                "equipment_id": to_row.get("equipment_id") or from_row.get("equipment_id", ""),
                "equipment_name": to_row.get("equipment_name") or from_row.get("equipment_name", ""),
                "sensor_id": to_row.get("sensor_id") or from_row.get("sensor_id", ""),
                "customer_asset_id": to_row.get("customer_asset_id") or from_row.get("customer_asset_id", ""),
                "status": status,
                "from_cluster": from_cluster,
                "to_cluster": to_cluster,
                "aligned_to_cluster": aligned_to_cluster,
                "raw_label_changed": "true" if raw_changed else "false",
                "aligned_changed": "true" if aligned_changed else "false",
                "from_distance_to_centroid": from_distance,
                "to_distance_to_centroid": to_distance,
                "distance_delta": to_distance - from_distance if from_distance is not None and to_distance is not None else None,
            }
        )
    return output


def _aligned_drift_warnings(
    alignment_rows: list[dict[str, Any]],
    raw_changed_count: int,
    aligned_changed_count: int,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    low_confidence = [
        row
        for row in alignment_rows
        if row.get("mapping_confidence") in {"low", "empty_cluster"}
    ]
    if low_confidence:
        warnings.append(
            {
                "level": "warning",
                "code": "label_mapping_ambiguous",
                "cluster_count": len(low_confidence),
                "message": "One or more centroid mappings are weak; interpret drift cautiously.",
            }
        )
    if raw_changed_count != aligned_changed_count:
        warnings.append(
            {
                "level": "info",
                "code": "label_alignment_adjusted_drift",
                "raw_label_changed_count": raw_changed_count,
                "aligned_changed_count": aligned_changed_count,
                "message": "Centroid alignment changed the apparent drift count.",
            }
        )
    return warnings


def _drift_interpretation(
    matched_count: int,
    raw_changed_count: int,
    aligned_changed_count: int,
    warnings: list[dict[str, Any]],
) -> str:
    if matched_count == 0:
        return "No matched sensors; drift cannot be interpreted."
    ratio = aligned_changed_count / matched_count
    if ratio <= 0.05:
        posture = "stable"
    elif ratio <= 0.15:
        posture = "modest movement"
    else:
        posture = "notable movement"
    if raw_changed_count != aligned_changed_count:
        return f"Aligned drift shows {posture}; raw label movement was adjusted by centroid mapping."
    if any(warning.get("code") == "label_mapping_ambiguous" for warning in warnings):
        return f"Aligned drift shows {posture}, but centroid mapping is label-ambiguous."
    return f"Aligned drift shows {posture} across matched sensors."


def _centroid_columns(from_summary: list[dict[str, Any]], to_summary: list[dict[str, Any]]) -> list[str]:
    from_columns = {field for row in from_summary for field in row if field.startswith("centroid_scaled_")}
    to_columns = {field for row in to_summary for field in row if field.startswith("centroid_scaled_")}
    return sorted(from_columns & to_columns)


def _centroid_distance(left: dict[str, Any], right: dict[str, Any], columns: list[str]) -> float:
    return sqrt(sum((_float(left.get(column)) - _float(right.get(column))) ** 2 for column in columns))


def _mapping_confidence(
    best_distance: float,
    second_distance: float | None,
    from_row: dict[str, Any],
    to_row: dict[str, Any],
) -> str:
    if (_optional_int(from_row.get("sensor_count")) or 0) == 0 or (_optional_int(to_row.get("sensor_count")) or 0) == 0:
        return "empty_cluster"
    if second_distance is None:
        return "single_target"
    if second_distance <= 0:
        return "low"
    ratio = best_distance / second_distance
    if ratio <= 0.5:
        return "high"
    if ratio <= 0.8:
        return "medium"
    return "low"


def _model_summary_from_computed(computed: dict[str, Any], action: str) -> dict[str, Any]:
    metrics = computed.get("metrics") or {}
    metric_values = metrics.get("metrics") or {}
    return {
        "model_run_id": computed["model_run_id"],
        "source": computed["source"],
        "date": computed["source_date"],
        "source_date": computed["source_date"],
        "feature_space": computed["feature_space"],
        "feature_space_label": computed.get("feature_space_label"),
        "dimension": computed["dimension"],
        "k": computed["k"],
        "algorithm": computed["algorithm"],
        "random_seed": computed["random_seed"],
        "feature_policy_version": computed["feature_policy_version"],
        "status": "complete",
        "action": action,
        "feature_row_count": computed["feature_row_count"],
        "feature_count": computed["feature_count"],
        "row_count": computed["feature_row_count"],
        "artifact_dir": computed.get("artifact_dir"),
        "silhouette_score": (metric_values.get("silhouette_score") or {}).get("value"),
        "inertia": (metrics.get("kmeans") or {}).get("inertia"),
        "warning_count": len(computed.get("warnings") or []),
    }


def _model_summary_from_run(row: dict[str, Any], action: str) -> dict[str, Any]:
    metrics = _json_loads(row.get("metrics_json"), {})
    metric_values = metrics.get("metrics") or {}
    return {
        "model_run_id": row["model_run_id"],
        "source": row["source"],
        "date": row["source_date"],
        "source_date": row["source_date"],
        "feature_space": row["feature_space"],
        "feature_space_label": FEATURE_SPACE_SPECS.get(row["feature_space"], FeatureSpaceSpec(row["feature_space"], row["feature_space"], "", "")).label,
        "dimension": metrics.get("dimension") or feature_space_dimension(row["feature_space"]),
        "k": int(row["k"]),
        "algorithm": row["algorithm"],
        "random_seed": int(row["random_seed"]),
        "feature_policy_version": row["feature_policy_version"],
        "status": row["status"],
        "action": action,
        "feature_row_count": int(row["feature_row_count"] or 0),
        "feature_count": int(row["feature_count"] or 0),
        "row_count": int(row["feature_row_count"] or 0),
        "artifact_dir": row.get("artifact_dir"),
        "silhouette_score": (metric_values.get("silhouette_score") or {}).get("value"),
        "inertia": (metrics.get("kmeans") or {}).get("inertia"),
        "warning_count": len(_json_loads(row.get("warnings_json"), [])),
    }


def _drift_summary_from_run(row: dict[str, Any], action: str) -> dict[str, Any]:
    return _drift_summary_from_metrics(
        drift_run_id=row["drift_run_id"],
        metrics=_json_loads(row.get("metrics_json"), {}),
        action=action,
    )


def _drift_summary_from_metrics(drift_run_id: str, metrics: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "drift_run_id": drift_run_id,
        "source": metrics.get("source"),
        "from_date": metrics.get("from_date"),
        "to_date": metrics.get("to_date"),
        "feature_space": metrics.get("feature_space"),
        "dimension": metrics.get("dimension"),
        "k": metrics.get("k"),
        "status": "complete",
        "action": action,
        "matched_sensor_count": metrics.get("matched_sensor_count"),
        "raw_label_changed_count": metrics.get("raw_label_changed_count"),
        "aligned_changed_count": metrics.get("aligned_changed_count"),
        "aligned_changed_ratio": metrics.get("aligned_changed_ratio"),
        "warning_count": metrics.get("warning_count", 0),
        "artifact_dir": metrics.get("artifact_dir"),
        "interpretation": metrics.get("interpretation", ""),
    }


def _quality_row_from_model(model: dict[str, Any]) -> dict[str, Any]:
    metrics = _json_loads(model.get("metrics_json"), {})
    metric_values = metrics.get("metrics") or {}
    warnings = _json_loads(model.get("warnings_json"), [])
    return {
        "date": model["source_date"],
        "model_run_id": model["model_run_id"],
        "feature_space": model["feature_space"],
        "row_count": model["feature_row_count"],
        "feature_count": model["feature_count"],
        "k": model["k"],
        "inertia": (metrics.get("kmeans") or {}).get("inertia"),
        "silhouette_score": (metric_values.get("silhouette_score") or {}).get("value"),
        "calinski_harabasz_score": (metric_values.get("calinski_harabasz_score") or {}).get("value"),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _window_drift_row(view: dict[str, Any]) -> dict[str, Any]:
    metrics = view.get("aligned_metrics") or {}
    return {
        "from_date": metrics.get("from_date"),
        "to_date": metrics.get("to_date"),
        "status": "complete",
        "matched_sensor_count": metrics.get("matched_sensor_count"),
        "raw_label_changed_count": metrics.get("raw_label_changed_count"),
        "aligned_changed_count": metrics.get("aligned_changed_count"),
        "raw_label_changed_ratio": metrics.get("raw_label_changed_ratio"),
        "aligned_changed_ratio": metrics.get("aligned_changed_ratio"),
        "warning_count": metrics.get("warning_count", 0),
        "warnings": metrics.get("warnings", []),
        "interpretation": metrics.get("interpretation"),
    }


def _identifier_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "installation_point_id": row.get("installation_point_id"),
        "installation_point_name": row.get("installation_point_name"),
        "equipment_id": row.get("equipment_id"),
        "equipment_name": row.get("equipment_name"),
        "sensor_id": row.get("sensor_id"),
        "customer_asset_id": row.get("customer_asset_id"),
    }


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in VALID_REGISTRY_SOURCES:
        allowed = ", ".join(sorted(VALID_REGISTRY_SOURCES))
        raise ValueError(f"source must be one of: {allowed}")
    return source_mode


def _validate_feature_space(feature_space: str) -> FeatureSpaceSpec:
    candidate = (feature_space or "").strip().lower()
    if candidate not in FEATURE_SPACE_SPECS:
        allowed = ", ".join(FEATURE_SPACE_SPECS)
        raise ValueError(f"feature_space must be one of: {allowed}")
    return FEATURE_SPACE_SPECS[candidate]


def _validate_feature_spaces(feature_spaces: Iterable[str]) -> list[str]:
    parsed = []
    for raw_value in feature_spaces:
        value = raw_value.strip().lower()
        if not value:
            continue
        parsed.append(_validate_feature_space(value).name)
    if not parsed:
        raise ValueError("at least one feature space is required")
    return parsed


def _validate_ks(ks: Iterable[int]) -> list[int]:
    parsed = []
    for raw_value in ks:
        value = int(raw_value)
        if value < 1:
            raise ValueError("k must be at least 1")
        parsed.append(value)
    if not parsed:
        raise ValueError("at least one k value is required")
    return parsed


def _action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        action = str(row.get("action") or row.get("status") or "unknown")
        counts[action] = counts.get(action, 0) + 1
    return counts


def _query_dicts(
    connection: sqlite3.Connection,
    sql: str,
    params: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, tuple(params))]


def _model_dir_name(source_date: str, source: str, feature_space: str, k: int) -> str:
    return f"date={source_date}_source={source}_feature_space={feature_space}_k={k}"


def _drift_dir_name(from_date: date, to_date: date, source: str, feature_space: str, k: int) -> str:
    return (
        f"from={from_date.isoformat()}_to={to_date.isoformat()}_"
        f"source={source}_feature_space={feature_space}_k={k}"
    )


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    result = float(value)
    return result if isfinite(result) else None


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value)


def _sort_key(value: Any) -> tuple[int, Any]:
    raw = str(value)
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

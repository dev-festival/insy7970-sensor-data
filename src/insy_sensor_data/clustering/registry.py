from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import isfinite
from statistics import median
from typing import Any, Iterable
import json
import sqlite3

from insy_sensor_data.clustering import engine
from insy_sensor_data.clustering.features import IDENTIFIER_FIELDS
from insy_sensor_data.clustering.policy import (
    ACTIVE_MODEL_POLICY,
    FeatureSpaceSpec,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store, observation_db_path
from insy_sensor_data.observations import load_sensor_daily_snapshots
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.schema import snapshot_revision


REGISTRY_SCHEMA_VERSION = 2
FEATURE_POLICY_VERSION = ACTIVE_MODEL_POLICY.feature_policy_version
SCALER_POLICY = ACTIVE_MODEL_POLICY.scaler_policy
ALGORITHM = ACTIVE_MODEL_POLICY.algorithm
ALIGNMENT_POLICY = ACTIVE_MODEL_POLICY.alignment_policy
DEFAULT_FEATURE_SPACES = tuple(spec.name for spec in ACTIVE_MODEL_POLICY.feature_spaces)
DEFAULT_REGISTRY_KS = (ACTIVE_MODEL_POLICY.k,)
DEFAULT_RANDOM_SEED = ACTIVE_MODEL_POLICY.random_seed
DEFAULT_MAX_ITERATIONS = ACTIVE_MODEL_POLICY.max_iterations
DEFAULT_TOLERANCE = ACTIVE_MODEL_POLICY.tolerance
VALID_REGISTRY_SOURCES = {"mock", "api"}

FEATURE_SPACE_SPECS = ACTIVE_MODEL_POLICY.feature_specs


class InsufficientModelDataError(ValueError):
    """The snapshot exists but cannot support the active model contract."""


class ModelNotReadyError(FileNotFoundError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


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
    _ensure_model_schema(settings)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    selected_feature_spaces = _validate_feature_spaces(feature_spaces or DEFAULT_FEATURE_SPACES)
    selected_ks = _validate_ks(ks or DEFAULT_REGISTRY_KS)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
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
                            status=(
                                "insufficient_data"
                                if isinstance(exc, InsufficientModelDataError)
                                else "failed"
                            ),
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
        "model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "feature_policy_version": FEATURE_POLICY_VERSION,
        "scaler_policy": SCALER_POLICY,
        "algorithm": ALGORITHM,
        "database_path": observation_db_path(settings).as_posix(),
        "date_count": len(dates),
        "model_count": len(models),
        "models_built": model_actions.get("built", 0),
        "models_reused": model_actions.get("reused", 0),
        "models_failed": model_actions.get("failed", 0),
        "models_insufficient_data": model_actions.get("insufficient_data", 0),
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
    _ensure_model_schema(settings)
    spec = _validate_feature_space(feature_space)
    ACTIVE_MODEL_POLICY.validate_k(k)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
    if max_iterations != ACTIVE_MODEL_POLICY.max_iterations:
        raise ValueError(
            f"max_iterations is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.max_iterations}."
        )

    model_run_id = cluster_model_run_id(
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    input_revision = _current_snapshot_revision(
        settings,
        source=source_mode,
        source_date=run_date.isoformat(),
    )
    if input_revision is None:
        raise FileNotFoundError(
            f"Missing snapshot revision for source {source_mode} date {run_date.isoformat()}."
        )
    if not force:
        existing = _find_model_run(settings, model_run_id=model_run_id)
        readiness = model_run_readiness(existing, input_revision)
        if readiness["status"] == "ready":
            return _model_summary_from_run(existing, action="reused")

    computed = _compute_feature_space_model(
        settings=settings,
        run_date=run_date,
        source=source_mode,
        spec=spec,
        k=k,
        random_seed=random_seed,
        max_iterations=max_iterations,
        input_revision=input_revision,
    )
    artifact_dir = "sqlite"
    completed_at = _utc_now()
    metrics = {
        **computed["metrics"],
        "outputs": {},
    }
    computed = {
        **computed,
        "artifact_dir": artifact_dir,
        "metrics": metrics,
        "completed_at": completed_at,
    }
    _persist_complete_model_run(settings, computed)
    return _model_summary_from_computed(computed, action="built")


def rebuild_active_model_date(
    settings: AppSettings,
    *,
    run_date: date,
    source: str,
    feature_spaces: Iterable[str] | None = None,
    force: bool = True,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    _ensure_model_schema(settings)
    selected_feature_spaces = _validate_feature_spaces(
        feature_spaces or DEFAULT_FEATURE_SPACES
    )
    models: list[dict[str, Any]] = []
    for feature_space in selected_feature_spaces:
        try:
            models.append(
                build_cluster_model_run(
                    settings=settings,
                    run_date=run_date,
                    source=source_mode,
                    feature_space=feature_space,
                    k=ACTIVE_MODEL_POLICY.k,
                    random_seed=ACTIVE_MODEL_POLICY.random_seed,
                    max_iterations=ACTIVE_MODEL_POLICY.max_iterations,
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
                    k=ACTIVE_MODEL_POLICY.k,
                    random_seed=ACTIVE_MODEL_POLICY.random_seed,
                    error=str(exc),
                    status=(
                        "insufficient_data"
                        if isinstance(exc, InsufficientModelDataError)
                        else "failed"
                    ),
                )
            )

    drift_runs: list[dict[str, Any]] = []
    adjacent_pairs = [
        (run_date - timedelta(days=1), run_date),
        (run_date, run_date + timedelta(days=1)),
    ]
    for from_date, to_date in adjacent_pairs:
        if (
            _current_snapshot_revision(
                settings,
                source=source_mode,
                source_date=from_date.isoformat(),
            )
            is None
            or _current_snapshot_revision(
                settings,
                source=source_mode,
                source_date=to_date.isoformat(),
            )
            is None
        ):
            continue
        for feature_space in selected_feature_spaces:
            try:
                drift_runs.append(
                    build_registered_cluster_drift(
                        settings=settings,
                        from_date=from_date,
                        to_date=to_date,
                        source=source_mode,
                        feature_space=feature_space,
                        k=ACTIVE_MODEL_POLICY.k,
                        random_seed=ACTIVE_MODEL_POLICY.random_seed,
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
                        "k": ACTIVE_MODEL_POLICY.k,
                        "status": getattr(exc, "status", "skipped"),
                        "action": "skipped",
                        "reason": str(exc),
                    }
                )
    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "models": models,
        "drift_runs": drift_runs,
        "model_counts": _action_counts(models),
        "drift_counts": _action_counts(drift_runs),
        "readiness": active_date_readiness(
            settings,
            source=source_mode,
            run_date=run_date,
        ),
    }


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
    _ensure_model_schema(settings)
    spec = _validate_feature_space(feature_space)
    if to_date < from_date:
        raise ValueError("to_date must be on or after from_date")
    ACTIVE_MODEL_POLICY.validate_k(k)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
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
    alignment_rows = engine.centroid_alignment_rows(
        from_summary=from_centroids,
        to_summary=to_centroids,
        from_date=from_date,
        to_date=to_date,
    )
    alignment = {str(row["from_cluster"]): str(row["to_cluster"]) for row in alignment_rows}
    drift_rows = engine.aligned_sensor_drift_rows(from_rows, to_rows, alignment)
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
    artifact_dir = "sqlite"
    metrics = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "alignment_policy": ALIGNMENT_POLICY,
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "from_snapshot_revision": from_model["input_snapshot_revision"],
        "to_snapshot_revision": to_model["input_snapshot_revision"],
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
        "artifact_dir": artifact_dir,
        "outputs": {},
        "built_at": _utc_now(),
    }
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
    clauses: list[str] = [
        "run.feature_policy_version = ?",
        "run.model_policy_version = ?",
        "run.k = ?",
    ]
    params: list[Any] = [
        FEATURE_POLICY_VERSION,
        ACTIVE_MODEL_POLICY.version,
        ACTIVE_MODEL_POLICY.k,
    ]
    if source_mode is not None:
        clauses.append("run.source = ?")
        params.append(source_mode)
    if start_date is not None:
        clauses.append("run.source_date >= ?")
        params.append(start_date.isoformat())
    if end_date is not None:
        clauses.append("run.source_date <= ?")
        params.append(end_date.isoformat())
    where = f"WHERE {' AND '.join(clauses)}"
    with read_store(
        settings,
        required_tables=("cluster_model_runs", "snapshot_revisions"),
    ) as connection:
        rows = _query_dicts(
            connection,
            f"""
            SELECT
                run.model_run_id,
                run.source,
                run.source_date,
                run.feature_space,
                run.k,
                run.algorithm,
                run.random_seed,
                run.feature_policy_version,
                run.model_policy_version,
                run.feature_columns_json,
                run.scaler_policy,
                run.input_snapshot_hash,
                run.input_snapshot_revision,
                run.max_iterations,
                run.tolerance,
                run.pca_iterations,
                run.input_snapshot_row_count,
                run.feature_row_count,
                run.feature_count,
                run.status,
                run.created_at,
                run.completed_at,
                run.artifact_dir,
                run.metrics_json,
                run.warnings_json,
                snapshot.snapshot_revision AS current_snapshot_revision
            FROM cluster_model_runs AS run
            LEFT JOIN snapshot_revisions AS snapshot
              ON snapshot.source = run.source
             AND snapshot.source_date = run.source_date
            {where}
            ORDER BY run.source_date, run.feature_space, run.created_at
            """,
            params,
        )
    models = []
    for row in rows:
        readiness = model_run_readiness(row, row.get("current_snapshot_revision"))
        models.append(
            {
                **_model_summary_from_run(row, action=row.get("status", "")),
                "readiness": readiness,
                "readiness_status": readiness["status"],
            }
        )
    ready = [model for model in models if model["readiness_status"] == "ready"]
    return {
        "source": source_mode,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "feature_spaces": [spec.name for spec in ACTIVE_MODEL_POLICY.feature_spaces],
        "ks": [ACTIVE_MODEL_POLICY.k],
        "models": models,
        "count": len(models),
        "complete_count": sum(1 for model in models if model["status"] == "complete"),
        "ready_count": len(ready),
        "stale_count": sum(1 for model in models if model["readiness_status"] == "stale"),
    }


def load_registered_cluster_view(
    settings: AppSettings,
    run_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
    installation_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    ACTIVE_MODEL_POLICY.validate_k(k)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
    model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    rows = _model_assignment_rows(
        settings,
        model["model_run_id"],
        installation_point_ids=installation_point_ids,
    )
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
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "input_snapshot_revision": model["input_snapshot_revision"],
        "readiness": "ready",
        "artifact_dir": model.get("artifact_dir") or "sqlite",
        "registered": True,
        "metrics": metrics,
        "row_count": len(rows),
        "all_row_count": int(model.get("feature_row_count") or len(rows)),
        "cluster_row_count": len(cluster_rows),
        "pca_row_count": len(pca_rows),
        "rows": rows,
        "cluster_rows": cluster_rows,
        "pca_rows": pca_rows,
    }


def load_registered_cluster_summary(
    settings: AppSettings,
    run_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
    installation_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return cluster membership counts without loading assignment projections."""
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    ACTIVE_MODEL_POLICY.validate_k(k)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
    model = _require_model_run(
        settings=settings,
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=k,
        random_seed=random_seed,
    )
    counts = _model_assignment_counts(
        settings,
        model["model_run_id"],
        installation_point_ids=installation_point_ids,
    )
    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": int(model["k"]),
        "model_run_id": model["model_run_id"],
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "input_snapshot_revision": model["input_snapshot_revision"],
        "readiness": "ready",
        "row_count": sum(int(row["sensor_count"]) for row in counts),
        "all_row_count": int(model.get("feature_row_count") or 0),
        "cluster_counts": counts,
    }


def load_registered_drift_view(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    feature_space: str,
    k: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
    installation_point_ids: set[str] | None = None,
    summary_only: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    ACTIVE_MODEL_POLICY.validate_k(k)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
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
    if (
        drift.get("model_policy_version") != ACTIVE_MODEL_POLICY.version
        or drift.get("from_snapshot_revision")
        != from_model.get("input_snapshot_revision")
        or drift.get("to_snapshot_revision")
        != to_model.get("input_snapshot_revision")
    ):
        raise ModelNotReadyError(
            "stale",
            f"Registered drift is stale for source {source_mode} "
            f"{from_date.isoformat()} to {to_date.isoformat()} feature_space={spec.name}.",
        )
    metrics = _json_loads(drift.get("metrics_json"), {})
    rows = (
        []
        if summary_only and installation_point_ids is None
        else _drift_assignment_rows(
            settings,
            drift_id,
            installation_point_ids=installation_point_ids,
        )
    )
    alignment_rows = _centroid_alignment_for_drift(settings, drift_id)
    if installation_point_ids is not None:
        metrics = _scoped_drift_metrics(metrics, rows, alignment_rows)
    return {
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "drift_run_id": drift_id,
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
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
    installation_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    ACTIVE_MODEL_POLICY.validate_k(k)
    if random_seed != ACTIVE_MODEL_POLICY.random_seed:
        raise ValueError(
            f"random_seed is service-owned by active policy {ACTIVE_MODEL_POLICY.version}; "
            f"expected {ACTIVE_MODEL_POLICY.random_seed}."
        )
    dates = _date_range(start_date, end_date)
    date_readiness = [
        active_model_readiness(
            settings,
            source=source_mode,
            run_date=run_date,
            feature_space=spec.name,
        )
        for run_date in dates
    ]
    readiness_by_date = {row["date"]: row for row in date_readiness}
    ready_models: dict[str, dict[str, Any]] = {}
    for run_date in dates:
        readiness = readiness_by_date[run_date.isoformat()]
        if readiness["status"] != "ready":
            continue
        model = _find_model_run(
            settings,
            model_run_id=str(readiness["model_run_id"]),
        )
        if model is not None:
            ready_models[run_date.isoformat()] = model

    drift_views: list[dict[str, Any]] = []
    missing_pairs: list[dict[str, Any]] = []
    for from_date, to_date in zip(dates, dates[1:], strict=False):
        from_readiness = readiness_by_date[from_date.isoformat()]
        to_readiness = readiness_by_date[to_date.isoformat()]
        if from_readiness["status"] != "ready" or to_readiness["status"] != "ready":
            missing_pairs.append(
                {
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "status": "model_gap",
                    "from_model_status": from_readiness["status"],
                    "to_model_status": to_readiness["status"],
                    "reason": "one or both adjacent active models are not ready",
                }
            )
            continue
        try:
            drift_views.append(
                load_registered_drift_view(
                    settings=settings,
                    from_date=from_date,
                    to_date=to_date,
                    source=source_mode,
                    feature_space=spec.name,
                    k=k,
                    random_seed=random_seed,
                    installation_point_ids=installation_point_ids,
                    summary_only=True,
                )
            )
        except (FileNotFoundError, ModelNotReadyError) as exc:
            missing_pairs.append(
                {
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "status": getattr(exc, "status", "missing"),
                    "from_model_status": from_readiness["status"],
                    "to_model_status": to_readiness["status"],
                    "reason": str(exc),
                }
            )

    quality_rows = [
        _quality_row_from_model(ready_models[run_date.isoformat()])
        for run_date in dates
        if run_date.isoformat() in ready_models
    ]
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
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "date_count": len(dates),
        "ready_date_count": len(ready_models),
        "missing_date_count": len(dates) - len(ready_models),
        "pair_count": max(0, len(dates) - 1),
        "complete_pair_count": len(drift_views),
        "missing_pair_count": len(missing_pairs),
        "warning_count": warning_count,
        "model_run_ids": [model["model_run_id"] for model in ready_models.values()],
        "drift_run_ids": [view["drift_run_id"] for view in drift_views],
    }
    return {
        "source": source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "feature_space": spec.name,
        "dimension": spec.dimension,
        "k": k,
        "status": "complete" if not missing_pairs else "partial",
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "artifact_dir": "sqlite",
        "registered": True,
        "metrics": metrics,
        "window_rows": quality_rows,
        "quality_rows": quality_rows,
        "aligned_drift_rows": aligned_rows,
        "alignment_rows": alignment_rows,
        "date_readiness": date_readiness,
        "missing_dates": [
            row for row in date_readiness if row["status"] != "ready"
        ],
        "missing_pairs": missing_pairs,
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
        f"{ALGORITHM}:seed{random_seed}:{ACTIVE_MODEL_POLICY.version}"
    )


def cluster_drift_run_id(from_model_run_id: str, to_model_run_id: str) -> str:
    return f"{from_model_run_id}->{to_model_run_id}:{ALIGNMENT_POLICY}"


def feature_space_dimension(feature_space: str) -> str:
    return _validate_feature_space(feature_space).dimension


def active_model_readiness(
    settings: AppSettings,
    *,
    source: str,
    run_date: date,
    feature_space: str,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    spec = _validate_feature_space(feature_space)
    model_run_id = cluster_model_run_id(
        source=source_mode,
        source_date=run_date.isoformat(),
        feature_space=spec.name,
        k=ACTIVE_MODEL_POLICY.k,
        random_seed=ACTIVE_MODEL_POLICY.random_seed,
    )
    model = _find_model_run(settings, model_run_id=model_run_id)
    revision = _current_snapshot_revision(
        settings,
        source=source_mode,
        source_date=run_date.isoformat(),
    )
    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "feature_space": spec.name,
        "model_run_id": model_run_id,
        **model_run_readiness(model, revision),
    }


def active_date_readiness(
    settings: AppSettings,
    *,
    source: str,
    run_date: date,
) -> dict[str, Any]:
    rows = [
        active_model_readiness(
            settings,
            source=source,
            run_date=run_date,
            feature_space=spec.name,
        )
        for spec in ACTIVE_MODEL_POLICY.feature_spaces
    ]
    statuses = {str(row["status"]) for row in rows}
    if statuses == {"ready"}:
        status = "ready"
    else:
        status = next(
            candidate
            for candidate in ("failed", "insufficient_data", "stale", "missing")
            if candidate in statuses
        )
    return {
        "source": source,
        "date": run_date.isoformat(),
        "status": status,
        "registered_model_ready": status == "ready",
        "ready_model_count": sum(1 for row in rows if row["status"] == "ready"),
        "required_model_count": len(rows),
        "feature_readiness": rows,
    }


def model_run_readiness(
    model: dict[str, Any] | None,
    current_snapshot_revision: str | None,
) -> dict[str, Any]:
    if current_snapshot_revision is None:
        return {
            "status": "missing",
            "reason": "the durable snapshot revision is missing",
            "current_snapshot_revision": None,
            "input_snapshot_revision": (
                model.get("input_snapshot_revision") if model else None
            ),
        }
    if model is None:
        return {
            "status": "missing",
            "reason": "the active-policy model has not been built",
            "current_snapshot_revision": current_snapshot_revision,
            "input_snapshot_revision": None,
        }
    build_status = str(model.get("status") or "failed")
    if build_status in {"failed", "insufficient_data"}:
        return {
            "status": build_status,
            "reason": (
                "the snapshot cannot satisfy the active feature contract"
                if build_status == "insufficient_data"
                else "the most recent active-policy model build failed"
            ),
            "current_snapshot_revision": current_snapshot_revision,
            "input_snapshot_revision": model.get("input_snapshot_revision"),
        }
    if build_status != "complete":
        return {
            "status": "failed",
            "reason": f"the model build status is {build_status!r}",
            "current_snapshot_revision": current_snapshot_revision,
            "input_snapshot_revision": model.get("input_snapshot_revision"),
        }
    if model.get("model_policy_version") != ACTIVE_MODEL_POLICY.version:
        return {
            "status": "stale",
            "reason": "the model policy version does not match the active policy",
            "current_snapshot_revision": current_snapshot_revision,
            "input_snapshot_revision": model.get("input_snapshot_revision"),
        }
    input_revision = model.get("input_snapshot_revision")
    if input_revision != current_snapshot_revision:
        return {
            "status": "stale",
            "reason": "the input snapshot revision no longer matches the durable snapshot",
            "current_snapshot_revision": current_snapshot_revision,
            "input_snapshot_revision": input_revision,
        }
    return {
        "status": "ready",
        "reason": "snapshot revision and active model policy match",
        "current_snapshot_revision": current_snapshot_revision,
        "input_snapshot_revision": input_revision,
    }


def _current_snapshot_revision(
    settings: AppSettings,
    *,
    source: str,
    source_date: str,
) -> str | None:
    with read_store(settings, required_tables=("snapshot_revisions",)) as connection:
        row = connection.execute(
            """
            SELECT snapshot_revision
            FROM snapshot_revisions
            WHERE source = ? AND source_date = ?
            """,
            (source, source_date),
        ).fetchone()
    return str(row[0]) if row is not None else None


def _compute_feature_space_model(
    settings: AppSettings,
    run_date: date,
    source: str,
    spec: FeatureSpaceSpec,
    k: int,
    random_seed: int,
    max_iterations: int,
    input_revision: str,
) -> dict[str, Any]:
    snapshot_rows = load_sensor_daily_snapshots(
        settings=settings,
        run_date=run_date,
        source=source,
    )
    feature_rows_all, feature_columns = _store_feature_rows(snapshot_rows, spec)
    if not feature_rows_all:
        raise InsufficientModelDataError("feature matrix has no rows")
    if not feature_columns:
        raise InsufficientModelDataError(
            f"feature space {spec.name} has no sufficiently covered feature columns"
        )
    if k > len(feature_rows_all):
        raise InsufficientModelDataError("k cannot exceed feature row count")

    feature_rows = [
        {
            **{field: row.get(field, "") for field in IDENTIFIER_FIELDS},
            **{field: row.get(field, "") for field in feature_columns},
        }
        for row in feature_rows_all
    ]
    matrix = engine.numeric_matrix(feature_rows, feature_columns)
    scaled = engine.standard_scale(matrix, feature_columns)
    kmeans = engine.kmeans(
        scaled.values,
        k=k,
        random_seed=random_seed,
        max_iterations=max_iterations,
        tolerance=ACTIVE_MODEL_POLICY.tolerance,
    )
    pca = engine.pca_coordinates(
        scaled.values,
        iterations=ACTIVE_MODEL_POLICY.pca_iterations,
    )
    metrics = engine.cluster_metrics(scaled.values, kmeans)
    cluster_counts = engine.cluster_counts(kmeans.labels, k)
    sensor_rows = engine.sensor_cluster_rows(feature_rows, feature_columns, kmeans)
    summary_rows = engine.cluster_summary_rows(
        feature_rows=feature_rows,
        feature_columns=feature_columns,
        scaled=scaled,
        result=kmeans,
        k=k,
    )
    pca_rows = engine.pca_rows(feature_rows, kmeans, pca)
    computed_revision = snapshot_revision(source, run_date.isoformat(), snapshot_rows)
    if computed_revision != input_revision:
        raise ValueError(
            "Snapshot revision changed while the model was being prepared; retry the build."
        )
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
        "model_policy_version": ACTIVE_MODEL_POLICY.version,
        "scaler_policy": SCALER_POLICY,
        "max_iterations": ACTIVE_MODEL_POLICY.max_iterations,
        "tolerance": ACTIVE_MODEL_POLICY.tolerance,
        "pca_iterations": ACTIVE_MODEL_POLICY.pca_iterations,
        "feature_columns": feature_columns,
        "input_snapshot_hash": input_revision,
        "input_snapshot_revision": input_revision,
        "input_snapshot_row_count": len(feature_rows_all),
        "feature_row_count": len(feature_rows),
        "feature_count": len(feature_columns),
        "created_at": built_at,
        "sensor_rows": sensor_rows,
        "cluster_rows": summary_rows,
        "pca_rows": pca_rows,
        "warnings": warnings,
        "metrics": {
            "schema_version": engine.ENGINE_SCHEMA_VERSION,
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
            "model_policy_version": ACTIVE_MODEL_POLICY.version,
            "scaler_policy": SCALER_POLICY,
            "built_at": built_at,
            "feature_matrix_path": None,
            "feature_summary_path": None,
            "feature_status": "store_backed",
            "input_snapshot_hash": input_revision,
            "input_snapshot_revision": input_revision,
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


def _persist_complete_model_run(settings: AppSettings, computed: dict[str, Any]) -> None:
    with connect_observation_store(settings) as connection:
        with connection:
            revision_row = connection.execute(
                """
                SELECT snapshot_revision
                FROM snapshot_revisions
                WHERE source = ? AND source_date = ?
                """,
                (computed["source"], computed["source_date"]),
            ).fetchone()
            current_revision = str(revision_row[0]) if revision_row is not None else None
            if current_revision != computed["input_snapshot_revision"]:
                raise ValueError(
                    "Snapshot revision changed before model persistence; the transaction was rolled back."
                )
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
                    model_policy_version,
                    feature_columns_json,
                    scaler_policy,
                    input_snapshot_hash,
                    input_snapshot_revision,
                    max_iterations,
                    tolerance,
                    pca_iterations,
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    computed["model_policy_version"],
                    json.dumps(computed["feature_columns"], sort_keys=True),
                    computed["scaler_policy"],
                    computed.get("input_snapshot_hash"),
                    computed["input_snapshot_revision"],
                    computed["max_iterations"],
                    computed["tolerance"],
                    computed["pca_iterations"],
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
    status: str,
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
    warning = {
        "level": "warning" if status == "insufficient_data" else "error",
        "code": (
            "insufficient_model_data"
            if status == "insufficient_data"
            else "model_build_failed"
        ),
        "message": error,
    }
    input_revision = _current_snapshot_revision(
        settings,
        source=source_mode,
        source_date=run_date.isoformat(),
    )
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
                    model_policy_version,
                    feature_columns_json,
                    scaler_policy,
                    input_snapshot_hash,
                    input_snapshot_revision,
                    max_iterations,
                    tolerance,
                    pca_iterations,
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ACTIVE_MODEL_POLICY.version,
                    "[]",
                    SCALER_POLICY,
                    input_revision,
                    input_revision,
                    ACTIVE_MODEL_POLICY.max_iterations,
                    ACTIVE_MODEL_POLICY.tolerance,
                    ACTIVE_MODEL_POLICY.pca_iterations,
                    0,
                    0,
                    0,
                    status,
                    now,
                    now,
                    None,
                    json.dumps(
                        {
                            "error": error,
                            "model_policy_version": ACTIVE_MODEL_POLICY.version,
                            "input_snapshot_revision": input_revision,
                        },
                        sort_keys=True,
                    ),
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
        "status": status,
        "action": status,
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
            current_revisions = {
                str(row["source_date"]): str(row["snapshot_revision"])
                for row in connection.execute(
                    """
                    SELECT source_date, snapshot_revision
                    FROM snapshot_revisions
                    WHERE source = ? AND source_date IN (?, ?)
                    """,
                    (
                        metrics["source"],
                        metrics["from_date"],
                        metrics["to_date"],
                    ),
                ).fetchall()
            }
            if (
                current_revisions.get(metrics["from_date"])
                != from_model["input_snapshot_revision"]
                or current_revisions.get(metrics["to_date"])
                != to_model["input_snapshot_revision"]
            ):
                raise ValueError(
                    "Snapshot revision changed before drift persistence; the transaction was rolled back."
                )
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
                    model_policy_version,
                    from_snapshot_revision,
                    to_snapshot_revision,
                    matched_sensor_count,
                    raw_changed_sensor_count,
                    aligned_changed_sensor_count,
                    status,
                    metrics_json,
                    warnings_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ACTIVE_MODEL_POLICY.version,
                    from_model["input_snapshot_revision"],
                    to_model["input_snapshot_revision"],
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
                model_policy_version,
                feature_columns_json,
                scaler_policy,
                input_snapshot_hash,
                input_snapshot_revision,
                max_iterations,
                tolerance,
                pca_iterations,
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
                model_policy_version,
                from_snapshot_revision,
                to_snapshot_revision,
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
    model = _find_model_run(settings, model_run_id=model_run_id)
    revision = _current_snapshot_revision(
        settings,
        source=source,
        source_date=source_date,
    )
    readiness = model_run_readiness(model, revision)
    if readiness["status"] != "ready":
        raise ModelNotReadyError(
            readiness["status"],
            f"Registered model is {readiness['status']} for source {source} "
            f"date {source_date} feature_space={feature_space}; "
            f"{readiness['reason']}",
        )
    assert model is not None
    return model


def _model_assignment_rows(
    settings: AppSettings,
    model_run_id: str,
    *,
    installation_point_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if installation_point_ids is not None and not installation_point_ids:
        return []
    clauses = ["model_run_id = ?"]
    params: list[Any] = [model_run_id]
    if installation_point_ids is not None:
        placeholders = ", ".join("?" for _value in installation_point_ids)
        clauses.append(f"installation_point_id IN ({placeholders})")
        params.extend(sorted(installation_point_ids, key=engine.sort_key))
    with read_store(
        settings,
        required_tables=("cluster_model_assignments",),
    ) as connection:
        rows = _query_dicts(
            connection,
            f"""
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
            WHERE {" AND ".join(clauses)}
            ORDER BY CAST(installation_point_id AS INTEGER), installation_point_id
            """,
            tuple(params),
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


def _model_assignment_counts(
    settings: AppSettings,
    model_run_id: str,
    *,
    installation_point_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if installation_point_ids is not None and not installation_point_ids:
        return []
    clauses = ["model_run_id = ?"]
    params: list[Any] = [model_run_id]
    if installation_point_ids is not None:
        placeholders = ", ".join("?" for _value in installation_point_ids)
        clauses.append(f"installation_point_id IN ({placeholders})")
        params.extend(sorted(installation_point_ids, key=engine.sort_key))
    with read_store(
        settings,
        required_tables=("cluster_model_assignments",),
    ) as connection:
        rows = _query_dicts(
            connection,
            f"""
            SELECT cluster, COUNT(*) AS sensor_count
            FROM cluster_model_assignments
            WHERE {" AND ".join(clauses)}
            GROUP BY cluster
            ORDER BY cluster
            """,
            tuple(params),
        )
    return [
        {"cluster": int(row["cluster"]), "sensor_count": int(row["sensor_count"])}
        for row in rows
    ]


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


def _drift_assignment_rows(
    settings: AppSettings,
    drift_run_id: str,
    *,
    installation_point_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if installation_point_ids is not None and not installation_point_ids:
        return []
    clauses = ["assignment.drift_run_id = ?"]
    params: list[Any] = [drift_run_id]
    if installation_point_ids is not None:
        placeholders = ", ".join("?" for _value in installation_point_ids)
        clauses.append(f"assignment.installation_point_id IN ({placeholders})")
        params.extend(sorted(installation_point_ids, key=engine.sort_key))
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
            f"""
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
            WHERE {" AND ".join(clauses)}
            ORDER BY CAST(assignment.installation_point_id AS INTEGER), assignment.installation_point_id
            """,
            tuple(params),
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


def _store_feature_rows(
    snapshot_rows: list[dict[str, Any]],
    spec: FeatureSpaceSpec,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not snapshot_rows:
        return [], []
    candidates = _feature_space_columns(list(snapshot_rows[0]), spec)
    feature_columns: list[str] = []
    imputation: dict[str, float] = {}
    minimum_non_null = max(
        1,
        int(
            len(snapshot_rows)
            * ACTIVE_MODEL_POLICY.minimum_feature_coverage
            + 0.999999
        ),
    )
    for field in candidates:
        values = [
            float(row[field])
            for row in snapshot_rows
            if row.get(field) not in (None, "") and isfinite(float(row[field]))
        ]
        if len(values) < minimum_non_null:
            continue
        feature_columns.append(field)
        imputation[field] = float(median(values))
    rows = [
        {
            **{field: row.get(field, "") for field in IDENTIFIER_FIELDS},
            **{
                field: (
                    float(row[field])
                    if row.get(field) not in (None, "") and isfinite(float(row[field]))
                    else imputation[field]
                )
                for field in feature_columns
            },
        }
        for row in snapshot_rows
    ]
    return rows, feature_columns


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


def _scoped_drift_metrics(
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
    alignment_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recalculate sensor-level drift metrics after the SQL scope is applied."""
    matched_rows = [row for row in rows if row.get("status") == "matched"]
    matched_count = len(matched_rows)
    raw_changed_count = sum(
        1 for row in matched_rows if row.get("raw_label_changed") == "true"
    )
    aligned_changed_count = sum(
        1 for row in matched_rows if row.get("aligned_changed") == "true"
    )
    warnings = _aligned_drift_warnings(
        alignment_rows,
        raw_changed_count,
        aligned_changed_count,
    )
    return {
        **metrics,
        "matched_sensor_count": matched_count,
        "raw_label_changed_count": raw_changed_count,
        "aligned_changed_count": aligned_changed_count,
        "raw_label_changed_ratio": (
            raw_changed_count / matched_count if matched_count else None
        ),
        "aligned_changed_ratio": (
            aligned_changed_count / matched_count if matched_count else None
        ),
        "warning_count": len(warnings),
        "warnings": warnings,
        "interpretation": _drift_interpretation(
            matched_count=matched_count,
            raw_changed_count=raw_changed_count,
            aligned_changed_count=aligned_changed_count,
            warnings=warnings,
        ),
        "scope_applied": True,
    }


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
        "model_policy_version": computed["model_policy_version"],
        "input_snapshot_revision": computed["input_snapshot_revision"],
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
        "model_policy_version": row.get("model_policy_version"),
        "input_snapshot_revision": row.get("input_snapshot_revision"),
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
        ACTIVE_MODEL_POLICY.validate_k(value)
        parsed.append(value)
    if not parsed:
        raise ValueError("at least one k value is required")
    return list(dict.fromkeys(parsed))


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


def _ensure_model_schema(settings: AppSettings) -> None:
    with connect_observation_store(settings):
        pass


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


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

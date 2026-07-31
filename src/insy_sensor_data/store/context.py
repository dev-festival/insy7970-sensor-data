from __future__ import annotations

from collections import defaultdict
from typing import Any

from insy_sensor_data.clustering.policy import ACTIVE_MODEL_POLICY
from insy_sensor_data.clustering.registry import model_run_readiness
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.revision import data_revision
from insy_sensor_data.store.schema import active_snapshot_table


CONTEXT_TABLES = (
    "cluster_model_runs",
    "cluster_drift_runs",
    "snapshot_revisions",
    "waites_ingestion_ledger",
)

WEB_METRICS = (
    {"key": "rms_vel", "label": "RMS Velocity", "axis": True, "unit": "in/s"},
    {"key": "rms_accel", "label": "RMS Acceleration", "axis": True, "unit": "m/s²"},
    {"key": "rms_pkpk", "label": "RMS Peak-to-Peak", "axis": True, "unit": "source"},
    {"key": "rms_cf", "label": "RMS Crest Factor", "axis": True, "unit": "ratio"},
    {"key": "impact", "label": "Impact", "axis": False, "unit": "m/s²"},
    {"key": "temp_sensor", "label": "Sensor Temperature", "axis": False, "unit": "°F"},
    {"key": "temp_ambient", "label": "Ambient Temperature", "axis": False, "unit": "°F"},
)


def browser_context(settings: AppSettings) -> dict[str, Any]:
    """Return only the service-owned choices needed to bootstrap the web app."""
    source = settings.source_mode
    with read_store(settings, required_tables=CONTEXT_TABLES) as connection:
        table = active_snapshot_table(connection)
        snapshots = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT
                    facts.source,
                    facts.source_date AS date,
                    COUNT(*) AS record_count,
                    MAX(facts.built_at) AS built_at,
                    MAX(revision.snapshot_revision) AS snapshot_revision
                FROM {table} AS facts
                LEFT JOIN snapshot_revisions AS revision
                  ON revision.source = facts.source
                 AND revision.source_date = facts.source_date
                WHERE facts.source = ?
                GROUP BY facts.source, facts.source_date
                ORDER BY facts.source_date
                """,
                (source,),
            ).fetchall()
        ]
        models = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    model_run_id,
                    source,
                    source_date AS date,
                    feature_space,
                    k,
                    status,
                    model_policy_version,
                    input_snapshot_revision,
                    completed_at
                FROM cluster_model_runs
                WHERE source = ?
                  AND feature_policy_version = ?
                  AND model_policy_version = ?
                  AND k = ?
                ORDER BY source_date, feature_space, created_at
                """,
                (
                    source,
                    ACTIVE_MODEL_POLICY.feature_policy_version,
                    ACTIVE_MODEL_POLICY.version,
                    ACTIVE_MODEL_POLICY.k,
                ),
            ).fetchall()
        ]
        revision = data_revision(connection, source)

    readiness, latest, _visible_models = _readiness(snapshots, models)
    dates = [
        {
            key: row.get(key)
            for key in (
                "date",
                "snapshot_ready",
                "registered_model_ready",
                "model_status",
                "snapshot_revision",
                "snapshot_built_at",
                "model_completed_at",
            )
        }
        for row in readiness
    ]
    timestamps = [
        str(value)
        for value in (
            revision.get("snapshot_built_at"),
            revision.get("ingestion_completed_at"),
            *(row.get("model_completed_at") for row in dates),
        )
        if value
    ]
    if not dates:
        sync_status = "not_synchronized"
    elif all(row.get("registered_model_ready") for row in dates):
        sync_status = "ready"
    else:
        sync_status = "model_pending"
    return {
        "source": source,
        "views": ["review", "trends", "cluster", "drift"],
        "dates": dates,
        "metrics": list(WEB_METRICS),
        "dimensions": ["x", "y", "z"],
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "synchronization": {
            "status": sync_status,
            "last_synchronized_at": max(timestamps, default=None),
            "data_revision": revision,
            "latest_readiness": latest,
        },
    }


def service_context(settings: AppSettings) -> dict[str, Any]:
    """Return the active SQLite model policy and per-date web readiness."""
    source = settings.source_mode
    with read_store(settings, required_tables=CONTEXT_TABLES) as connection:
        table = active_snapshot_table(connection)
        snapshots = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT
                    facts.source,
                    facts.source_date AS date,
                    COUNT(*) AS record_count,
                    MAX(facts.built_at) AS built_at,
                    MAX(revision.snapshot_revision) AS snapshot_revision
                FROM {table} AS facts
                LEFT JOIN snapshot_revisions AS revision
                  ON revision.source = facts.source
                 AND revision.source_date = facts.source_date
                WHERE facts.source = ?
                GROUP BY facts.source, facts.source_date
                ORDER BY facts.source_date
                """,
                (source,),
            ).fetchall()
        ]
        models = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    model_run_id,
                    source,
                    source_date AS date,
                    feature_space,
                    k,
                    status,
                    model_policy_version,
                    input_snapshot_revision,
                    created_at,
                    completed_at,
                    input_snapshot_row_count,
                    feature_row_count,
                    metrics_json,
                    warnings_json
                FROM cluster_model_runs
                WHERE source = ?
                  AND feature_policy_version = ?
                  AND model_policy_version = ?
                  AND k = ?
                ORDER BY source_date, feature_space, created_at
                """,
                (
                    source,
                    ACTIVE_MODEL_POLICY.feature_policy_version,
                    ACTIVE_MODEL_POLICY.version,
                    ACTIVE_MODEL_POLICY.k,
                ),
            ).fetchall()
        ]
        drift = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    drift_run_id,
                    source,
                    from_date,
                    to_date,
                    feature_space,
                    k,
                    status,
                    model_policy_version,
                    from_snapshot_revision,
                    to_snapshot_revision,
                    created_at
                FROM cluster_drift_runs
                WHERE source = ?
                  AND model_policy_version = ?
                  AND k = ?
                ORDER BY from_date, to_date, feature_space
                """,
                (source, ACTIVE_MODEL_POLICY.version, ACTIVE_MODEL_POLICY.k),
            ).fetchall()
        ]
        revision = data_revision(connection, source)

    readiness, latest, visible_models = _readiness(snapshots, models)
    trends = _snapshot_ranges(snapshots)
    cluster_windows = _registered_windows(readiness)
    return {
        "sources": [source] if snapshots or models or drift else [],
        "dimensions": sorted(
            {spec.dimension for spec in ACTIVE_MODEL_POLICY.feature_spaces}
        ),
        "feature_spaces": [spec.name for spec in ACTIVE_MODEL_POLICY.feature_spaces],
        "ks": [ACTIVE_MODEL_POLICY.k],
        "active_model_policy": ACTIVE_MODEL_POLICY.public_payload(),
        "snapshots": snapshots,
        "trends": trends,
        "clusters": [],
        "cluster_models": visible_models,
        "drift": drift,
        "cluster_windows": cluster_windows,
        "readiness": readiness,
        "latest_readiness": {source: latest} if snapshots or models else {},
        "data_revisions": {source: revision},
        "counts": {
            "snapshots": len(snapshots),
            "trends": len(trends),
            "clusters": 0,
            "cluster_models": len(models),
            "drift": len(drift),
            "cluster_windows": len(cluster_windows),
        },
    }


def operational_dates(settings: AppSettings) -> dict[str, Any]:
    with read_store(
        settings,
        required_tables=("waites_ingestion_ledger",),
    ) as connection:
        table = active_snapshot_table(connection)
        snapshot_dates = [
            str(row["source_date"])
            for row in connection.execute(
                f"""
                SELECT DISTINCT source_date
                FROM {table}
                WHERE source = ?
                ORDER BY source_date
                """,
                (settings.source_mode,),
            ).fetchall()
        ]
        raw_dates = [
            str(row["source_date"])
            for row in connection.execute(
                """
                SELECT DISTINCT source_date
                FROM waites_ingestion_ledger
                WHERE source = ?
                ORDER BY source_date
                """,
                (settings.source_mode,),
            ).fetchall()
        ]
        revision = data_revision(connection, settings.source_mode)
    trends = (
        [{"start_date": snapshot_dates[0], "end_date": snapshot_dates[-1]}]
        if snapshot_dates
        else []
    )
    return {
        "raw_waites": raw_dates,
        "snapshots": snapshot_dates,
        "trends": trends,
        "data_revision": revision,
    }


def _readiness(
    snapshots: list[dict[str, Any]],
    models: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str | None], list[dict[str, Any]]]:
    models_by_key = {
        (str(model["date"]), str(model["feature_space"])): model
        for model in models
    }
    visible_models: list[dict[str, Any]] = []
    readiness: list[dict[str, Any]] = []
    for snapshot in snapshots:
        date_value = str(snapshot["date"])
        feature_readiness = []
        completed_at: str | None = None
        for spec in ACTIVE_MODEL_POLICY.feature_spaces:
            model = models_by_key.get((date_value, spec.name))
            result = model_run_readiness(model, snapshot.get("snapshot_revision"))
            feature_row = {
                "feature_space": spec.name,
                "dimension": spec.dimension,
                "model_run_id": model.get("model_run_id") if model else None,
                **result,
            }
            feature_readiness.append(feature_row)
            if model is not None:
                visible_models.append(
                    {
                        **model,
                        "readiness_status": result["status"],
                        "readiness": result,
                    }
                )
                model_completed = model.get("completed_at")
                if model_completed and (
                    completed_at is None or str(model_completed) > completed_at
                ):
                    completed_at = str(model_completed)
        statuses = {str(row["status"]) for row in feature_readiness}
        overall = (
            "ready"
            if statuses == {"ready"}
            else next(
                candidate
                for candidate in (
                    "failed",
                    "insufficient_data",
                    "stale",
                    "missing",
                )
                if candidate in statuses
            )
        )
        readiness.append(
            {
                "source": str(snapshot["source"]),
                "date": date_value,
                "snapshot_ready": True,
                "registered_model_ready": overall == "ready",
                "model_status": overall,
                "ready_model_count": sum(
                    1 for row in feature_readiness if row["status"] == "ready"
                ),
                "required_model_count": len(ACTIVE_MODEL_POLICY.feature_spaces),
                "complete_model_count": sum(
                    1
                    for spec in ACTIVE_MODEL_POLICY.feature_spaces
                    if (models_by_key.get((date_value, spec.name)) or {}).get("status")
                    == "complete"
                ),
                "feature_spaces": [
                    row["feature_space"]
                    for row in feature_readiness
                    if row["status"] == "ready"
                ],
                "ks": [ACTIVE_MODEL_POLICY.k] if overall == "ready" else [],
                "snapshot_revision": snapshot.get("snapshot_revision"),
                "snapshot_built_at": snapshot.get("built_at"),
                "model_completed_at": completed_at,
                "feature_readiness": feature_readiness,
            }
        )
    return (
        readiness,
        {
            "snapshot_date": max(
                (row["date"] for row in readiness if row["snapshot_ready"]),
                default=None,
            ),
            "registered_model_date": max(
                (
                    row["date"]
                    for row in readiness
                    if row["registered_model_ready"]
                ),
                default=None,
            ),
            "fully_ready_date": max(
                (
                    row["date"]
                    for row in readiness
                    if row["snapshot_ready"] and row["registered_model_ready"]
                ),
                default=None,
            ),
        },
        visible_models,
    )


def _snapshot_ranges(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates_by_source: dict[str, list[str]] = defaultdict(list)
    for snapshot in snapshots:
        dates_by_source[str(snapshot["source"])].append(str(snapshot["date"]))
    return [
        {
            "source": source,
            "start_date": min(dates),
            "end_date": max(dates),
            "input_mode": "sqlite",
        }
        for source, dates in sorted(dates_by_source.items())
        if dates
    ]


def _registered_windows(readiness: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for spec in ACTIVE_MODEL_POLICY.feature_spaces:
        ready_dates = [
            str(row["date"])
            for row in readiness
            if any(
                feature["feature_space"] == spec.name
                and feature["status"] == "ready"
                for feature in row["feature_readiness"]
            )
        ]
        if not ready_dates:
            continue
        output.append(
            {
                "source": str(readiness[0]["source"]),
                "feature_space": spec.name,
                "k": ACTIVE_MODEL_POLICY.k,
                "model_policy_version": ACTIVE_MODEL_POLICY.version,
                "start_date": min(ready_dates),
                "end_date": max(ready_dates),
                "date_count": len(ready_dates),
                "required_date_count": len(readiness),
                "status": (
                    "complete"
                    if len(ready_dates) == len(readiness)
                    else "partial"
                ),
                "registered": True,
            }
        )
    return output

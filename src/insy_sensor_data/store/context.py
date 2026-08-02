from __future__ import annotations

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

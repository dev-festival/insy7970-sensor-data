from __future__ import annotations

from collections import defaultdict
from typing import Any

from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.revision import data_revision


CONTEXT_TABLES = (
    "sensor_daily_snapshots",
    "cluster_model_runs",
    "cluster_drift_runs",
    "waites_ingestion_ledger",
)


def service_context(settings: AppSettings) -> dict[str, Any]:
    """Return SQLite-backed bootstrap context for the web application."""
    with read_store(settings, required_tables=CONTEXT_TABLES) as connection:
        snapshots = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    source,
                    source_date AS date,
                    COUNT(*) AS record_count,
                    MAX(built_at) AS built_at
                FROM sensor_daily_snapshots
                GROUP BY source, source_date
                ORDER BY source, source_date
                """
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
                    created_at,
                    completed_at,
                    input_snapshot_row_count,
                    feature_row_count
                FROM cluster_model_runs
                ORDER BY source, source_date, feature_space, k, created_at
                """
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
                    created_at
                FROM cluster_drift_runs
                ORDER BY source, from_date, to_date, feature_space, k
                """
            ).fetchall()
        ]
        sources = sorted(
            {
                str(row["source"])
                for row in [*snapshots, *models, *drift]
                if row.get("source")
            }
        )
        revisions = {
            source: data_revision(connection, source)
            for source in sources
        }
    readiness, latest = _readiness(snapshots, models)
    complete_models = [row for row in models if row["status"] == "complete"]
    feature_spaces = sorted(
        {str(row["feature_space"]) for row in complete_models}
    )
    dimensions = sorted(
        {
            dimension
            for dimension in (
                _feature_space_dimension(row["feature_space"])
                for row in complete_models
            )
            if dimension
        }
    )
    ks = sorted({int(row["k"]) for row in complete_models})
    trends = _snapshot_ranges(snapshots)
    cluster_windows = _registered_windows(complete_models)
    return {
        "sources": sources,
        "dimensions": dimensions,
        "feature_spaces": feature_spaces,
        "ks": ks,
        "snapshots": snapshots,
        "trends": trends,
        "clusters": [],
        "cluster_models": models,
        "drift": drift,
        "cluster_windows": cluster_windows,
        "readiness": readiness,
        "latest_readiness": latest,
        "data_revisions": revisions,
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
        required_tables=("sensor_daily_snapshots", "waites_ingestion_ledger"),
    ) as connection:
        snapshot_dates = [
            str(row["source_date"])
            for row in connection.execute(
                """
                SELECT DISTINCT source_date
                FROM sensor_daily_snapshots
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
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str | None]]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in snapshots:
        key = (str(snapshot["source"]), str(snapshot["date"]))
        by_key[key] = {
            "source": key[0],
            "date": key[1],
            "snapshot_ready": True,
            "registered_model_ready": False,
            "complete_model_count": 0,
            "feature_spaces": [],
            "ks": [],
            "snapshot_built_at": snapshot.get("built_at"),
            "model_completed_at": None,
        }
    for model in models:
        if model["status"] != "complete":
            continue
        key = (str(model["source"]), str(model["date"]))
        row = by_key.setdefault(
            key,
            {
                "source": key[0],
                "date": key[1],
                "snapshot_ready": False,
                "registered_model_ready": False,
                "complete_model_count": 0,
                "feature_spaces": [],
                "ks": [],
                "snapshot_built_at": None,
                "model_completed_at": None,
            },
        )
        row["registered_model_ready"] = True
        row["complete_model_count"] += 1
        if model["feature_space"] not in row["feature_spaces"]:
            row["feature_spaces"].append(model["feature_space"])
        selected_k = int(model["k"])
        if selected_k not in row["ks"]:
            row["ks"].append(selected_k)
        completed_at = model.get("completed_at")
        if completed_at and (
            row["model_completed_at"] is None
            or completed_at > row["model_completed_at"]
        ):
            row["model_completed_at"] = completed_at
    readiness = sorted(
        by_key.values(),
        key=lambda row: (row["source"], row["date"]),
    )
    for row in readiness:
        row["feature_spaces"].sort()
        row["ks"].sort()
    latest: dict[str, dict[str, str | None]] = {}
    for source in sorted({row["source"] for row in readiness}):
        source_rows = [row for row in readiness if row["source"] == source]
        latest[source] = {
            "snapshot_date": max(
                (
                    row["date"]
                    for row in source_rows
                    if row["snapshot_ready"]
                ),
                default=None,
            ),
            "registered_model_date": max(
                (
                    row["date"]
                    for row in source_rows
                    if row["registered_model_ready"]
                ),
                default=None,
            ),
            "fully_ready_date": max(
                (
                    row["date"]
                    for row in source_rows
                    if row["snapshot_ready"]
                    and row["registered_model_ready"]
                ),
                default=None,
            ),
        }
    return readiness, latest


def _snapshot_ranges(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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


def _registered_windows(
    models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for model in models:
        grouped[
            (
                str(model["source"]),
                str(model["feature_space"]),
                int(model["k"]),
            )
        ].append(str(model["date"]))
    return [
        {
            "source": source,
            "feature_space": feature_space,
            "k": k,
            "start_date": min(dates),
            "end_date": max(dates),
            "date_count": len(set(dates)),
            "registered": True,
        }
        for (source, feature_space, k), dates in sorted(grouped.items())
        if dates
    ]


def _feature_space_dimension(feature_space: Any) -> str | None:
    value = str(feature_space or "")
    if not value:
        return None
    prefix = value.split("_", 1)[0]
    return prefix if prefix in {"x", "y", "z"} else None

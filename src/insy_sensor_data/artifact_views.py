from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import re
import sqlite3

from insy_sensor_data.artifacts import read_csv_rows, read_json
from insy_sensor_data.clustering.features import DIMENSIONS
from insy_sensor_data.clustering.registry import (
    list_registered_cluster_models,
    load_registered_cluster_view,
    load_registered_cluster_window_view,
    load_registered_drift_view,
)
from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.snapshots.build import list_snapshot_dates, load_snapshot
from insy_sensor_data.snapshots.trends import list_trend_ranges, load_trends, query_sqlite_trends
from insy_sensor_data.storage import get_storage_paths


CLUSTER_RE = re.compile(
    r"^date=(?P<date>\d{4}-\d{2}-\d{2})_"
    r"source=(?P<source>[^_]+)_"
    r"dimension=(?P<dimension>[^_]+)_"
    r"k=(?P<k>\d+)$"
)
DRIFT_RE = re.compile(
    r"^from=(?P<from_date>\d{4}-\d{2}-\d{2})_"
    r"to=(?P<to_date>\d{4}-\d{2}-\d{2})_"
    r"source=(?P<source>[^_]+)_"
    r"dimension=(?P<dimension>[^_]+)_"
    r"k=(?P<k>\d+)$"
)
WINDOW_RE = re.compile(
    r"^start=(?P<start_date>\d{4}-\d{2}-\d{2})_"
    r"end=(?P<end_date>\d{4}-\d{2}-\d{2})_"
    r"source=(?P<source>[^_]+)_"
    r"dimension=(?P<dimension>[^_]+)_"
    r"k=(?P<k>\d+)$"
)
VALID_SCOPE_TYPES = {"all", "asset_tree", "equipment", "sensor"}
AXIS_METRICS = {"rms_vel", "rms_accel", "rms_pkpk", "rms_cf"}
NON_AXIS_METRICS = {"impact", "temp_sensor", "temp_ambient"}
SNAPSHOT_REVIEW_BASE_COLUMNS = [
    "sensor_name",
    "installation_point_id",
    "asset_number",
    "rms_vel_mean_x",
    "rms_vel_mean_y",
    "rms_vel_mean_z",
    "rms_accel_mean_x",
    "rms_accel_mean_y",
    "rms_accel_mean_z",
    "impact_mean",
    "temp_sensor_mean",
]


def discover_artifacts(settings: AppSettings) -> dict[str, Any]:
    storage = get_storage_paths(settings.data_dir)
    snapshots = _snapshot_artifacts(settings)
    trends = _trend_artifacts(settings)
    clusters = _cluster_artifacts(storage.clusters_dir)
    drift = _drift_artifacts(storage.drift_dir)
    cluster_windows = _cluster_window_artifacts(storage.cluster_windows_dir)
    cluster_models = list_registered_cluster_models(settings)["models"]
    sources = sorted(
        {
            str(row["source"])
            for group in [snapshots, trends, clusters, drift, cluster_windows, cluster_models]
            for row in group
            if row.get("source")
        }
    )
    dimensions = sorted(
        {
            str(row["dimension"])
            for group in [clusters, drift, cluster_windows]
            for row in group
            if row.get("dimension")
        }
    )
    feature_spaces = sorted(
        {
            str(row["feature_space"])
            for row in cluster_models
            if row.get("feature_space") and row.get("status") == "complete"
        }
    )
    ks = sorted(
        {
            int(row["k"])
            for group in [clusters, drift, cluster_windows, cluster_models]
            for row in group
            if row.get("k") is not None
        }
    )
    return {
        "sources": sources,
        "dimensions": dimensions,
        "feature_spaces": feature_spaces,
        "ks": ks,
        "snapshots": snapshots,
        "trends": trends,
        "clusters": clusters,
        "cluster_models": cluster_models,
        "drift": drift,
        "cluster_windows": cluster_windows,
        "counts": {
            "snapshots": len(snapshots),
            "trends": len(trends),
            "clusters": len(clusters),
            "cluster_models": len(cluster_models),
            "drift": len(drift),
            "cluster_windows": len(cluster_windows),
        },
    }


def list_equipment_view(
    settings: AppSettings,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    source_mode = _optional_source(source)
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot_date in list_snapshot_dates(settings):
        parsed_snapshot_date = date.fromisoformat(snapshot_date)
        if start_date is not None and parsed_snapshot_date < start_date:
            continue
        if end_date is not None and parsed_snapshot_date > end_date:
            continue
        payload = load_snapshot(settings, date.fromisoformat(snapshot_date))
        snapshot_source = str(payload["metadata"].get("source") or "")
        if source_mode is not None and snapshot_source != source_mode:
            continue
        for row in payload["rows"]:
            equipment_id = str(row.get("equipment_id") or "")
            key = (snapshot_source, equipment_id)
            equipment = grouped.setdefault(
                key,
                {
                    "source": snapshot_source,
                    "equipment_id": equipment_id,
                    "equipment_name": row.get("equipment_name") or "",
                    "customer_asset_id": row.get("customer_asset_id") or "",
                    "sensor_count": 0,
                    "installation_point_ids": set(),
                    "dates": set(),
                },
            )
            installation_point_id = str(row.get("installation_point_id") or "")
            if installation_point_id:
                equipment["installation_point_ids"].add(installation_point_id)
            equipment["dates"].add(snapshot_date)
            if not equipment["equipment_name"] and row.get("equipment_name"):
                equipment["equipment_name"] = row.get("equipment_name")
            if not equipment["customer_asset_id"] and row.get("customer_asset_id"):
                equipment["customer_asset_id"] = row.get("customer_asset_id")

    rows = []
    for equipment in grouped.values():
        installation_ids = sorted(equipment["installation_point_ids"], key=_sort_key)
        dates = sorted(equipment["dates"])
        rows.append(
            {
                "source": equipment["source"],
                "equipment_id": equipment["equipment_id"],
                "equipment_name": equipment["equipment_name"],
                "customer_asset_id": equipment["customer_asset_id"],
                "sensor_count": len(installation_ids),
                "installation_point_ids": installation_ids,
                "dates": dates,
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "date_count": len(dates),
            }
        )
    return {
        "source": source_mode,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "count": len(rows),
        "rows": sorted(rows, key=lambda row: (_sort_key(row["equipment_id"]), row["source"])),
    }


def list_equipment_tree_view(
    settings: AppSettings,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    source_mode = _optional_source(source)
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    references = _load_reference_indexes(settings, source_mode)
    active_equipment = _active_equipment_from_snapshots(
        settings,
        source_mode=source_mode,
        start_date=start_date,
        end_date=end_date,
    )
    tree_map: dict[str, dict[str, Any]] = {}
    equipment_count = 0
    sensor_count = 0

    for equipment_id, active in active_equipment.items():
        equipment_ref = references["equipment"].get(equipment_id, {})
        asset_tree_id = _text_id(equipment_ref.get("asset_tree_id")) or "unknown"
        asset_tree_ref = references["asset_trees"].get(asset_tree_id, {})
        tree = tree_map.setdefault(
            asset_tree_id,
            {
                "asset_tree_id": asset_tree_id,
                "asset_tree_name": _asset_tree_name(asset_tree_id, asset_tree_ref),
                "parent_asset_tree_id": _none_if_empty(
                    _text_id(asset_tree_ref.get("parent_asset_tree_id"))
                ),
                "facility_id": _none_if_empty(_text_id(asset_tree_ref.get("facility_id"))),
                "asset_tree_path": _clean_text(asset_tree_ref.get("asset_tree_path"))
                or _asset_tree_name(asset_tree_id, asset_tree_ref),
                "dates": set(),
                "equipment": [],
            },
        )

        equipment_dates = sorted(active["dates"])
        tree["dates"].update(equipment_dates)
        sensors = []
        for installation_point_id, sensor_active in sorted(
            active["sensors"].items(),
            key=lambda item: _sort_key(item[0]),
        ):
            sensor_ref = references["installation_points"].get(installation_point_id, {})
            sensor_dates = sorted(sensor_active["dates"])
            sensors.append(
                {
                    "installation_point_id": installation_point_id,
                    "installation_point_name": _clean_text(sensor_active.get("installation_point_name"))
                    or _clean_text(sensor_ref.get("name"))
                    or f"Sensor {installation_point_id}",
                    "sensor_id": _text_id(sensor_active.get("sensor_id"))
                    or _text_id(sensor_ref.get("sensor_id")),
                    "customer_asset_id": _clean_text(sensor_active.get("customer_asset_id"))
                    or _clean_text(sensor_ref.get("customer_asset_id")),
                    "active_dates": sensor_dates,
                    "first_date": sensor_dates[0] if sensor_dates else None,
                    "last_date": sensor_dates[-1] if sensor_dates else None,
                    "date_count": len(sensor_dates),
                }
            )
        sensor_count += len(sensors)
        equipment_count += 1
        tree["equipment"].append(
            {
                "equipment_id": equipment_id,
                "equipment_name": _clean_text(active.get("equipment_name"))
                or _clean_text(equipment_ref.get("name"))
                or f"Equipment {equipment_id}",
                "customer_asset_id": _clean_text(active.get("customer_asset_id"))
                or _clean_text(equipment_ref.get("customer_asset_id")),
                "asset_tree_id": asset_tree_id,
                "active_dates": equipment_dates,
                "first_date": equipment_dates[0] if equipment_dates else None,
                "last_date": equipment_dates[-1] if equipment_dates else None,
                "date_count": len(equipment_dates),
                "sensor_count": len(sensors),
                "sensors": sensors,
            }
        )

    asset_trees = []
    for tree in tree_map.values():
        dates = sorted(tree.pop("dates"))
        tree["equipment"].sort(key=lambda row: (_sort_key(row["equipment_id"]), row["equipment_name"]))
        tree["equipment_count"] = len(tree["equipment"])
        tree["sensor_count"] = sum(len(row["sensors"]) for row in tree["equipment"])
        tree["active_dates"] = dates
        tree["first_date"] = dates[0] if dates else None
        tree["last_date"] = dates[-1] if dates else None
        tree["date_count"] = len(dates)
        asset_trees.append(tree)

    return {
        "source": source_mode,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "asset_tree_count": len(asset_trees),
        "equipment_count": equipment_count,
        "sensor_count": sensor_count,
        "asset_trees": sorted(
            asset_trees,
            key=lambda row: (_sort_key(row["asset_tree_id"]), row["asset_tree_name"]),
        ),
    }


def load_snapshot_view(
    settings: AppSettings,
    run_date: date,
    source: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    source_mode = _optional_source(source)
    payload = load_snapshot(settings, run_date)
    snapshot_source = str(payload["metadata"].get("source") or "")
    if source_mode is not None and snapshot_source != source_mode:
        raise FileNotFoundError(
            f"Missing snapshot artifact for source {source_mode} date {run_date.isoformat()}."
        )
    rows = _filter_rows(
        payload["rows"],
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        customer_asset_id=customer_asset_id,
    )
    return {
        **payload,
        "source": snapshot_source,
        "row_count": len(payload["rows"]),
        "filtered_row_count": len(rows),
        "filters": _filters(
            source=source_mode,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            customer_asset_id=customer_asset_id,
        ),
        "rows": rows,
    }


def load_trend_view(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str | None = None,
    scope: str = "all",
    asset_tree_id: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
    customer_asset_id: str | None = None,
    metric: str = "rms_vel",
    dimension: str = "x",
    stat: str = "mean",
) -> dict[str, Any]:
    source_mode = _optional_source(source)
    resolved_source = source_mode or settings.source_mode
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    selected_metric = _normalize_metric(metric)
    selected_dimension = _normalize_dimension_for_metric(selected_metric, dimension)
    selected_stat = _normalize_stat(stat)
    value_field = _metric_field(selected_metric, selected_stat, selected_dimension)
    try:
        payload = query_sqlite_trends(
            settings=settings,
            start_date=start_date,
            end_date=end_date,
            source=resolved_source,
        )
        trend_source = resolved_source
        input_mode = "sqlite"
    except FileNotFoundError:
        payload = load_trends(settings, start_date, end_date)
        trend_source = str(payload["metadata"].get("source") or "")
        if source_mode is not None and trend_source != source_mode:
            raise FileNotFoundError(
                f"Missing trend data for source {source_mode} range "
                f"{start_date.isoformat()} to {end_date.isoformat()}."
            )
        resolved_source = trend_source or resolved_source
        input_mode = "artifact_fallback"

    scope_context = _resolve_review_scope(
        settings=settings,
        source=resolved_source,
        start_date=start_date,
        end_date=end_date,
        scope=scope,
        asset_tree_id=asset_tree_id,
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        sensor_id=sensor_id,
    )
    trend_source = str(payload["metadata"].get("source") or "")
    sensor_base_rows = _filter_rows_for_review_scope(payload["sensor_rows"], scope_context)
    equipment_base_rows = _filter_rows_for_review_scope(payload["equipment_rows"], scope_context)
    sensor_rows = _filter_rows(
        sensor_base_rows,
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        sensor_id=sensor_id,
        customer_asset_id=customer_asset_id,
    )
    equipment_rows = _filter_rows(
        equipment_base_rows,
        equipment_id=equipment_id,
        customer_asset_id=customer_asset_id,
    )
    if installation_point_id or sensor_id:
        selected_equipment_dates = {
            (str(row.get("date") or ""), str(row.get("equipment_id") or ""))
            for row in sensor_rows
            if row.get("date") and row.get("equipment_id")
        }
        equipment_rows = [
            row
            for row in equipment_rows
            if (str(row.get("date") or ""), str(row.get("equipment_id") or "")) in selected_equipment_dates
        ]
    metadata = {
        **payload.get("metadata", {}),
        "input_mode": input_mode,
        "served_from": input_mode,
        "metric": selected_metric,
        "dimension": selected_dimension,
        "stat": selected_stat,
        "value_field": value_field,
    }
    return {
        **payload,
        "source": trend_source or resolved_source,
        "input": input_mode,
        "input_mode": input_mode,
        "metric": selected_metric,
        "dimension": selected_dimension,
        "stat": selected_stat,
        "value_field": value_field,
        "metadata": metadata,
        "scope": _public_scope(scope_context),
        "sensor_row_count": len(payload["sensor_rows"]),
        "filtered_sensor_row_count": len(sensor_rows),
        "equipment_row_count": len(payload["equipment_rows"]),
        "filtered_equipment_row_count": len(equipment_rows),
        "filters": _filters(
            source=source_mode,
            scope=scope,
            asset_tree_id=asset_tree_id,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
            sensor_id=sensor_id,
            customer_asset_id=customer_asset_id,
        ),
        "sensor_rows": sensor_rows,
        "equipment_rows": equipment_rows,
    }


def load_cluster_view(
    settings: AppSettings,
    run_date: date,
    source: str,
    dimension: str,
    k: int,
    feature_space: str | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    if feature_space:
        return load_registered_cluster_view(
            settings=settings,
            run_date=run_date,
            source=source_mode,
            feature_space=feature_space,
            k=_validate_k(k),
        )
    cluster_dimension = _validate_dimension(dimension)
    cluster_k = _validate_k(k)
    storage = get_storage_paths(settings.data_dir)
    cluster_dir = storage.clusters_dir / _cluster_dir_name(run_date, source_mode, cluster_dimension, cluster_k)
    metrics = read_json(cluster_dir / "metrics.json")
    rows = read_csv_rows(cluster_dir / "sensor_clusters.csv")
    cluster_rows = read_csv_rows(cluster_dir / "cluster_summary.csv")
    pca_rows = read_csv_rows(cluster_dir / "pca_coordinates.csv")
    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "dimension": cluster_dimension,
        "k": cluster_k,
        "artifact_dir": cluster_dir.as_posix(),
        "metrics": metrics,
        "row_count": len(rows),
        "cluster_row_count": len(cluster_rows),
        "pca_row_count": len(pca_rows),
        "rows": rows,
        "cluster_rows": cluster_rows,
        "pca_rows": pca_rows,
    }


def load_drift_view(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    dimension: str,
    k: int,
    feature_space: str | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    if feature_space:
        return load_registered_drift_view(
            settings=settings,
            from_date=from_date,
            to_date=to_date,
            source=source_mode,
            feature_space=feature_space,
            k=_validate_k(k),
        )
    cluster_dimension = _validate_dimension(dimension)
    cluster_k = _validate_k(k)
    if to_date < from_date:
        raise ValueError("to_date must be on or after from_date")
    storage = get_storage_paths(settings.data_dir)
    drift_dir = storage.drift_dir / _drift_dir_name(from_date, to_date, source_mode, cluster_dimension, cluster_k)
    metrics = read_json(drift_dir / "metrics.json")
    aligned_metrics_path = drift_dir / "aligned_metrics.json"
    aligned_metrics = read_json(aligned_metrics_path) if aligned_metrics_path.exists() else None
    return {
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "dimension": cluster_dimension,
        "k": cluster_k,
        "artifact_dir": drift_dir.as_posix(),
        "metrics": metrics,
        "aligned_metrics": aligned_metrics,
        "raw_rows": read_csv_rows(drift_dir / "cluster_drift.csv"),
        "centroid_rows": read_csv_rows(drift_dir / "centroid_drift.csv"),
        "aligned_rows": _optional_csv_rows(drift_dir / "aligned_cluster_drift.csv"),
        "alignment_rows": _optional_csv_rows(drift_dir / "centroid_alignment.csv"),
    }


def load_cluster_window_view(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    dimension: str,
    k: int,
    feature_space: str | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    if feature_space:
        return load_registered_cluster_window_view(
            settings=settings,
            start_date=start_date,
            end_date=end_date,
            source=source_mode,
            feature_space=feature_space,
            k=_validate_k(k),
        )
    cluster_dimension = _validate_dimension(dimension)
    cluster_k = _validate_k(k)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    storage = get_storage_paths(settings.data_dir)
    window_dir = (
        storage.cluster_windows_dir
        / _window_dir_name(start_date, end_date, source_mode, cluster_dimension, cluster_k)
    )
    metrics = read_json(window_dir / "metrics.json")
    return {
        "source": source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "dimension": cluster_dimension,
        "k": cluster_k,
        "artifact_dir": window_dir.as_posix(),
        "metrics": metrics,
        "window_rows": read_csv_rows(window_dir / "window_summary.csv"),
        "quality_rows": read_csv_rows(window_dir / "quality_summary.csv"),
        "aligned_drift_rows": read_csv_rows(window_dir / "aligned_drift_summary.csv"),
        "alignment_rows": read_csv_rows(window_dir / "centroid_alignment.csv"),
    }


def load_snapshot_review_view(
    settings: AppSettings,
    run_date: date,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    scope: str = "all",
    asset_tree_id: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
    metric: str = "rms_vel",
    dimension: str = "x",
    feature_space: str | None = None,
    stat: str = "mean",
    k: int = 4,
) -> dict[str, Any]:
    source_mode = _optional_source(source)
    effective_start = start_date or run_date
    effective_end = end_date or run_date
    if effective_end < effective_start:
        raise ValueError("end_date must be on or after start_date")

    payload = load_snapshot(settings, run_date)
    snapshot_source = str(payload["metadata"].get("source") or "")
    if source_mode is not None and snapshot_source != source_mode:
        raise FileNotFoundError(
            f"Missing snapshot artifact for source {source_mode} date {run_date.isoformat()}."
        )
    resolved_source = snapshot_source or source_mode or ""
    scope_context = _resolve_review_scope(
        settings=settings,
        source=resolved_source,
        start_date=effective_start,
        end_date=effective_end,
        scope=scope,
        asset_tree_id=asset_tree_id,
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        sensor_id=sensor_id,
    )
    snapshot_rows = payload["rows"]
    scoped_rows = _filter_rows_for_review_scope(snapshot_rows, scope_context)
    selected_metric = _normalize_metric(metric)
    selected_dimension = _normalize_dimension_for_metric(selected_metric, dimension)
    selected_stat = _normalize_stat(stat)
    cluster_dimension = _cluster_dimension_from_feature_space(feature_space, selected_dimension)
    return {
        "source": resolved_source,
        "date": run_date.isoformat(),
        "start_date": effective_start.isoformat(),
        "end_date": effective_end.isoformat(),
        "scope": _public_scope(scope_context),
        "context": _snapshot_review_context(scope_context, scoped_rows, snapshot_rows),
        "trend": _snapshot_review_trend(
            settings=settings,
            start_date=effective_start,
            end_date=effective_end,
            source=resolved_source,
            scope_context=scope_context,
            metric=selected_metric,
            dimension=selected_dimension,
            stat=selected_stat,
        ),
        "cluster_context": _snapshot_review_cluster_context(
            settings=settings,
            run_date=run_date,
            source=resolved_source,
            dimension=cluster_dimension,
            feature_space=feature_space,
            k=k,
            scope_context=scope_context,
        ),
        "events": _snapshot_review_events(
            settings=settings,
            run_date=run_date,
            source=resolved_source,
            scope_context=scope_context,
            snapshot_rows=snapshot_rows,
        ),
        "measurements": _snapshot_review_measurements(
            scoped_rows,
            metric=selected_metric,
            dimension=selected_dimension,
            stat=selected_stat,
        ),
        "metadata": {
            "snapshot_row_count": len(snapshot_rows),
            "filtered_snapshot_row_count": len(scoped_rows),
            "metric": selected_metric,
            "dimension": selected_dimension,
            "stat": selected_stat,
            "cluster_dimension": cluster_dimension,
            "k": _validate_k(k),
        },
    }


def _snapshot_artifacts(settings: AppSettings) -> list[dict[str, Any]]:
    snapshots = []
    for snapshot_date in list_snapshot_dates(settings):
        try:
            payload = load_snapshot(settings, date.fromisoformat(snapshot_date))
        except (FileNotFoundError, ValueError):
            continue
        snapshots.append(
            {
                "date": snapshot_date,
                "source": payload["metadata"].get("source"),
                "row_count": payload["metadata"].get("record_count", len(payload["rows"])),
                "metadata_path": payload["metadata"].get("outputs", {}).get("metadata"),
            }
        )
    return snapshots


def _trend_artifacts(settings: AppSettings) -> list[dict[str, Any]]:
    trends = []
    for trend_range in list_trend_ranges(settings):
        try:
            start = date.fromisoformat(trend_range["start_date"])
            end = date.fromisoformat(trend_range["end_date"])
            payload = load_trends(settings, start, end)
        except (FileNotFoundError, ValueError):
            continue
        metadata = payload["metadata"]
        trends.append(
            {
                "start_date": trend_range["start_date"],
                "end_date": trend_range["end_date"],
                "source": metadata.get("source"),
                "input_mode": metadata.get("input_mode"),
                "sensor_record_count": metadata.get("sensor_record_count"),
                "equipment_record_count": metadata.get("equipment_record_count"),
            }
        )
    return trends


def _cluster_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts = []
    if not root.exists():
        return artifacts
    for path in sorted(root.glob("date=*_source=*_dimension=*_k=*")):
        match = CLUSTER_RE.match(path.name)
        if not match or not path.is_dir():
            continue
        metrics = _optional_json(path / "metrics.json")
        artifacts.append(
            {
                **_match_dict(match),
                "k": int(match.group("k")),
                "row_count": metrics.get("row_count") if metrics else None,
                "feature_count": metrics.get("feature_count") if metrics else None,
                "artifact_dir": path.as_posix(),
                "complete": all(
                    (path / name).exists()
                    for name in [
                        "sensor_clusters.csv",
                        "cluster_summary.csv",
                        "pca_coordinates.csv",
                        "metrics.json",
                    ]
                ),
            }
        )
    return artifacts


def _drift_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts = []
    if not root.exists():
        return artifacts
    for path in sorted(root.glob("from=*_to=*_source=*_dimension=*_k=*")):
        match = DRIFT_RE.match(path.name)
        if not match or not path.is_dir():
            continue
        metrics = _optional_json(path / "aligned_metrics.json") or _optional_json(path / "metrics.json")
        artifacts.append(
            {
                **_match_dict(match),
                "k": int(match.group("k")),
                "matched_sensor_count": metrics.get("matched_sensor_count") if metrics else None,
                "aligned_changed_count": metrics.get("aligned_changed_count") if metrics else None,
                "artifact_dir": path.as_posix(),
                "complete": all(
                    (path / name).exists()
                    for name in ["cluster_drift.csv", "centroid_drift.csv", "metrics.json"]
                ),
                "aligned": all(
                    (path / name).exists()
                    for name in ["aligned_cluster_drift.csv", "centroid_alignment.csv", "aligned_metrics.json"]
                ),
            }
        )
    return artifacts


def _cluster_window_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts = []
    if not root.exists():
        return artifacts
    for path in sorted(root.glob("start=*_end=*_source=*_dimension=*_k=*")):
        match = WINDOW_RE.match(path.name)
        if not match or not path.is_dir():
            continue
        metrics = _optional_json(path / "metrics.json")
        artifacts.append(
            {
                **_match_dict(match),
                "k": int(match.group("k")),
                "date_count": metrics.get("date_count") if metrics else None,
                "pair_count": metrics.get("pair_count") if metrics else None,
                "warning_count": metrics.get("warning_count") if metrics else None,
                "artifact_dir": path.as_posix(),
                "complete": all(
                    (path / name).exists()
                    for name in [
                        "window_summary.csv",
                        "quality_summary.csv",
                        "aligned_drift_summary.csv",
                        "centroid_alignment.csv",
                        "metrics.json",
                    ]
                ),
            }
        )
    return artifacts


def _resolve_review_scope(
    settings: AppSettings,
    source: str,
    start_date: date,
    end_date: date,
    scope: str,
    asset_tree_id: str | None,
    equipment_id: str | None,
    installation_point_id: str | None,
    sensor_id: str | None,
) -> dict[str, Any]:
    scope_type = scope if scope in VALID_SCOPE_TYPES else "all"
    context: dict[str, Any] = {
        "type": scope_type,
        "asset_tree_id": _text_id(asset_tree_id),
        "equipment_id": _text_id(equipment_id),
        "installation_point_id": _text_id(installation_point_id),
        "sensor_id": _text_id(sensor_id),
        "label": "All equipment",
        "equipment_ids": set(),
        "installation_point_ids": set(),
    }
    if scope_type == "all":
        return context

    try:
        tree_payload = list_equipment_tree_view(
            settings=settings,
            source=source,
            start_date=start_date,
            end_date=end_date,
        )
    except (FileNotFoundError, ValueError):
        tree_payload = {"asset_trees": []}

    for asset_tree in tree_payload.get("asset_trees", []):
        if scope_type == "asset_tree" and asset_tree.get("asset_tree_id") == context["asset_tree_id"]:
            context["label"] = asset_tree.get("asset_tree_name") or f"Asset Tree {context['asset_tree_id']}"
            context["equipment_ids"] = {
                _text_id(row.get("equipment_id"))
                for row in asset_tree.get("equipment", [])
                if _text_id(row.get("equipment_id"))
            }
            context["installation_point_ids"] = {
                _text_id(sensor.get("installation_point_id"))
                for row in asset_tree.get("equipment", [])
                for sensor in row.get("sensors", [])
                if _text_id(sensor.get("installation_point_id"))
            }
            return context
        for equipment in asset_tree.get("equipment", []):
            equipment_id_value = _text_id(equipment.get("equipment_id"))
            if scope_type == "equipment" and equipment_id_value == context["equipment_id"]:
                context.update(
                    {
                        "asset_tree_id": _text_id(asset_tree.get("asset_tree_id")),
                        "equipment_id": equipment_id_value,
                        "equipment_name": equipment.get("equipment_name") or "",
                        "customer_asset_id": equipment.get("customer_asset_id") or "",
                        "label": equipment.get("equipment_name") or f"Equipment {equipment_id_value}",
                        "equipment_ids": {equipment_id_value} if equipment_id_value else set(),
                        "installation_point_ids": {
                            _text_id(sensor.get("installation_point_id"))
                            for sensor in equipment.get("sensors", [])
                            if _text_id(sensor.get("installation_point_id"))
                        },
                    }
                )
                return context
            for sensor in equipment.get("sensors", []):
                installation_id = _text_id(sensor.get("installation_point_id"))
                sensor_id_value = _text_id(sensor.get("sensor_id"))
                if scope_type == "sensor" and (
                    installation_id == context["installation_point_id"]
                    or (context["sensor_id"] and sensor_id_value == context["sensor_id"])
                ):
                    context.update(
                        {
                            "asset_tree_id": _text_id(asset_tree.get("asset_tree_id")),
                            "equipment_id": equipment_id_value,
                            "installation_point_id": installation_id,
                            "sensor_id": sensor_id_value,
                            "equipment_name": equipment.get("equipment_name") or "",
                            "customer_asset_id": sensor.get("customer_asset_id")
                            or equipment.get("customer_asset_id")
                            or "",
                            "sensor_name": sensor.get("installation_point_name") or "",
                            "label": sensor.get("installation_point_name") or f"Sensor {installation_id}",
                            "equipment_ids": {equipment_id_value} if equipment_id_value else set(),
                            "installation_point_ids": {installation_id} if installation_id else set(),
                        }
                    )
                    return context

    if scope_type == "asset_tree" and context["asset_tree_id"]:
        context["label"] = f"Asset Tree {context['asset_tree_id']}"
    elif scope_type == "equipment" and context["equipment_id"]:
        context["label"] = f"Equipment {context['equipment_id']}"
        context["equipment_ids"] = {context["equipment_id"]}
    elif scope_type == "sensor" and context["installation_point_id"]:
        context["label"] = f"Sensor {context['installation_point_id']}"
        context["installation_point_ids"] = {context["installation_point_id"]}
        if context["equipment_id"]:
            context["equipment_ids"] = {context["equipment_id"]}
    return context


def _public_scope(scope_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": scope_context["type"],
        "asset_tree_id": _none_if_empty(scope_context.get("asset_tree_id", "")),
        "equipment_id": _none_if_empty(scope_context.get("equipment_id", "")),
        "installation_point_id": _none_if_empty(scope_context.get("installation_point_id", "")),
        "sensor_id": _none_if_empty(scope_context.get("sensor_id", "")),
        "label": scope_context.get("label", "All equipment"),
    }


def _snapshot_review_context(
    scope_context: dict[str, Any],
    scoped_rows: list[dict[str, Any]],
    snapshot_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = scoped_rows if scope_context["type"] != "all" else snapshot_rows
    first = rows[0] if rows else {}
    equipment_ids = sorted({_text_id(row.get("equipment_id")) for row in rows if _text_id(row.get("equipment_id"))})
    sensor_ids = sorted(
        {
            _text_id(row.get("installation_point_id"))
            for row in rows
            if _text_id(row.get("installation_point_id"))
        }
    )
    context = {
        "label": scope_context.get("label", "All equipment"),
        "equipment_name": scope_context.get("equipment_name") or first.get("equipment_name") or "",
        "customer_asset_id": scope_context.get("customer_asset_id") or first.get("customer_asset_id") or "",
        "sensor_name": scope_context.get("sensor_name") or first.get("installation_point_name") or "",
        "equipment_count": len(equipment_ids),
        "sensor_count": len(sensor_ids),
        "snapshot_row_count": len(rows),
        "all_snapshot_row_count": len(snapshot_rows),
    }
    if scope_context["type"] == "sensor" and not context["sensor_count"] and scope_context.get("installation_point_id"):
        context["sensor_count"] = 1
    return context


def _snapshot_review_trend(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    scope_context: dict[str, Any],
    metric: str,
    dimension: str,
    stat: str,
) -> dict[str, Any]:
    value_field = _metric_field(metric, stat, dimension)
    try:
        payload = load_trend_view(
            settings=settings,
            start_date=start_date,
            end_date=end_date,
            source=source,
        )
    except FileNotFoundError as exc:
        return {
            "status": "missing",
            "message": str(exc),
            "value_field": value_field,
            "sensor_rows": [],
            "equipment_rows": [],
            "rows": [],
        }
    sensor_rows = _filter_rows_for_review_scope(payload.get("sensor_rows", []), scope_context)
    equipment_rows = _filter_rows_for_review_scope(payload.get("equipment_rows", []), scope_context)
    return {
        "status": "available",
        "value_field": value_field,
        "row_count": len(sensor_rows),
        "sensor_rows": sensor_rows,
        "equipment_rows": equipment_rows,
        "rows": sensor_rows,
        "skipped_dates": payload.get("metadata", {}).get("skipped_dates", []),
    }


def _snapshot_review_cluster_context(
    settings: AppSettings,
    run_date: date,
    source: str,
    dimension: str,
    feature_space: str | None,
    k: int,
    scope_context: dict[str, Any],
) -> dict[str, Any]:
    try:
        payload = load_cluster_view(
            settings=settings,
            run_date=run_date,
            source=source,
            dimension=dimension,
            feature_space=feature_space,
            k=k,
        )
    except FileNotFoundError as exc:
        return {
            "status": "missing",
            "message": str(exc),
            "dimension": dimension,
            "feature_space": feature_space,
            "k": k,
            "points": [],
            "rows": [],
            "selected_ids": sorted(scope_context.get("installation_point_ids", set())),
        }
    points = _filter_rows_for_review_scope(payload.get("pca_rows", []), scope_context)
    rows = _filter_rows_for_review_scope(payload.get("rows", []), scope_context)
    return {
        "status": "available",
        "dimension": payload["dimension"],
        "feature_space": payload.get("feature_space"),
        "k": payload["k"],
        "row_count": len(rows),
        "all_row_count": payload["row_count"],
        "points": points,
        "rows": rows,
        "selected_ids": sorted(scope_context.get("installation_point_ids", set())),
        "metrics": payload.get("metrics", {}),
    }


def _snapshot_review_events(
    settings: AppSettings,
    run_date: date,
    source: str,
    scope_context: dict[str, Any],
    snapshot_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    action_rows = _sqlite_action_item_rows(settings, run_date)
    source_path = "sqlite"
    if not action_rows:
        action_rows = _raw_action_item_rows(settings, run_date, source)
        source_path = "raw"
    snapshot_context = _snapshot_lookup(snapshot_rows)
    events = [
        _event_row_from_action_item(row, run_date, snapshot_context)
        for row in action_rows
    ]
    scoped_events = [row for row in events if _event_matches_scope(row, scope_context)]
    return {
        "status": "available",
        "input": source_path,
        "row_count": len(scoped_events),
        "all_row_count": len(events),
        "rows": scoped_events,
    }


def _snapshot_review_measurements(
    rows: list[dict[str, Any]],
    metric: str,
    dimension: str,
    stat: str,
) -> dict[str, Any]:
    columns = _measurement_columns(metric, dimension, stat)
    measurement_rows = []
    for row in rows:
        measurement = {
            "sensor_name": row.get("installation_point_name") or row.get("installation_point_id") or "",
            "installation_point_id": row.get("installation_point_id") or "",
            "asset_number": row.get("customer_asset_id") or "",
            "equipment_name": row.get("equipment_name") or "",
        }
        for column in columns:
            if column not in measurement:
                measurement[column] = row.get(column)
        measurement_rows.append(measurement)
    return {
        "status": "available",
        "row_count": len(measurement_rows),
        "columns": columns,
        "rows": measurement_rows,
    }


def _filter_rows_for_review_scope(
    rows: list[dict[str, Any]],
    scope_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if scope_context["type"] == "all":
        return rows
    return [row for row in rows if _row_matches_review_scope(row, scope_context)]


def _row_matches_review_scope(row: dict[str, Any], scope_context: dict[str, Any]) -> bool:
    installation_ids = scope_context.get("installation_point_ids", set())
    equipment_ids = scope_context.get("equipment_ids", set())
    installation_id = _text_id(row.get("installation_point_id"))
    equipment_id = _text_id(row.get("equipment_id"))
    if scope_context["type"] == "sensor":
        return bool(installation_id and installation_id in installation_ids)
    if installation_id and installation_ids:
        return installation_id in installation_ids
    if equipment_id and equipment_ids:
        return equipment_id in equipment_ids
    return False


def _sqlite_action_item_rows(settings: AppSettings, run_date: date) -> list[dict[str, Any]]:
    database_path = get_storage_paths(settings.data_dir).observations_db_path
    if not database_path.exists():
        return []
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            table_exists = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'waites_action_items'
                """
            ).fetchone()
            if table_exists is None:
                return []
            rows = connection.execute(
                """
                SELECT
                    source_date,
                    action_item_id,
                    wo_number,
                    wo_status,
                    sensor_id,
                    type,
                    status,
                    installation_point_id,
                    equipment_id,
                    title,
                    description,
                    urgency,
                    closed_at
                FROM waites_action_items
                WHERE source_date = ?
                ORDER BY action_item_id
                """,
                (run_date.isoformat(),),
            ).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _raw_action_item_rows(settings: AppSettings, run_date: date, source: str) -> list[dict[str, Any]]:
    raw_dir = get_storage_paths(settings.data_dir).raw_waites_run_dir(run_date.isoformat())
    manifest = _optional_json(raw_dir / "manifest.json")
    if manifest and source and manifest.get("source") not in (None, source):
        return []
    try:
        return read_json(raw_dir / "action-items.json").get("list", [])
    except FileNotFoundError:
        return []


def _snapshot_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _text_id(row.get("installation_point_id")): row
        for row in rows
        if _text_id(row.get("installation_point_id"))
    }


def _event_row_from_action_item(
    row: dict[str, Any],
    run_date: date,
    snapshot_context: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    installation_point = row.get("installation_point") if isinstance(row.get("installation_point"), dict) else {}
    equipment = row.get("equipment") if isinstance(row.get("equipment"), dict) else {}
    installation_id = _text_id(row.get("installation_point_id") or installation_point.get("installation_point_id"))
    context_row = snapshot_context.get(installation_id, {})
    equipment_id = _text_id(row.get("equipment_id") or equipment.get("equipment_id") or context_row.get("equipment_id"))
    action_id = _text_id(row.get("action_item_id")) or "unknown"
    title = _clean_text(row.get("title")) or f"Action item {action_id}"
    return {
        "date": _text_id(row.get("source_date")) or run_date.isoformat(),
        "source": "waites",
        "status": _clean_text(row.get("status") or row.get("action_item_status")),
        "type": _clean_text(row.get("type") or row.get("action_item_type")),
        "asset_number": _clean_text(context_row.get("customer_asset_id")),
        "installation_point_id": installation_id,
        "sensor_name": _clean_text(context_row.get("installation_point_name")),
        "equipment_id": equipment_id,
        "event_id": action_id,
        "work_order": _clean_text(row.get("wo_number")),
        "work_order_status": _clean_text(row.get("wo_status")),
        "title": title,
        "urgency": _clean_text(row.get("urgency")),
        "closed_at": _clean_text(row.get("closed_at")),
    }


def _event_matches_scope(row: dict[str, Any], scope_context: dict[str, Any]) -> bool:
    if scope_context["type"] == "all":
        return True
    installation_id = _text_id(row.get("installation_point_id"))
    equipment_id = _text_id(row.get("equipment_id"))
    if scope_context["type"] == "sensor":
        return bool(installation_id and installation_id in scope_context.get("installation_point_ids", set()))
    if installation_id and installation_id in scope_context.get("installation_point_ids", set()):
        return True
    return bool(equipment_id and equipment_id in scope_context.get("equipment_ids", set()))


def _measurement_columns(metric: str, dimension: str, stat: str) -> list[str]:
    columns = list(SNAPSHOT_REVIEW_BASE_COLUMNS)
    for field in [
        _metric_field(metric, stat, dimension),
        _metric_field(metric, "max", dimension),
        _metric_field(metric, "min", dimension),
        _metric_field(metric, "std", dimension),
    ]:
        if field not in columns:
            columns.append(field)
    return columns


def _metric_field(metric: str, stat: str, dimension: str) -> str:
    if metric in AXIS_METRICS:
        axis = dimension if dimension in {"x", "y", "z"} else "x"
        return f"{metric}_{stat}_{axis}"
    return f"{metric}_{stat}"


def _normalize_metric(metric: str) -> str:
    candidate = (metric or "").strip().lower()
    if candidate in AXIS_METRICS | NON_AXIS_METRICS:
        return candidate
    return "rms_vel"


def _normalize_stat(stat: str) -> str:
    candidate = (stat or "").strip().lower()
    return candidate if candidate in {"mean", "max", "min", "std"} else "mean"


def _normalize_dimension_for_metric(metric: str, dimension: str) -> str:
    candidate = (dimension or "").strip().lower()
    if metric in AXIS_METRICS:
        return candidate if candidate in {"x", "y", "z"} else "x"
    return "temperature" if metric.startswith("temp") else candidate or "x"


def _cluster_dimension_from_feature_space(feature_space: str | None, dimension: str) -> str:
    if not feature_space:
        return _validate_dimension(dimension)
    feature = feature_space.strip().lower()
    if feature in DIMENSIONS:
        return _validate_dimension(feature)
    if feature.startswith("x_"):
        return "x"
    if feature.startswith("y_"):
        return "y"
    if feature.startswith("z_"):
        return "z"
    if feature == "temperature":
        return "temperature"
    return _validate_dimension(dimension)


def list_cluster_model_view(
    settings: AppSettings,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    return list_registered_cluster_models(
        settings=settings,
        source=source,
        start_date=start_date,
        end_date=end_date,
    )


def _filter_rows(
    rows: list[dict[str, Any]],
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
    customer_asset_id: str | None = None,
) -> list[dict[str, Any]]:
    filters = {
        "equipment_id": equipment_id,
        "installation_point_id": installation_point_id,
        "sensor_id": sensor_id,
        "customer_asset_id": customer_asset_id,
    }
    active = {key: str(value) for key, value in filters.items() if value not in (None, "")}
    if not active:
        return rows
    return [
        row
        for row in rows
        if all(str(row.get(key) or "") == value for key, value in active.items())
    ]


def _active_equipment_from_snapshots(
    settings: AppSettings,
    source_mode: str | None,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for snapshot_date in list_snapshot_dates(settings):
        parsed_snapshot_date = date.fromisoformat(snapshot_date)
        if start_date is not None and parsed_snapshot_date < start_date:
            continue
        if end_date is not None and parsed_snapshot_date > end_date:
            continue

        payload = load_snapshot(settings, parsed_snapshot_date)
        snapshot_source = str(payload["metadata"].get("source") or "")
        if source_mode is not None and snapshot_source != source_mode:
            continue

        for row in payload["rows"]:
            equipment_id = _text_id(row.get("equipment_id"))
            equipment = active.setdefault(
                equipment_id,
                {
                    "equipment_id": equipment_id,
                    "equipment_name": "",
                    "customer_asset_id": "",
                    "dates": set(),
                    "sensors": {},
                },
            )
            equipment["dates"].add(snapshot_date)
            if not equipment["equipment_name"] and row.get("equipment_name"):
                equipment["equipment_name"] = row.get("equipment_name")
            if not equipment["customer_asset_id"] and row.get("customer_asset_id"):
                equipment["customer_asset_id"] = row.get("customer_asset_id")

            installation_point_id = _text_id(row.get("installation_point_id"))
            if not installation_point_id:
                continue
            sensor = equipment["sensors"].setdefault(
                installation_point_id,
                {
                    "installation_point_id": installation_point_id,
                    "installation_point_name": "",
                    "sensor_id": "",
                    "customer_asset_id": "",
                    "dates": set(),
                },
            )
            sensor["dates"].add(snapshot_date)
            if not sensor["installation_point_name"] and row.get("installation_point_name"):
                sensor["installation_point_name"] = row.get("installation_point_name")
            if not sensor["sensor_id"] and row.get("sensor_id"):
                sensor["sensor_id"] = row.get("sensor_id")
            if not sensor["customer_asset_id"] and row.get("customer_asset_id"):
                sensor["customer_asset_id"] = row.get("customer_asset_id")
    return active


def _load_reference_indexes(
    settings: AppSettings,
    source_mode: str | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "asset_trees": _merged_reference_index(
            _csv_reference_rows(settings, "asset_tree.csv"),
            _sqlite_reference_rows(
                settings,
                "waites_asset_tree_reference",
                [
                    "asset_tree_id",
                    "name",
                    "parent_asset_tree_id",
                    "facility_id",
                    "asset_tree_path",
                ],
                source_mode,
            ),
            "asset_tree_id",
        ),
        "equipment": _merged_reference_index(
            _csv_reference_rows(settings, "equipment.csv"),
            _sqlite_reference_rows(
                settings,
                "waites_equipment_reference",
                ["equipment_id", "asset_tree_id", "name", "facility_id", "customer_asset_id"],
                source_mode,
            ),
            "equipment_id",
        ),
        "installation_points": _merged_reference_index(
            _csv_reference_rows(settings, "installation_points.csv"),
            _sqlite_reference_rows(
                settings,
                "waites_installation_point_reference",
                [
                    "installation_point_id",
                    "name",
                    "equipment_id",
                    "sensor_id",
                    "facility_id",
                    "last_seen",
                    "installation_date",
                    "customer_asset_id",
                ],
                source_mode,
            ),
            "installation_point_id",
        ),
    }


def _sqlite_reference_rows(
    settings: AppSettings,
    table: str,
    fields: list[str],
    source_mode: str | None,
) -> list[dict[str, Any]]:
    database_path = get_storage_paths(settings.data_dir).observations_db_path
    if not database_path.exists():
        return []
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            table_exists = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table,),
            ).fetchone()
            if table_exists is None:
                return []

            columns = ", ".join(fields)
            if source_mode is None:
                rows = connection.execute(f"SELECT {columns} FROM {table}").fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {columns} FROM {table} WHERE source = ?",
                    (source_mode,),
                ).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _csv_reference_rows(settings: AppSettings, filename: str) -> list[dict[str, Any]]:
    path = get_storage_paths(settings.data_dir).waites_reference_dir() / filename
    try:
        return read_csv_rows(path)
    except FileNotFoundError:
        return []


def _reference_index(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {
        _text_id(row.get(key)): row
        for row in rows
        if _text_id(row.get(key))
    }


def _merged_reference_index(
    fallback_rows: list[dict[str, Any]],
    preferred_rows: list[dict[str, Any]],
    key: str,
) -> dict[str, dict[str, Any]]:
    index = _reference_index(fallback_rows, key)
    index.update(_reference_index(preferred_rows, key))
    return index


def _asset_tree_name(asset_tree_id: str, asset_tree_ref: dict[str, Any]) -> str:
    if asset_tree_id == "unknown":
        return _clean_text(asset_tree_ref.get("name")) or "Unknown Asset Tree"
    return _clean_text(asset_tree_ref.get("name")) or f"Asset Tree {asset_tree_id}"


def _none_if_empty(value: str) -> str | None:
    return value or None


def _clean_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _text_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _filters(**values: str | None) -> dict[str, str | None]:
    return {key: value for key, value in values.items() if value not in (None, "")}


def _cluster_dir_name(run_date: date, source: str, dimension: str, k: int) -> str:
    return f"date={run_date.isoformat()}_source={source}_dimension={dimension}_k={k}"


def _drift_dir_name(from_date: date, to_date: date, source: str, dimension: str, k: int) -> str:
    return (
        f"from={from_date.isoformat()}_to={to_date.isoformat()}_"
        f"source={source}_dimension={dimension}_k={k}"
    )


def _window_dir_name(start_date: date, end_date: date, source: str, dimension: str, k: int) -> str:
    return (
        f"start={start_date.isoformat()}_end={end_date.isoformat()}_"
        f"source={source}_dimension={dimension}_k={k}"
    )


def _optional_json(path: Path) -> dict[str, Any] | None:
    try:
        return read_json(path)
    except FileNotFoundError:
        return None


def _optional_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        return read_csv_rows(path)
    except FileNotFoundError:
        return []


def _match_dict(match: re.Match[str]) -> dict[str, str]:
    return {key: value for key, value in match.groupdict().items() if key != "k"}


def _optional_source(source: str | None) -> str | None:
    if source in (None, ""):
        return None
    return _validate_source(source)


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise ValueError(f"source must be one of: {allowed}")
    return source_mode


def _validate_dimension(dimension: str) -> str:
    cluster_dimension = dimension.strip().lower()
    if cluster_dimension not in DIMENSIONS:
        allowed = ", ".join(DIMENSIONS)
        raise ValueError(f"dimension must be one of: {allowed}")
    return cluster_dimension


def _validate_k(k: int) -> int:
    if k < 1:
        raise ValueError("k must be at least 1")
    return k


def _sort_key(value: Any) -> tuple[int, Any]:
    raw = str(value)
    return (0, int(raw)) if raw.isdigit() else (1, raw)

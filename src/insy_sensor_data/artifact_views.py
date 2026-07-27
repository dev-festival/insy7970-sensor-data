from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import re

from insy_sensor_data.artifacts import read_csv_rows, read_json
from insy_sensor_data.clustering.features import DIMENSIONS
from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.snapshots.build import list_snapshot_dates, load_snapshot
from insy_sensor_data.snapshots.trends import list_trend_ranges, load_trends
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


def discover_artifacts(settings: AppSettings) -> dict[str, Any]:
    storage = get_storage_paths(settings.data_dir)
    snapshots = _snapshot_artifacts(settings)
    trends = _trend_artifacts(settings)
    clusters = _cluster_artifacts(storage.clusters_dir)
    drift = _drift_artifacts(storage.drift_dir)
    cluster_windows = _cluster_window_artifacts(storage.cluster_windows_dir)
    sources = sorted(
        {
            str(row["source"])
            for group in [snapshots, trends, clusters, drift, cluster_windows]
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
    ks = sorted(
        {
            int(row["k"])
            for group in [clusters, drift, cluster_windows]
            for row in group
            if row.get("k") is not None
        }
    )
    return {
        "sources": sources,
        "dimensions": dimensions,
        "ks": ks,
        "snapshots": snapshots,
        "trends": trends,
        "clusters": clusters,
        "drift": drift,
        "cluster_windows": cluster_windows,
        "counts": {
            "snapshots": len(snapshots),
            "trends": len(trends),
            "clusters": len(clusters),
            "drift": len(drift),
            "cluster_windows": len(cluster_windows),
        },
    }


def list_equipment_view(settings: AppSettings, source: str | None = None) -> dict[str, Any]:
    source_mode = _optional_source(source)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot_date in list_snapshot_dates(settings):
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
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "date_count": len(dates),
            }
        )
    return {
        "source": source_mode,
        "count": len(rows),
        "rows": sorted(rows, key=lambda row: (_sort_key(row["equipment_id"]), row["source"])),
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
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    source_mode = _optional_source(source)
    payload = load_trends(settings, start_date, end_date)
    trend_source = str(payload["metadata"].get("source") or "")
    if source_mode is not None and trend_source != source_mode:
        raise FileNotFoundError(
            f"Missing trend artifact for source {source_mode} range "
            f"{start_date.isoformat()} to {end_date.isoformat()}."
        )
    sensor_rows = _filter_rows(
        payload["sensor_rows"],
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        customer_asset_id=customer_asset_id,
    )
    equipment_rows = _filter_rows(
        payload["equipment_rows"],
        equipment_id=equipment_id,
        customer_asset_id=customer_asset_id,
    )
    return {
        **payload,
        "source": trend_source,
        "sensor_row_count": len(payload["sensor_rows"]),
        "filtered_sensor_row_count": len(sensor_rows),
        "equipment_row_count": len(payload["equipment_rows"]),
        "filtered_equipment_row_count": len(equipment_rows),
        "filters": _filters(
            source=source_mode,
            equipment_id=equipment_id,
            installation_point_id=installation_point_id,
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
) -> dict[str, Any]:
    source_mode = _validate_source(source)
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
) -> dict[str, Any]:
    source_mode = _validate_source(source)
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
) -> dict[str, Any]:
    source_mode = _validate_source(source)
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


def _filter_rows(
    rows: list[dict[str, Any]],
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    customer_asset_id: str | None = None,
) -> list[dict[str, Any]]:
    filters = {
        "equipment_id": equipment_id,
        "installation_point_id": installation_point_id,
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

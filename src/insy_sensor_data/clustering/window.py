from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import isfinite, sqrt
from typing import Any

from insy_sensor_data.artifacts import read_csv_rows, read_json, write_csv_rows, write_json
from insy_sensor_data.clustering.features import DIMENSIONS
from insy_sensor_data.clustering.model import (
    DEFAULT_RANDOM_SEED,
    build_cluster_run,
    compare_cluster_drift,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths


WINDOW_SCHEMA_VERSION = 1
ALIGNED_DRIFT_SCHEMA_VERSION = 1
VALID_WINDOW_DIMENSIONS = set(DIMENSIONS)

WINDOW_SUMMARY_FIELDS = [
    "date",
    "status",
    "cluster_dir",
    "row_count",
    "feature_count",
    "inertia",
    "silhouette_score",
    "calinski_harabasz_score",
    "cluster_counts",
    "warning_count",
    "interpretation",
]

QUALITY_SUMMARY_FIELDS = [
    "date",
    "row_count",
    "feature_count",
    "k",
    "inertia",
    "silhouette_score",
    "calinski_harabasz_score",
    "quality_level",
    "warning_count",
    "warnings",
    "interpretation",
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
]

ALIGNED_DRIFT_SUMMARY_FIELDS = [
    "from_date",
    "to_date",
    "status",
    "matched_sensor_count",
    "raw_label_changed_count",
    "aligned_changed_count",
    "raw_label_changed_ratio",
    "aligned_changed_ratio",
    "warning_count",
    "warnings",
    "interpretation",
]


def build_cluster_window(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str = "mock",
    dimension: str = "x",
    k: int = 4,
    random_seed: int = DEFAULT_RANDOM_SEED,
    force: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    cluster_dimension = _validate_dimension(dimension)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if k < 1:
        raise ValueError("k must be at least 1")

    dates = _date_range(start_date, end_date)
    date_runs = [
        _ensure_cluster_run(
            settings=settings,
            run_date=run_date,
            source=source_mode,
            dimension=cluster_dimension,
            k=k,
            random_seed=random_seed,
            force=force,
        )
        for run_date in dates
    ]

    aligned_pairs: list[dict[str, Any]] = []
    alignment_rows: list[dict[str, Any]] = []
    for from_date, to_date in zip(dates, dates[1:], strict=False):
        pair_summary = align_cluster_drift(
            settings=settings,
            from_date=from_date,
            to_date=to_date,
            source=source_mode,
            dimension=cluster_dimension,
            k=k,
            force=force,
        )
        aligned_pairs.append(pair_summary)
        alignment_rows.extend(read_csv_rows(_centroid_alignment_path(settings, from_date, to_date, source_mode, cluster_dimension, k)))

    storage = get_storage_paths(settings.data_dir)
    output_dir = _cluster_window_dir(storage.cluster_windows_dir, start_date, end_date, source_mode, cluster_dimension, k)
    output_dir.mkdir(parents=True, exist_ok=True)
    window_path = output_dir / "window_summary.csv"
    quality_path = output_dir / "quality_summary.csv"
    aligned_summary_path = output_dir / "aligned_drift_summary.csv"
    centroid_alignment_path = output_dir / "centroid_alignment.csv"
    metrics_path = output_dir / "metrics.json"

    window_rows = [_window_summary_row(run) for run in date_runs]
    quality_rows = [_quality_summary_row(run, k) for run in date_runs]
    aligned_summary_rows = [_aligned_drift_summary_row(pair) for pair in aligned_pairs]
    write_csv_rows(window_path, window_rows, WINDOW_SUMMARY_FIELDS)
    write_csv_rows(quality_path, quality_rows, QUALITY_SUMMARY_FIELDS)
    write_csv_rows(aligned_summary_path, aligned_summary_rows, ALIGNED_DRIFT_SUMMARY_FIELDS)
    write_csv_rows(centroid_alignment_path, alignment_rows, CENTROID_ALIGNMENT_FIELDS)

    warnings = [
        warning
        for run in date_runs
        for warning in run.get("warnings", [])
    ] + [
        warning
        for pair in aligned_pairs
        for warning in pair.get("warnings", [])
    ]
    write_json(
        metrics_path,
        {
            "schema_version": WINDOW_SCHEMA_VERSION,
            "source": source_mode,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "dimension": cluster_dimension,
            "k": k,
            "random_seed": random_seed,
            "built_at": _utc_now(),
            "date_count": len(dates),
            "pair_count": len(aligned_pairs),
            "warning_count": len(warnings),
            "warnings": warnings,
            "date_runs": date_runs,
            "aligned_pairs": aligned_pairs,
            "outputs": {
                "window_summary": window_path.as_posix(),
                "quality_summary": quality_path.as_posix(),
                "aligned_drift_summary": aligned_summary_path.as_posix(),
                "centroid_alignment": centroid_alignment_path.as_posix(),
                "metrics": metrics_path.as_posix(),
            },
        },
    )

    return {
        "source": source_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "dimension": cluster_dimension,
        "k": k,
        "random_seed": random_seed,
        "cluster_window_dir": output_dir.as_posix(),
        "window_summary_path": window_path.as_posix(),
        "quality_summary_path": quality_path.as_posix(),
        "aligned_drift_summary_path": aligned_summary_path.as_posix(),
        "centroid_alignment_path": centroid_alignment_path.as_posix(),
        "metrics_path": metrics_path.as_posix(),
        "date_count": len(dates),
        "pair_count": len(aligned_pairs),
        "warning_count": len(warnings),
        "warnings": warnings,
        "date_runs": date_runs,
        "aligned_pairs": aligned_pairs,
    }


def align_cluster_drift(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str = "mock",
    dimension: str = "x",
    k: int = 4,
    force: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    cluster_dimension = _validate_dimension(dimension)
    if to_date < from_date:
        raise ValueError("to_date must be on or after from_date")
    if k < 1:
        raise ValueError("k must be at least 1")

    paths = _aligned_drift_paths(settings, from_date, to_date, source_mode, cluster_dimension, k)
    if not force and _aligned_drift_exists(paths):
        return _load_aligned_drift_summary(paths["aligned_metrics"])

    raw_drift = _ensure_raw_drift(
        settings=settings,
        from_date=from_date,
        to_date=to_date,
        source=source_mode,
        dimension=cluster_dimension,
        k=k,
        force=force,
    )
    from_dir = _cluster_dir(settings, from_date, source_mode, cluster_dimension, k)
    to_dir = _cluster_dir(settings, to_date, source_mode, cluster_dimension, k)
    from_rows = read_csv_rows(from_dir / "sensor_clusters.csv")
    to_rows = read_csv_rows(to_dir / "sensor_clusters.csv")
    from_summary = read_csv_rows(from_dir / "cluster_summary.csv")
    to_summary = read_csv_rows(to_dir / "cluster_summary.csv")

    alignment_rows = _centroid_alignment_rows(
        from_summary=from_summary,
        to_summary=to_summary,
        from_date=from_date,
        to_date=to_date,
    )
    alignment = {str(row["from_cluster"]): str(row["to_cluster"]) for row in alignment_rows}
    drift_rows = _aligned_sensor_drift_rows(from_rows, to_rows, alignment)
    matched_rows = [row for row in drift_rows if row["status"] == "matched"]
    raw_changed_count = sum(1 for row in matched_rows if row["raw_label_changed"] == "true")
    aligned_changed_count = sum(1 for row in matched_rows if row["aligned_changed"] == "true")
    matched_count = len(matched_rows)
    warnings = _aligned_drift_warnings(
        alignment_rows=alignment_rows,
        raw_changed_count=raw_changed_count,
        aligned_changed_count=aligned_changed_count,
    )
    interpretation = _drift_interpretation(
        matched_count=matched_count,
        raw_changed_count=raw_changed_count,
        aligned_changed_count=aligned_changed_count,
        warnings=warnings,
    )

    write_csv_rows(paths["aligned_cluster_drift"], drift_rows, ALIGNED_SENSOR_DRIFT_FIELDS)
    write_csv_rows(paths["centroid_alignment"], alignment_rows, CENTROID_ALIGNMENT_FIELDS)
    summary = {
        "schema_version": ALIGNED_DRIFT_SCHEMA_VERSION,
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "dimension": cluster_dimension,
        "k": k,
        "built_at": _utc_now(),
        "status": "completed",
        "drift_dir": paths["drift_dir"].as_posix(),
        "matched_sensor_count": matched_count,
        "raw_label_changed_count": raw_changed_count,
        "aligned_changed_count": aligned_changed_count,
        "raw_label_changed_ratio": raw_changed_count / matched_count if matched_count else None,
        "aligned_changed_ratio": aligned_changed_count / matched_count if matched_count else None,
        "warning_count": len(warnings),
        "warnings": warnings,
        "interpretation": interpretation,
        "raw_drift": raw_drift,
        "outputs": {
            "cluster_drift": paths["cluster_drift"].as_posix(),
            "centroid_drift": paths["centroid_drift"].as_posix(),
            "aligned_cluster_drift": paths["aligned_cluster_drift"].as_posix(),
            "centroid_alignment": paths["centroid_alignment"].as_posix(),
            "aligned_metrics": paths["aligned_metrics"].as_posix(),
        },
    }
    write_json(paths["aligned_metrics"], summary)
    return _public_aligned_drift_summary(summary)


def _ensure_cluster_run(
    settings: AppSettings,
    run_date: date,
    source: str,
    dimension: str,
    k: int,
    random_seed: int,
    force: bool,
) -> dict[str, Any]:
    paths = _cluster_paths(settings, run_date, source, dimension, k)
    if not force and all(path.exists() for path in paths.values()):
        summary = _cluster_summary_from_metrics(paths["metrics"])
        status = "skipped_existing"
    else:
        summary = build_cluster_run(
            settings=settings,
            run_date=run_date,
            source=source,
            dimension=dimension,
            k=k,
            random_seed=random_seed,
        )
        status = "completed"
    warnings = _cluster_quality_warnings(summary)
    return {
        **summary,
        "status": status,
        "quality_level": _cluster_quality_level(summary, warnings),
        "warning_count": len(warnings),
        "warnings": warnings,
        "interpretation": _cluster_interpretation(summary, warnings),
    }


def _ensure_raw_drift(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    dimension: str,
    k: int,
    force: bool,
) -> dict[str, Any]:
    paths = _aligned_drift_paths(settings, from_date, to_date, source, dimension, k)
    raw_paths = [paths["cluster_drift"], paths["centroid_drift"], paths["metrics"]]
    if not force and all(path.exists() for path in raw_paths):
        metrics = read_json(paths["metrics"])
        return {
            "source": source,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "dimension": dimension,
            "k": k,
            "drift_dir": paths["drift_dir"].as_posix(),
            "cluster_drift_path": paths["cluster_drift"].as_posix(),
            "centroid_drift_path": paths["centroid_drift"].as_posix(),
            "metrics_path": paths["metrics"].as_posix(),
            "matched_sensor_count": metrics.get("matched_sensor_count"),
            "changed_sensor_count": metrics.get("changed_sensor_count"),
            "changed_ratio": metrics.get("changed_ratio"),
            "status": "skipped_existing",
        }
    summary = compare_cluster_drift(
        settings=settings,
        from_date=from_date,
        to_date=to_date,
        source=source,
        dimension=dimension,
        k=k,
    )
    return {**summary, "status": "completed"}


def _cluster_summary_from_metrics(metrics_path) -> dict[str, Any]:
    metrics = read_json(metrics_path)
    outputs = metrics.get("outputs") or {}
    metric_values = metrics.get("metrics") or {}
    kmeans = metrics.get("kmeans") or {}
    return {
        "source": metrics["source"],
        "date": metrics["date"],
        "dimension": metrics["dimension"],
        "k": metrics["k"],
        "random_seed": metrics["random_seed"],
        "cluster_dir": metrics_path.parent.as_posix(),
        "sensor_clusters_path": outputs.get("sensor_clusters", (metrics_path.parent / "sensor_clusters.csv").as_posix()),
        "cluster_summary_path": outputs.get("cluster_summary", (metrics_path.parent / "cluster_summary.csv").as_posix()),
        "pca_coordinates_path": outputs.get("pca_coordinates", (metrics_path.parent / "pca_coordinates.csv").as_posix()),
        "metrics_path": metrics_path.as_posix(),
        "row_count": metrics.get("row_count"),
        "feature_count": metrics.get("feature_count"),
        "cluster_counts": metrics.get("cluster_counts", {}),
        "inertia": kmeans.get("inertia"),
        "silhouette_score": (metric_values.get("silhouette_score") or {}).get("value"),
        "calinski_harabasz_score": (metric_values.get("calinski_harabasz_score") or {}).get("value"),
    }


def _centroid_alignment_rows(
    from_summary: list[dict[str, str]],
    to_summary: list[dict[str, str]],
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
    from_rows: list[dict[str, str]],
    to_rows: list[dict[str, str]],
    alignment: dict[str, str],
) -> list[dict[str, Any]]:
    from_by_id = {row["installation_point_id"]: row for row in from_rows}
    to_by_id = {row["installation_point_id"]: row for row in to_rows}
    output: list[dict[str, Any]] = []
    for installation_id in sorted(set(from_by_id) | set(to_by_id), key=_sort_key):
        from_row = from_by_id.get(installation_id, {})
        to_row = to_by_id.get(installation_id, {})
        status = "matched" if from_row and to_row else "from_only" if from_row else "to_only"
        from_cluster = str(from_row.get("cluster", ""))
        to_cluster = str(to_row.get("cluster", ""))
        aligned_to_cluster = alignment.get(from_cluster, "")
        raw_changed = status == "matched" and from_cluster != to_cluster
        aligned_changed = status == "matched" and aligned_to_cluster != to_cluster
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
                "from_distance_to_centroid": from_row.get("distance_to_centroid", ""),
                "to_distance_to_centroid": to_row.get("distance_to_centroid", ""),
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


def _cluster_quality_warnings(summary: dict[str, Any]) -> list[dict[str, Any]]:
    row_count = _int(summary.get("row_count"))
    feature_count = _int(summary.get("feature_count"))
    k = _int(summary.get("k"))
    silhouette = _optional_float(summary.get("silhouette_score"))
    warnings: list[dict[str, Any]] = []
    if row_count < 30 or row_count < max(k * 5, 1):
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
                "message": "The feature matrix has too few features for strong clustering interpretation.",
            }
        )
    if silhouette is None:
        warnings.append(
            {
                "level": "warning",
                "code": "silhouette_unavailable",
                "message": "Silhouette score is unavailable for this row count and k.",
            }
        )
    elif silhouette < 0.2:
        warnings.append(
            {
                "level": "warning",
                "code": "low_silhouette",
                "value": silhouette,
                "message": "Cluster separation is weak for this run.",
            }
        )
    return warnings


def _cluster_quality_level(summary: dict[str, Any], warnings: list[dict[str, Any]]) -> str:
    codes = {warning["code"] for warning in warnings}
    if "small_sample_contract_only" in codes:
        return "contract_test_only"
    if "low_silhouette" in codes or "low_feature_count" in codes:
        return "noisy"
    silhouette = _optional_float(summary.get("silhouette_score"))
    if silhouette is not None and silhouette >= 0.45:
        return "reasonable_first_signal"
    return "inspect"


def _cluster_interpretation(summary: dict[str, Any], warnings: list[dict[str, Any]]) -> str:
    quality = _cluster_quality_level(summary, warnings)
    if quality == "contract_test_only":
        return "Too small for quality judgment; use this as a pipeline contract check."
    if quality == "noisy":
        return "Cluster structure looks noisy; inspect features and k before using drift operationally."
    if quality == "reasonable_first_signal":
        return "Cluster separation is a reasonable first signal; compare with adjacent days before acting."
    return "Cluster output is inspectable; use metrics and centroid movement for context."


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


def _window_summary_row(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": run.get("date"),
        "status": run.get("status"),
        "cluster_dir": run.get("cluster_dir"),
        "row_count": run.get("row_count"),
        "feature_count": run.get("feature_count"),
        "inertia": run.get("inertia"),
        "silhouette_score": run.get("silhouette_score"),
        "calinski_harabasz_score": run.get("calinski_harabasz_score"),
        "cluster_counts": run.get("cluster_counts", {}),
        "warning_count": run.get("warning_count", 0),
        "interpretation": run.get("interpretation"),
    }


def _quality_summary_row(run: dict[str, Any], k: int) -> dict[str, Any]:
    return {
        "date": run.get("date"),
        "row_count": run.get("row_count"),
        "feature_count": run.get("feature_count"),
        "k": k,
        "inertia": run.get("inertia"),
        "silhouette_score": run.get("silhouette_score"),
        "calinski_harabasz_score": run.get("calinski_harabasz_score"),
        "quality_level": run.get("quality_level"),
        "warning_count": run.get("warning_count", 0),
        "warnings": run.get("warnings", []),
        "interpretation": run.get("interpretation"),
    }


def _aligned_drift_summary_row(pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "from_date": pair.get("from_date"),
        "to_date": pair.get("to_date"),
        "status": pair.get("status"),
        "matched_sensor_count": pair.get("matched_sensor_count"),
        "raw_label_changed_count": pair.get("raw_label_changed_count"),
        "aligned_changed_count": pair.get("aligned_changed_count"),
        "raw_label_changed_ratio": pair.get("raw_label_changed_ratio"),
        "aligned_changed_ratio": pair.get("aligned_changed_ratio"),
        "warning_count": pair.get("warning_count", 0),
        "warnings": pair.get("warnings", []),
        "interpretation": pair.get("interpretation"),
    }


def _load_aligned_drift_summary(metrics_path) -> dict[str, Any]:
    summary = read_json(metrics_path)
    return {**_public_aligned_drift_summary(summary), "status": "skipped_existing"}


def _public_aligned_drift_summary(summary: dict[str, Any]) -> dict[str, Any]:
    outputs = summary.get("outputs") or {}
    return {
        "source": summary["source"],
        "from_date": summary["from_date"],
        "to_date": summary["to_date"],
        "dimension": summary["dimension"],
        "k": summary["k"],
        "status": summary.get("status", "completed"),
        "drift_dir": summary["drift_dir"],
        "cluster_drift_path": outputs.get("cluster_drift"),
        "centroid_drift_path": outputs.get("centroid_drift"),
        "aligned_cluster_drift_path": outputs.get("aligned_cluster_drift"),
        "centroid_alignment_path": outputs.get("centroid_alignment"),
        "aligned_metrics_path": outputs.get("aligned_metrics"),
        "matched_sensor_count": summary["matched_sensor_count"],
        "raw_label_changed_count": summary["raw_label_changed_count"],
        "aligned_changed_count": summary["aligned_changed_count"],
        "raw_label_changed_ratio": summary["raw_label_changed_ratio"],
        "aligned_changed_ratio": summary["aligned_changed_ratio"],
        "warning_count": summary.get("warning_count", 0),
        "warnings": summary.get("warnings", []),
        "interpretation": summary.get("interpretation", ""),
    }


def _centroid_columns(from_summary: list[dict[str, str]], to_summary: list[dict[str, str]]) -> list[str]:
    from_columns = {field for row in from_summary for field in row if field.startswith("centroid_scaled_")}
    to_columns = {field for row in to_summary for field in row if field.startswith("centroid_scaled_")}
    return sorted(from_columns & to_columns)


def _centroid_distance(left: dict[str, str], right: dict[str, str], columns: list[str]) -> float:
    return sqrt(sum((_float(left.get(column)) - _float(right.get(column))) ** 2 for column in columns))


def _mapping_confidence(
    best_distance: float,
    second_distance: float | None,
    from_row: dict[str, str],
    to_row: dict[str, str],
) -> str:
    if _int(from_row.get("sensor_count")) == 0 or _int(to_row.get("sensor_count")) == 0:
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


def _cluster_paths(settings: AppSettings, run_date: date, source: str, dimension: str, k: int) -> dict[str, Any]:
    cluster_dir = _cluster_dir(settings, run_date, source, dimension, k)
    return {
        "sensor_clusters": cluster_dir / "sensor_clusters.csv",
        "cluster_summary": cluster_dir / "cluster_summary.csv",
        "pca_coordinates": cluster_dir / "pca_coordinates.csv",
        "metrics": cluster_dir / "metrics.json",
    }


def _aligned_drift_paths(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    dimension: str,
    k: int,
) -> dict[str, Any]:
    drift_dir = _drift_dir(settings, from_date, to_date, source, dimension, k)
    return {
        "drift_dir": drift_dir,
        "cluster_drift": drift_dir / "cluster_drift.csv",
        "centroid_drift": drift_dir / "centroid_drift.csv",
        "metrics": drift_dir / "metrics.json",
        "aligned_cluster_drift": drift_dir / "aligned_cluster_drift.csv",
        "centroid_alignment": drift_dir / "centroid_alignment.csv",
        "aligned_metrics": drift_dir / "aligned_metrics.json",
    }


def _aligned_drift_exists(paths: dict[str, Any]) -> bool:
    return all(
        paths[name].exists()
        for name in ["aligned_cluster_drift", "centroid_alignment", "aligned_metrics"]
    )


def _centroid_alignment_path(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str,
    dimension: str,
    k: int,
):
    return _aligned_drift_paths(settings, from_date, to_date, source, dimension, k)["centroid_alignment"]


def _cluster_dir(settings: AppSettings, run_date: date, source: str, dimension: str, k: int):
    storage = get_storage_paths(settings.data_dir)
    return storage.clusters_dir / f"date={run_date.isoformat()}_source={source}_dimension={dimension}_k={k}"


def _drift_dir(settings: AppSettings, from_date: date, to_date: date, source: str, dimension: str, k: int):
    storage = get_storage_paths(settings.data_dir)
    return storage.drift_dir / (
        f"from={from_date.isoformat()}_to={to_date.isoformat()}_"
        f"source={source}_dimension={dimension}_k={k}"
    )


def _cluster_window_dir(root, start_date: date, end_date: date, source: str, dimension: str, k: int):
    return root / (
        f"start={start_date.isoformat()}_end={end_date.isoformat()}_"
        f"source={source}_dimension={dimension}_k={k}"
    )


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    return source_mode


def _validate_dimension(dimension: str) -> str:
    cluster_dimension = dimension.strip().lower()
    if cluster_dimension not in VALID_WINDOW_DIMENSIONS:
        allowed = ", ".join(sorted(VALID_WINDOW_DIMENSIONS))
        raise ValueError(f"dimension must be one of: {allowed}")
    return cluster_dimension


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    result = float(value)
    if not isfinite(result):
        return 0.0
    return result


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    result = float(value)
    return result if isfinite(result) else None


def _int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def _sort_key(value: Any) -> tuple[int, Any]:
    raw = str(value)
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

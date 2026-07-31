from __future__ import annotations

from datetime import UTC, date, datetime
from math import sqrt
from typing import Any

from insy_sensor_data.artifacts import read_csv_rows, read_json, write_csv_rows, write_json
from insy_sensor_data.clustering import engine
from insy_sensor_data.clustering.features import DIMENSIONS, IDENTIFIER_FIELDS, build_feature_preview
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths


CLUSTER_SCHEMA_VERSION = 1
DRIFT_SCHEMA_VERSION = 1
VALID_CLUSTER_DIMENSIONS = set(DIMENSIONS)
DEFAULT_RANDOM_SEED = 42
DEFAULT_MAX_ITERATIONS = 100
DEFAULT_TOLERANCE = 1e-6

SENSOR_CLUSTER_FIELDS = [
    *IDENTIFIER_FIELDS,
    "cluster",
    "distance_to_centroid",
]

PCA_FIELDS = [
    *IDENTIFIER_FIELDS,
    "cluster",
    "pc1",
    "pc2",
    "distance_to_centroid",
]

DRIFT_FIELDS = [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
    "status",
    "from_cluster",
    "to_cluster",
    "changed",
    "from_distance_to_centroid",
    "to_distance_to_centroid",
]

CENTROID_DRIFT_FIELDS = [
    "cluster",
    "from_sensor_count",
    "to_sensor_count",
    "centroid_distance",
]


def build_cluster_run(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
    dimension: str = "x",
    k: int = 4,
    random_seed: int = DEFAULT_RANDOM_SEED,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    cluster_dimension = _validate_dimension(dimension)
    if k < 1:
        raise ValueError("k must be at least 1")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    feature_summary = _ensure_feature_matrix(
        settings=settings,
        run_date=run_date,
        source=source_mode,
        dimension=cluster_dimension,
    )
    feature_rows = read_csv_rows(_feature_matrix_path(settings, run_date, source_mode, cluster_dimension))
    feature_columns = _feature_columns(feature_rows)
    if not feature_rows:
        raise ValueError("feature matrix has no rows")
    if not feature_columns:
        raise ValueError("feature matrix has no numeric feature columns")
    if k > len(feature_rows):
        raise ValueError("k cannot exceed feature row count")

    matrix = engine.numeric_matrix(feature_rows, feature_columns)
    scaled = engine.standard_scale(matrix, feature_columns)
    kmeans = engine.kmeans(
        scaled.values,
        k=k,
        random_seed=random_seed,
        max_iterations=max_iterations,
        tolerance=DEFAULT_TOLERANCE,
    )
    pca = engine.pca_coordinates(scaled.values, iterations=50)
    metrics = engine.cluster_metrics(scaled.values, kmeans)
    cluster_counts = engine.cluster_counts(kmeans.labels, k)

    storage = get_storage_paths(settings.data_dir)
    output_dir = _cluster_dir(storage.clusters_dir, run_date, source_mode, cluster_dimension, k)
    output_dir.mkdir(parents=True, exist_ok=True)
    sensor_path = output_dir / "sensor_clusters.csv"
    summary_path = output_dir / "cluster_summary.csv"
    pca_path = output_dir / "pca_coordinates.csv"
    metrics_path = output_dir / "metrics.json"

    sensor_rows = engine.sensor_cluster_rows(feature_rows, feature_columns, kmeans)
    summary_rows = engine.cluster_summary_rows(
        feature_rows=feature_rows,
        feature_columns=feature_columns,
        scaled=scaled,
        result=kmeans,
        k=k,
    )
    pca_rows = engine.pca_rows(feature_rows, kmeans, pca)
    write_csv_rows(sensor_path, sensor_rows, [*SENSOR_CLUSTER_FIELDS, *feature_columns])
    write_csv_rows(summary_path, summary_rows, engine.cluster_summary_fields(feature_columns))
    write_csv_rows(pca_path, pca_rows, PCA_FIELDS)
    write_json(
        metrics_path,
        {
            "schema_version": CLUSTER_SCHEMA_VERSION,
            "source": source_mode,
            "date": run_date.isoformat(),
            "dimension": cluster_dimension,
            "k": k,
            "random_seed": random_seed,
            "built_at": _utc_now(),
            "feature_matrix_path": _feature_matrix_path(settings, run_date, source_mode, cluster_dimension).as_posix(),
            "feature_summary_path": feature_summary.get("summary_path"),
            "feature_status": feature_summary.get("status"),
            "row_count": len(feature_rows),
            "feature_count": len(feature_columns),
            "features": feature_columns,
            "cluster_counts": cluster_counts,
            "scaler": {
                "means": scaled.means,
                "scales": scaled.scales,
            },
            "kmeans": {
                "algorithm": "deterministic_kmeans",
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
            "outputs": {
                "sensor_clusters": sensor_path.as_posix(),
                "cluster_summary": summary_path.as_posix(),
                "pca_coordinates": pca_path.as_posix(),
                "metrics": metrics_path.as_posix(),
            },
        },
    )

    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "dimension": cluster_dimension,
        "k": k,
        "random_seed": random_seed,
        "cluster_dir": output_dir.as_posix(),
        "sensor_clusters_path": sensor_path.as_posix(),
        "cluster_summary_path": summary_path.as_posix(),
        "pca_coordinates_path": pca_path.as_posix(),
        "metrics_path": metrics_path.as_posix(),
        "row_count": len(feature_rows),
        "feature_count": len(feature_columns),
        "cluster_counts": cluster_counts,
        "inertia": kmeans.inertia,
        "silhouette_score": metrics["silhouette_score"]["value"],
        "calinski_harabasz_score": metrics["calinski_harabasz_score"]["value"],
    }


def compare_cluster_drift(
    settings: AppSettings,
    from_date: date,
    to_date: date,
    source: str = "mock",
    dimension: str = "x",
    k: int = 4,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    cluster_dimension = _validate_dimension(dimension)
    if to_date < from_date:
        raise ValueError("to_date must be on or after from_date")
    if k < 1:
        raise ValueError("k must be at least 1")

    storage = get_storage_paths(settings.data_dir)
    from_dir = _cluster_dir(storage.clusters_dir, from_date, source_mode, cluster_dimension, k)
    to_dir = _cluster_dir(storage.clusters_dir, to_date, source_mode, cluster_dimension, k)
    from_rows = _load_cluster_rows(from_dir)
    to_rows = _load_cluster_rows(to_dir)
    from_metrics = read_json(from_dir / "metrics.json")
    to_metrics = read_json(to_dir / "metrics.json")

    output_dir = _drift_dir(storage.drift_dir, from_date, to_date, source_mode, cluster_dimension, k)
    output_dir.mkdir(parents=True, exist_ok=True)
    drift_path = output_dir / "cluster_drift.csv"
    centroid_path = output_dir / "centroid_drift.csv"
    metrics_path = output_dir / "metrics.json"

    drift_rows = _assignment_drift_rows(from_rows, to_rows)
    centroid_rows = _centroid_drift_rows(
        read_csv_rows(from_dir / "cluster_summary.csv"),
        read_csv_rows(to_dir / "cluster_summary.csv"),
    )
    matched_rows = [row for row in drift_rows if row["status"] == "matched"]
    changed_count = sum(1 for row in matched_rows if row["changed"] == "true")
    matched_count = len(matched_rows)
    write_csv_rows(drift_path, drift_rows, DRIFT_FIELDS)
    write_csv_rows(centroid_path, centroid_rows, CENTROID_DRIFT_FIELDS)
    write_json(
        metrics_path,
        {
            "schema_version": DRIFT_SCHEMA_VERSION,
            "source": source_mode,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "dimension": cluster_dimension,
            "k": k,
            "built_at": _utc_now(),
            "from_cluster_dir": from_dir.as_posix(),
            "to_cluster_dir": to_dir.as_posix(),
            "matched_sensor_count": matched_count,
            "changed_sensor_count": changed_count,
            "unchanged_sensor_count": matched_count - changed_count,
            "from_only_sensor_count": sum(1 for row in drift_rows if row["status"] == "from_only"),
            "to_only_sensor_count": sum(1 for row in drift_rows if row["status"] == "to_only"),
            "changed_ratio": changed_count / matched_count if matched_count else None,
            "from_inertia": (from_metrics.get("kmeans") or {}).get("inertia"),
            "to_inertia": (to_metrics.get("kmeans") or {}).get("inertia"),
            "outputs": {
                "cluster_drift": drift_path.as_posix(),
                "centroid_drift": centroid_path.as_posix(),
                "metrics": metrics_path.as_posix(),
            },
        },
    )

    return {
        "source": source_mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "dimension": cluster_dimension,
        "k": k,
        "drift_dir": output_dir.as_posix(),
        "cluster_drift_path": drift_path.as_posix(),
        "centroid_drift_path": centroid_path.as_posix(),
        "metrics_path": metrics_path.as_posix(),
        "matched_sensor_count": matched_count,
        "changed_sensor_count": changed_count,
        "changed_ratio": changed_count / matched_count if matched_count else None,
    }


def _ensure_feature_matrix(
    settings: AppSettings,
    run_date: date,
    source: str,
    dimension: str,
) -> dict[str, Any]:
    metadata_path = _feature_metadata_path(settings, run_date, source)
    matrix_path = _feature_matrix_path(settings, run_date, source, dimension)
    if not metadata_path.exists() or not matrix_path.exists():
        build_feature_preview(settings=settings, run_date=run_date, source=source, axis=dimension)

    metadata = read_json(metadata_path)
    dimension_summary = (metadata.get("dimensions") or {}).get(dimension)
    if not isinstance(dimension_summary, dict):
        raise ValueError(f"feature metadata is missing dimension {dimension!r}")
    if dimension_summary.get("status") != "ready":
        raise ValueError(
            f"feature matrix for dimension {dimension} is not ready; "
            f"status={dimension_summary.get('status')}"
        )
    return dimension_summary


def _feature_metadata_path(settings: AppSettings, run_date: date, source: str):
    return get_storage_paths(settings.data_dir).feature_dir(run_date.isoformat(), source) / "metadata.json"


def _feature_matrix_path(settings: AppSettings, run_date: date, source: str, dimension: str):
    return (
        get_storage_paths(settings.data_dir).feature_dir(run_date.isoformat(), source)
        / f"feature_matrix_{dimension}.csv"
    )


def _feature_columns(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return []
    return [field for field in rows[0] if field not in IDENTIFIER_FIELDS]


def _assignment_drift_rows(
    from_rows: list[dict[str, str]],
    to_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    from_by_id = {row["installation_point_id"]: row for row in from_rows}
    to_by_id = {row["installation_point_id"]: row for row in to_rows}
    output: list[dict[str, Any]] = []
    for installation_id in sorted(set(from_by_id) | set(to_by_id), key=_sort_key):
        from_row = from_by_id.get(installation_id, {})
        to_row = to_by_id.get(installation_id, {})
        status = "matched" if from_row and to_row else "from_only" if from_row else "to_only"
        from_cluster = from_row.get("cluster", "")
        to_cluster = to_row.get("cluster", "")
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
                "changed": "true" if status == "matched" and from_cluster != to_cluster else "false",
                "from_distance_to_centroid": from_row.get("distance_to_centroid", ""),
                "to_distance_to_centroid": to_row.get("distance_to_centroid", ""),
            }
        )
    return output


def _centroid_drift_rows(
    from_summary: list[dict[str, str]],
    to_summary: list[dict[str, str]],
) -> list[dict[str, Any]]:
    from_by_cluster = {row["cluster"]: row for row in from_summary}
    to_by_cluster = {row["cluster"]: row for row in to_summary}
    centroid_columns = sorted(
        set().union(
            *[
                {field for field in row if field.startswith("centroid_scaled_")}
                for row in [*from_summary, *to_summary]
            ]
        )
    )
    output: list[dict[str, Any]] = []
    for cluster in sorted(set(from_by_cluster) | set(to_by_cluster), key=_sort_key):
        from_row = from_by_cluster.get(cluster, {})
        to_row = to_by_cluster.get(cluster, {})
        output.append(
            {
                "cluster": cluster,
                "from_sensor_count": from_row.get("sensor_count", 0),
                "to_sensor_count": to_row.get("sensor_count", 0),
                "centroid_distance": _centroid_distance(from_row, to_row, centroid_columns),
            }
        )
    return output


def _centroid_distance(from_row: dict[str, str], to_row: dict[str, str], columns: list[str]) -> float | None:
    if not from_row or not to_row:
        return None
    return sqrt(
        sum(
            (
                engine.float_value(from_row.get(column))
                - engine.float_value(to_row.get(column))
            )
            ** 2
            for column in columns
        )
    )


def _load_cluster_rows(cluster_dir) -> list[dict[str, str]]:
    path = cluster_dir / "sensor_clusters.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing cluster artifact: {path}")
    return read_csv_rows(path)


def _cluster_dir(root, run_date: date, source: str, dimension: str, k: int):
    return root / f"date={run_date.isoformat()}_source={source}_dimension={dimension}_k={k}"


def _drift_dir(root, from_date: date, to_date: date, source: str, dimension: str, k: int):
    return root / (
        f"from={from_date.isoformat()}_to={to_date.isoformat()}_"
        f"source={source}_dimension={dimension}_k={k}"
    )


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    return source_mode


def _validate_dimension(dimension: str) -> str:
    cluster_dimension = dimension.strip().lower()
    if cluster_dimension not in VALID_CLUSTER_DIMENSIONS:
        allowed = ", ".join(sorted(VALID_CLUSTER_DIMENSIONS))
        raise ValueError(f"dimension must be one of: {allowed}")
    return cluster_dimension


def _sort_key(value: Any) -> tuple[int, Any]:
    raw = str(value)
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

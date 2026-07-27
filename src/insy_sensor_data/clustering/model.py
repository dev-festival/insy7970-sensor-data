from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import isfinite, sqrt
from random import Random
from typing import Any

from insy_sensor_data.artifacts import read_csv_rows, read_json, write_csv_rows, write_json
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


@dataclass(frozen=True)
class ScaledMatrix:
    values: list[list[float]]
    means: dict[str, float]
    scales: dict[str, float]


@dataclass(frozen=True)
class KMeansResult:
    labels: list[int]
    centroids: list[list[float]]
    distances: list[float]
    inertia: float
    iterations: int
    converged: bool


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

    storage = get_storage_paths(settings.data_dir)
    output_dir = _cluster_dir(storage.clusters_dir, run_date, source_mode, cluster_dimension, k)
    output_dir.mkdir(parents=True, exist_ok=True)
    sensor_path = output_dir / "sensor_clusters.csv"
    summary_path = output_dir / "cluster_summary.csv"
    pca_path = output_dir / "pca_coordinates.csv"
    metrics_path = output_dir / "metrics.json"

    sensor_rows = _sensor_cluster_rows(feature_rows, feature_columns, kmeans)
    summary_rows = _cluster_summary_rows(
        feature_rows=feature_rows,
        feature_columns=feature_columns,
        scaled=scaled,
        kmeans=kmeans,
        k=k,
    )
    pca_rows = _pca_rows(feature_rows, kmeans, pca)
    write_csv_rows(sensor_path, sensor_rows, [*SENSOR_CLUSTER_FIELDS, *feature_columns])
    write_csv_rows(summary_path, summary_rows, _cluster_summary_fields(feature_columns))
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


def _numeric_matrix(rows: list[dict[str, str]], feature_columns: list[str]) -> list[list[float]]:
    return [[_float(row.get(field)) for field in feature_columns] for row in rows]


def _standard_scale(matrix: list[list[float]], feature_columns: list[str]) -> ScaledMatrix:
    columns = list(zip(*matrix, strict=True))
    means = {feature: sum(values) / len(values) for feature, values in zip(feature_columns, columns, strict=True)}
    scales: dict[str, float] = {}
    for feature, values in zip(feature_columns, columns, strict=True):
        mean = means[feature]
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        scale = sqrt(variance)
        scales[feature] = scale if scale > 0 else 1.0
    scaled = [
        [
            (value - means[feature]) / scales[feature]
            for feature, value in zip(feature_columns, row, strict=True)
        ]
        for row in matrix
    ]
    return ScaledMatrix(values=scaled, means=means, scales=scales)


def _kmeans(
    matrix: list[list[float]],
    k: int,
    random_seed: int,
    max_iterations: int,
) -> KMeansResult:
    centroids = _initial_centroids(matrix, k, random_seed)
    labels = [0 for _row in matrix]
    converged = False
    for iteration in range(1, max_iterations + 1):
        labels = [_nearest_centroid(row, centroids) for row in matrix]
        next_centroids = _updated_centroids(matrix, labels, centroids, k)
        shift = sum(_squared_distance(old, new) for old, new in zip(centroids, next_centroids, strict=True))
        centroids = next_centroids
        if shift <= DEFAULT_TOLERANCE:
            converged = True
            break

    distances = [sqrt(_squared_distance(row, centroids[label])) for row, label in zip(matrix, labels, strict=True)]
    inertia = sum(distance**2 for distance in distances)
    return KMeansResult(
        labels=labels,
        centroids=centroids,
        distances=distances,
        inertia=inertia,
        iterations=iteration,
        converged=converged,
    )


def _initial_centroids(matrix: list[list[float]], k: int, random_seed: int) -> list[list[float]]:
    rng = Random(random_seed)
    first_index = rng.randrange(len(matrix))
    centroid_indexes = [first_index]
    while len(centroid_indexes) < k:
        next_index = max(
            (index for index in range(len(matrix)) if index not in centroid_indexes),
            key=lambda index: (
                min(_squared_distance(matrix[index], matrix[centroid_index]) for centroid_index in centroid_indexes),
                -index,
            ),
        )
        centroid_indexes.append(next_index)
    return [list(matrix[index]) for index in centroid_indexes]


def _nearest_centroid(row: list[float], centroids: list[list[float]]) -> int:
    return min(
        range(len(centroids)),
        key=lambda cluster: (_squared_distance(row, centroids[cluster]), cluster),
    )


def _updated_centroids(
    matrix: list[list[float]],
    labels: list[int],
    centroids: list[list[float]],
    k: int,
) -> list[list[float]]:
    updated: list[list[float]] = []
    for cluster in range(k):
        members = [row for row, label in zip(matrix, labels, strict=True) if label == cluster]
        if not members:
            updated.append(list(centroids[cluster]))
            continue
        updated.append([sum(values) / len(values) for values in zip(*members, strict=True)])
    return updated


def _sensor_cluster_rows(
    feature_rows: list[dict[str, str]],
    feature_columns: list[str],
    kmeans: KMeansResult,
) -> list[dict[str, Any]]:
    output = []
    for row, label, distance in zip(feature_rows, kmeans.labels, kmeans.distances, strict=True):
        output.append(
            {
                **{field: row.get(field, "") for field in IDENTIFIER_FIELDS},
                "cluster": label,
                "distance_to_centroid": distance,
                **{field: row.get(field, "") for field in feature_columns},
            }
        )
    return output


def _cluster_summary_rows(
    feature_rows: list[dict[str, str]],
    feature_columns: list[str],
    scaled: ScaledMatrix,
    kmeans: KMeansResult,
    k: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for cluster in range(k):
        indexes = [index for index, label in enumerate(kmeans.labels) if label == cluster]
        row: dict[str, Any] = {
            "cluster": cluster,
            "sensor_count": len(indexes),
            "sensor_fraction": len(indexes) / len(feature_rows) if feature_rows else 0,
            "within_cluster_sse": sum(kmeans.distances[index] ** 2 for index in indexes),
        }
        for feature in feature_columns:
            row[f"mean_{feature}"] = _cluster_feature_mean(feature_rows, indexes, feature)
        for feature, value in zip(feature_columns, kmeans.centroids[cluster], strict=True):
            row[f"centroid_scaled_{feature}"] = value
        output.append(row)
    return output


def _cluster_summary_fields(feature_columns: list[str]) -> list[str]:
    return [
        "cluster",
        "sensor_count",
        "sensor_fraction",
        "within_cluster_sse",
        *[f"mean_{feature}" for feature in feature_columns],
        *[f"centroid_scaled_{feature}" for feature in feature_columns],
    ]


def _cluster_feature_mean(rows: list[dict[str, str]], indexes: list[int], feature: str) -> float | None:
    if not indexes:
        return None
    values = [_float(rows[index].get(feature)) for index in indexes]
    return sum(values) / len(values)


def _pca_rows(
    feature_rows: list[dict[str, str]],
    kmeans: KMeansResult,
    pca: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    coordinates = pca["coordinates"]
    for row, label, distance, coordinate in zip(
        feature_rows,
        kmeans.labels,
        kmeans.distances,
        coordinates,
        strict=True,
    ):
        rows.append(
            {
                **{field: row.get(field, "") for field in IDENTIFIER_FIELDS},
                "cluster": label,
                "pc1": coordinate[0],
                "pc2": coordinate[1],
                "distance_to_centroid": distance,
            }
        )
    return rows


def _cluster_metrics(matrix: list[list[float]], kmeans: KMeansResult) -> dict[str, Any]:
    k = len(kmeans.centroids)
    n = len(matrix)
    return {
        "inertia": {
            "available": True,
            "value": kmeans.inertia,
        },
        "silhouette_score": _silhouette_metric(matrix, kmeans.labels, k),
        "calinski_harabasz_score": _calinski_harabasz_metric(matrix, kmeans, k, n),
    }


def _silhouette_metric(matrix: list[list[float]], labels: list[int], k: int) -> dict[str, Any]:
    n = len(matrix)
    if k < 2 or n <= k:
        return {"available": False, "value": None, "reason": "requires 2 <= k < row_count"}

    scores: list[float] = []
    for index, row in enumerate(matrix):
        own_cluster = labels[index]
        same = [
            _distance(row, matrix[other_index])
            for other_index, other_label in enumerate(labels)
            if other_label == own_cluster and other_index != index
        ]
        other_cluster_distances = []
        for cluster in range(k):
            if cluster == own_cluster:
                continue
            members = [
                _distance(row, matrix[other_index])
                for other_index, other_label in enumerate(labels)
                if other_label == cluster
            ]
            if members:
                other_cluster_distances.append(sum(members) / len(members))
        if not other_cluster_distances:
            continue
        a_value = sum(same) / len(same) if same else 0.0
        b_value = min(other_cluster_distances)
        denominator = max(a_value, b_value)
        scores.append(0.0 if denominator == 0 else (b_value - a_value) / denominator)
    if not scores:
        return {"available": False, "value": None, "reason": "no comparable clusters"}
    return {"available": True, "value": sum(scores) / len(scores), "reason": None}


def _calinski_harabasz_metric(
    matrix: list[list[float]],
    kmeans: KMeansResult,
    k: int,
    n: int,
) -> dict[str, Any]:
    if k < 2 or n <= k:
        return {"available": False, "value": None, "reason": "requires 2 <= k < row_count"}
    overall = [sum(values) / len(values) for values in zip(*matrix, strict=True)]
    between = 0.0
    for cluster in range(k):
        count = sum(1 for label in kmeans.labels if label == cluster)
        between += count * _squared_distance(kmeans.centroids[cluster], overall)
    within = kmeans.inertia
    if within <= 0:
        return {"available": False, "value": None, "reason": "within-cluster variance is zero"}
    value = (between / (k - 1)) / (within / (n - k))
    return {"available": True, "value": value, "reason": None}


def _pca_coordinates(matrix: list[list[float]]) -> dict[str, Any]:
    if not matrix:
        return {"available": False, "coordinates": [], "explained_variance_ratio": [None, None]}
    feature_count = len(matrix[0])
    if feature_count == 1:
        return {
            "available": True,
            "coordinates": [[row[0], 0.0] for row in matrix],
            "explained_variance_ratio": [1.0, 0.0],
        }

    covariance = _covariance_matrix(matrix)
    first_vector, first_value = _power_iteration(covariance, seed=1)
    deflated = _deflate(covariance, first_vector, first_value)
    second_vector, second_value = _power_iteration(deflated, seed=2)
    coordinates = [
        [_dot(row, first_vector), _dot(row, second_vector)]
        for row in matrix
    ]
    total = sum(max(value, 0.0) for value in _eigenvalue_estimates(covariance))
    ratios = (
        [first_value / total if total else None, second_value / total if total else None]
        if total
        else [None, None]
    )
    return {
        "available": True,
        "coordinates": coordinates,
        "explained_variance_ratio": ratios,
    }


def _covariance_matrix(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    feature_count = len(matrix[0])
    if n < 2:
        return [[0.0 for _ in range(feature_count)] for _ in range(feature_count)]
    return [
        [
            sum(row[i] * row[j] for row in matrix) / (n - 1)
            for j in range(feature_count)
        ]
        for i in range(feature_count)
    ]


def _power_iteration(matrix: list[list[float]], seed: int, iterations: int = 50) -> tuple[list[float], float]:
    rng = Random(seed)
    vector = _normalize([rng.random() + 0.1 for _ in matrix])
    for _iteration in range(iterations):
        next_vector = _matrix_vector(matrix, vector)
        norm = _norm(next_vector)
        if norm == 0:
            break
        vector = [value / norm for value in next_vector]
    value = _dot(vector, _matrix_vector(matrix, vector))
    return vector, max(value, 0.0)


def _deflate(matrix: list[list[float]], vector: list[float], value: float) -> list[list[float]]:
    return [
        [
            matrix[i][j] - value * vector[i] * vector[j]
            for j in range(len(matrix))
        ]
        for i in range(len(matrix))
    ]


def _eigenvalue_estimates(matrix: list[list[float]]) -> list[float]:
    return [max(matrix[index][index], 0.0) for index in range(len(matrix))]


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
            (_float(from_row.get(column)) - _float(to_row.get(column))) ** 2
            for column in columns
        )
    )


def _load_cluster_rows(cluster_dir) -> list[dict[str, str]]:
    path = cluster_dir / "sensor_clusters.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing cluster artifact: {path}")
    return read_csv_rows(path)


def _cluster_counts(labels: list[int], k: int) -> dict[str, int]:
    return {str(cluster): sum(1 for label in labels if label == cluster) for cluster in range(k)}


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


def _distance(left: list[float], right: list[float]) -> float:
    return sqrt(_squared_distance(left, right))


def _squared_distance(left: list[float], right: list[float]) -> float:
    return sum((left_value - right_value) ** 2 for left_value, right_value in zip(left, right, strict=True))


def _matrix_vector(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [_dot(row, vector) for row in matrix]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))


def _norm(values: list[float]) -> float:
    return sqrt(sum(value**2 for value in values))


def _normalize(values: list[float]) -> list[float]:
    norm = _norm(values)
    if norm == 0:
        return [1.0 if index == 0 else 0.0 for index, _value in enumerate(values)]
    return [value / norm for value in values]


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    result = float(value)
    if not isfinite(result):
        return 0.0
    return result


def _sort_key(value: Any) -> tuple[int, Any]:
    raw = str(value)
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

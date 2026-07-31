from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite, sqrt
from random import Random
from typing import Any

from insy_sensor_data.clustering.features import IDENTIFIER_FIELDS


ENGINE_SCHEMA_VERSION = 1


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


def numeric_matrix(rows: list[dict[str, Any]], feature_columns: list[str]) -> list[list[float]]:
    return [[float_value(row.get(field)) for field in feature_columns] for row in rows]


def standard_scale(matrix: list[list[float]], feature_columns: list[str]) -> ScaledMatrix:
    columns = list(zip(*matrix, strict=True))
    means = {
        feature: sum(values) / len(values)
        for feature, values in zip(feature_columns, columns, strict=True)
    }
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


def kmeans(
    matrix: list[list[float]],
    *,
    k: int,
    random_seed: int,
    max_iterations: int,
    tolerance: float,
) -> KMeansResult:
    centroids = _initial_centroids(matrix, k, random_seed)
    labels = [0 for _row in matrix]
    converged = False
    iteration = 0
    for iteration in range(1, max_iterations + 1):
        labels = [_nearest_centroid(row, centroids) for row in matrix]
        next_centroids = _updated_centroids(matrix, labels, centroids, k)
        shift = sum(
            squared_distance(old, new)
            for old, new in zip(centroids, next_centroids, strict=True)
        )
        centroids = next_centroids
        if shift <= tolerance:
            converged = True
            break
    distances = [
        sqrt(squared_distance(row, centroids[label]))
        for row, label in zip(matrix, labels, strict=True)
    ]
    return KMeansResult(
        labels=labels,
        centroids=centroids,
        distances=distances,
        inertia=sum(distance**2 for distance in distances),
        iterations=iteration,
        converged=converged,
    )


def sensor_cluster_rows(
    feature_rows: list[dict[str, Any]],
    feature_columns: list[str],
    result: KMeansResult,
) -> list[dict[str, Any]]:
    return [
        {
            **{field: row.get(field, "") for field in IDENTIFIER_FIELDS},
            "cluster": label,
            "distance_to_centroid": distance,
            **{field: row.get(field, "") for field in feature_columns},
        }
        for row, label, distance in zip(
            feature_rows,
            result.labels,
            result.distances,
            strict=True,
        )
    ]


def cluster_summary_rows(
    *,
    feature_rows: list[dict[str, Any]],
    feature_columns: list[str],
    scaled: ScaledMatrix,
    result: KMeansResult,
    k: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for cluster in range(k):
        indexes = [index for index, label in enumerate(result.labels) if label == cluster]
        row: dict[str, Any] = {
            "cluster": cluster,
            "sensor_count": len(indexes),
            "sensor_fraction": len(indexes) / len(feature_rows) if feature_rows else 0,
            "within_cluster_sse": sum(result.distances[index] ** 2 for index in indexes),
        }
        for feature in feature_columns:
            row[f"mean_{feature}"] = _cluster_feature_mean(feature_rows, indexes, feature)
        for feature, value in zip(feature_columns, result.centroids[cluster], strict=True):
            row[f"centroid_scaled_{feature}"] = value
        output.append(row)
    return output


def cluster_summary_fields(feature_columns: list[str]) -> list[str]:
    return [
        "cluster",
        "sensor_count",
        "sensor_fraction",
        "within_cluster_sse",
        *[f"mean_{feature}" for feature in feature_columns],
        *[f"centroid_scaled_{feature}" for feature in feature_columns],
    ]


def pca_rows(
    feature_rows: list[dict[str, Any]],
    result: KMeansResult,
    pca: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for row, label, distance, coordinate in zip(
        feature_rows,
        result.labels,
        result.distances,
        pca["coordinates"],
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


def cluster_metrics(matrix: list[list[float]], result: KMeansResult) -> dict[str, Any]:
    k = len(result.centroids)
    row_count = len(matrix)
    return {
        "inertia": {"available": True, "value": result.inertia},
        "silhouette_score": _silhouette_metric(matrix, result.labels, k),
        "calinski_harabasz_score": _calinski_harabasz_metric(
            matrix,
            result,
            k,
            row_count,
        ),
    }


def pca_coordinates(matrix: list[list[float]], *, iterations: int) -> dict[str, Any]:
    if not matrix:
        return {
            "available": False,
            "coordinates": [],
            "explained_variance_ratio": [None, None],
        }
    feature_count = len(matrix[0])
    if feature_count == 1:
        return {
            "available": True,
            "coordinates": [[row[0], 0.0] for row in matrix],
            "explained_variance_ratio": [1.0, 0.0],
        }
    covariance = _covariance_matrix(matrix)
    first_vector, first_value = _power_iteration(covariance, seed=1, iterations=iterations)
    deflated = _deflate(covariance, first_vector, first_value)
    second_vector, second_value = _power_iteration(deflated, seed=2, iterations=iterations)
    coordinates = [[_dot(row, first_vector), _dot(row, second_vector)] for row in matrix]
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


def cluster_counts(labels: list[int], k: int) -> dict[str, int]:
    return {
        str(cluster): sum(1 for label in labels if label == cluster)
        for cluster in range(k)
    }


def centroid_alignment_rows(
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
    for from_row in sorted(from_summary, key=lambda row: sort_key(row["cluster"])):
        from_cluster = str(from_row["cluster"])
        candidates = [
            (
                _centroid_distance(from_row, to_row, centroid_columns),
                str(to_row["cluster"]),
                to_row,
            )
            for to_row in to_summary
        ]
        candidates.sort(key=lambda item: (item[0], sort_key(item[1])))
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
                "mapping_confidence": _mapping_confidence(
                    best_distance,
                    second_distance,
                    from_row,
                    to_row,
                ),
            }
        )
    return output


def aligned_sensor_drift_rows(
    from_rows: list[dict[str, Any]],
    to_rows: list[dict[str, Any]],
    alignment: dict[str, str],
) -> list[dict[str, Any]]:
    from_by_id = {str(row["installation_point_id"]): row for row in from_rows}
    to_by_id = {str(row["installation_point_id"]): row for row in to_rows}
    output: list[dict[str, Any]] = []
    for installation_id in sorted(set(from_by_id) | set(to_by_id), key=sort_key):
        from_row = from_by_id.get(installation_id, {})
        to_row = to_by_id.get(installation_id, {})
        status = "matched" if from_row and to_row else "from_only" if from_row else "to_only"
        from_cluster = text_value(from_row.get("cluster"))
        to_cluster = text_value(to_row.get("cluster"))
        aligned_to_cluster = alignment.get(from_cluster, "")
        from_distance = optional_float(from_row.get("distance_to_centroid"))
        to_distance = optional_float(to_row.get("distance_to_centroid"))
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
                "raw_label_changed": "true" if status == "matched" and from_cluster != to_cluster else "false",
                "aligned_changed": "true" if status == "matched" and aligned_to_cluster != to_cluster else "false",
                "from_distance_to_centroid": from_distance,
                "to_distance_to_centroid": to_distance,
                "distance_delta": (
                    to_distance - from_distance
                    if from_distance is not None and to_distance is not None
                    else None
                ),
            }
        )
    return output


def float_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    result = float(value)
    return result if isfinite(result) else 0.0


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    result = float(value)
    return result if isfinite(result) else None


def text_value(value: Any) -> str:
    return "" if value in (None, "") else str(value)


def sort_key(value: Any) -> tuple[int, Any]:
    raw = str(value)
    return (0, int(raw)) if raw.isdigit() else (1, raw)


def squared_distance(left: list[float], right: list[float]) -> float:
    return sum(
        (left_value - right_value) ** 2
        for left_value, right_value in zip(left, right, strict=True)
    )


def _initial_centroids(
    matrix: list[list[float]],
    k: int,
    random_seed: int,
) -> list[list[float]]:
    rng = Random(random_seed)
    centroid_indexes = [rng.randrange(len(matrix))]
    while len(centroid_indexes) < k:
        next_index = max(
            (index for index in range(len(matrix)) if index not in centroid_indexes),
            key=lambda index: (
                min(
                    squared_distance(matrix[index], matrix[centroid_index])
                    for centroid_index in centroid_indexes
                ),
                -index,
            ),
        )
        centroid_indexes.append(next_index)
    return [list(matrix[index]) for index in centroid_indexes]


def _nearest_centroid(row: list[float], centroids: list[list[float]]) -> int:
    return min(
        range(len(centroids)),
        key=lambda cluster: (squared_distance(row, centroids[cluster]), cluster),
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
        updated.append(
            [sum(values) / len(values) for values in zip(*members, strict=True)]
            if members
            else list(centroids[cluster])
        )
    return updated


def _cluster_feature_mean(
    rows: list[dict[str, Any]],
    indexes: list[int],
    feature: str,
) -> float | None:
    if not indexes:
        return None
    values = [float_value(rows[index].get(feature)) for index in indexes]
    return sum(values) / len(values)


def _silhouette_metric(
    matrix: list[list[float]],
    labels: list[int],
    k: int,
) -> dict[str, Any]:
    row_count = len(matrix)
    if k < 2 or row_count <= k:
        return {"available": False, "value": None, "reason": "requires 2 <= k < row_count"}
    scores: list[float] = []
    for index, row in enumerate(matrix):
        own_cluster = labels[index]
        same = [
            sqrt(squared_distance(row, matrix[other_index]))
            for other_index, other_label in enumerate(labels)
            if other_label == own_cluster and other_index != index
        ]
        other_cluster_distances = []
        for cluster in range(k):
            if cluster == own_cluster:
                continue
            members = [
                sqrt(squared_distance(row, matrix[other_index]))
                for other_index, other_label in enumerate(labels)
                if other_label == cluster
            ]
            if members:
                other_cluster_distances.append(sum(members) / len(members))
        if not other_cluster_distances:
            continue
        own_distance = sum(same) / len(same) if same else 0.0
        nearest_other = min(other_cluster_distances)
        denominator = max(own_distance, nearest_other)
        scores.append(
            0.0
            if denominator == 0
            else (nearest_other - own_distance) / denominator
        )
    if not scores:
        return {"available": False, "value": None, "reason": "no comparable clusters"}
    return {"available": True, "value": sum(scores) / len(scores), "reason": None}


def _calinski_harabasz_metric(
    matrix: list[list[float]],
    result: KMeansResult,
    k: int,
    row_count: int,
) -> dict[str, Any]:
    if k < 2 or row_count <= k:
        return {"available": False, "value": None, "reason": "requires 2 <= k < row_count"}
    overall = [sum(values) / len(values) for values in zip(*matrix, strict=True)]
    between = 0.0
    for cluster in range(k):
        count = sum(1 for label in result.labels if label == cluster)
        between += count * squared_distance(result.centroids[cluster], overall)
    if result.inertia <= 0:
        return {
            "available": False,
            "value": None,
            "reason": "within-cluster variance is zero",
        }
    value = (between / (k - 1)) / (result.inertia / (row_count - k))
    return {"available": True, "value": value, "reason": None}


def _covariance_matrix(matrix: list[list[float]]) -> list[list[float]]:
    row_count = len(matrix)
    feature_count = len(matrix[0])
    if row_count < 2:
        return [[0.0 for _ in range(feature_count)] for _ in range(feature_count)]
    return [
        [
            sum(row[i] * row[j] for row in matrix) / (row_count - 1)
            for j in range(feature_count)
        ]
        for i in range(feature_count)
    ]


def _power_iteration(
    matrix: list[list[float]],
    *,
    seed: int,
    iterations: int,
) -> tuple[list[float], float]:
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


def _deflate(
    matrix: list[list[float]],
    vector: list[float],
    value: float,
) -> list[list[float]]:
    return [
        [
            matrix[i][j] - value * vector[i] * vector[j]
            for j in range(len(matrix))
        ]
        for i in range(len(matrix))
    ]


def _eigenvalue_estimates(matrix: list[list[float]]) -> list[float]:
    return [max(matrix[index][index], 0.0) for index in range(len(matrix))]


def _matrix_vector(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [_dot(row, vector) for row in matrix]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )


def _norm(values: list[float]) -> float:
    return sqrt(sum(value**2 for value in values))


def _normalize(values: list[float]) -> list[float]:
    norm = _norm(values)
    if norm == 0:
        return [1.0 if index == 0 else 0.0 for index, _value in enumerate(values)]
    return [value / norm for value in values]


def _centroid_columns(
    from_summary: list[dict[str, Any]],
    to_summary: list[dict[str, Any]],
) -> list[str]:
    from_columns = {
        field
        for row in from_summary
        for field in row
        if field.startswith("centroid_scaled_")
    }
    to_columns = {
        field
        for row in to_summary
        for field in row
        if field.startswith("centroid_scaled_")
    }
    return sorted(from_columns & to_columns)


def _centroid_distance(
    left: dict[str, Any],
    right: dict[str, Any],
    columns: list[str],
) -> float:
    return sqrt(
        sum(
            (float_value(left.get(column)) - float_value(right.get(column))) ** 2
            for column in columns
        )
    )


def _mapping_confidence(
    best_distance: float,
    second_distance: float | None,
    from_row: dict[str, Any],
    to_row: dict[str, Any],
) -> str:
    if int(from_row.get("sensor_count") or 0) == 0 or int(to_row.get("sensor_count") or 0) == 0:
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

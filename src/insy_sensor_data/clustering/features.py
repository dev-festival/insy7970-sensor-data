from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from insy_sensor_data.artifacts import read_csv_rows, read_json, write_csv_rows, write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths


AXES = ("x", "y", "z")
TEMPERATURE_DIMENSION = "temperature"
DIMENSIONS = (*AXES, TEMPERATURE_DIMENSION)
VALID_FEATURE_DIMENSIONS = {*DIMENSIONS, "all"}
VALID_FEATURE_AXES = VALID_FEATURE_DIMENSIONS
FEATURE_SCHEMA_VERSION = 2

IDENTIFIER_FIELDS = [
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "facility_id",
    "customer_asset_id",
    "installation_customer_asset_id",
    "equipment_customer_asset_id",
]

FEATURE_SUMMARY_FIELDS = [
    "dimension",
    "feature",
    "included",
    "reason",
    "non_null_count",
    "null_count",
    "imputed_count",
    "imputation_value",
    "mean",
    "min",
    "max",
]


def build_feature_preview(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
    axis: str = "all",
    min_non_null_ratio: float = 0.2,
    min_rows: int = 3,
    min_features: int = 2,
) -> dict[str, Any]:
    source_mode = source.strip().lower()
    if source_mode not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    dimension_mode = _validate_dimension(axis)
    if not 0 <= min_non_null_ratio <= 1:
        raise ValueError("min_non_null_ratio must be between 0 and 1")

    storage = get_storage_paths(settings.data_dir)
    snapshot_dir = storage.snapshot_dir(run_date.isoformat())
    snapshot_path = snapshot_dir / "sensor_snapshot.csv"
    metadata_path = snapshot_dir / "metadata.json"
    if not snapshot_path.exists() or not metadata_path.exists():
        raise FileNotFoundError(
            f"Missing snapshot artifacts for {run_date.isoformat()}; "
            "run `uv run sensor-data snapshot build` first."
        )

    snapshot_metadata = read_json(metadata_path)
    if snapshot_metadata.get("source") != source_mode:
        raise ValueError(
            f"Snapshot source {snapshot_metadata.get('source')!r} does not match requested source {source_mode!r}."
        )

    rows = read_csv_rows(snapshot_path)
    output_dir = storage.feature_dir(run_date.isoformat(), source_mode)
    output_dir.mkdir(parents=True, exist_ok=True)
    dimensions = DIMENSIONS if dimension_mode == "all" else (dimension_mode,)

    dimension_summaries: dict[str, Any] = {}
    outputs: dict[str, dict[str, str]] = {}
    for selected_dimension in dimensions:
        dimension_summary = _build_dimension_features(
            rows=rows,
            dimension=selected_dimension,
            output_dir=output_dir,
            min_non_null_ratio=min_non_null_ratio,
            min_rows=min_rows,
            min_features=min_features,
        )
        dimension_summaries[selected_dimension] = dimension_summary["readiness"]
        outputs[selected_dimension] = dimension_summary["outputs"]

    metadata = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "source": source_mode,
        "date": run_date.isoformat(),
        "built_at": _utc_now(),
        "dimension_mode": dimension_mode,
        "feature_policy": "dimension_specific_vibration_and_temperature",
        "composite_features": "excluded",
        "min_non_null_ratio": min_non_null_ratio,
        "min_rows": min_rows,
        "min_features": min_features,
        "input_snapshot": snapshot_path.as_posix(),
        "outputs": outputs,
        "dimensions": dimension_summaries,
    }
    metadata_path_out = output_dir / "metadata.json"
    write_json(metadata_path_out, metadata)

    return {
        "source": source_mode,
        "date": run_date.isoformat(),
        "dimension_mode": dimension_mode,
        "feature_policy": metadata["feature_policy"],
        "feature_dir": output_dir.as_posix(),
        "metadata_path": metadata_path_out.as_posix(),
        "dimensions": dimension_summaries,
        "outputs": outputs,
    }


def _build_dimension_features(
    rows: list[dict[str, str]],
    dimension: str,
    output_dir: Path,
    min_non_null_ratio: float,
    min_rows: int,
    min_features: int,
) -> dict[str, Any]:
    columns = list(rows[0]) if rows else []
    summary_rows = [
        _feature_summary_row(rows, column, dimension, min_non_null_ratio)
        for column in columns
    ]
    included_features = [
        row["feature"]
        for row in summary_rows
        if row["included"] == "true"
    ]
    imputation_values = {
        row["feature"]: row["imputation_value"]
        for row in summary_rows
        if row["included"] == "true"
    }
    matrix_rows = [
        _feature_matrix_row(row, included_features, imputation_values)
        for row in rows
    ]
    matrix_fields = IDENTIFIER_FIELDS + included_features
    matrix_path = output_dir / f"feature_matrix_{dimension}.csv"
    summary_path = output_dir / f"feature_summary_{dimension}.csv"
    write_csv_rows(matrix_path, matrix_rows, matrix_fields)
    write_csv_rows(summary_path, summary_rows, FEATURE_SUMMARY_FIELDS)

    warnings = _readiness_warnings(
        row_count=len(rows),
        feature_count=len(included_features),
        summary_rows=summary_rows,
        min_rows=min_rows,
        min_features=min_features,
    )
    return {
        "readiness": {
            "dimension": dimension,
            "status": "not_ready" if any(warning["level"] == "error" for warning in warnings) else "ready",
            "row_count": len(rows),
            "feature_count": len(included_features),
            "features": included_features,
            "imputed_value_count": sum(int(row["imputed_count"]) for row in summary_rows if row["included"] == "true"),
            "warnings": warnings,
            "matrix_path": matrix_path.as_posix(),
            "summary_path": summary_path.as_posix(),
        },
        "outputs": {
            "feature_matrix": matrix_path.as_posix(),
            "feature_summary": summary_path.as_posix(),
        },
    }


def _feature_summary_row(
    rows: list[dict[str, str]],
    column: str,
    dimension: str,
    min_non_null_ratio: float,
) -> dict[str, Any]:
    numeric_values = [_float_or_none(row.get(column)) for row in rows]
    non_null_values = [value for value in numeric_values if value is not None]
    null_count = len(rows) - len(non_null_values)
    reason = _feature_reason(column, dimension, len(non_null_values), len(rows), min_non_null_ratio)
    included = reason == "included"
    imputation_value = median(non_null_values) if included and non_null_values else None
    return {
        "dimension": dimension,
        "feature": column,
        "included": "true" if included else "false",
        "reason": reason,
        "non_null_count": len(non_null_values),
        "null_count": null_count,
        "imputed_count": null_count if included else 0,
        "imputation_value": imputation_value,
        "mean": _mean(non_null_values),
        "min": min(non_null_values) if non_null_values else None,
        "max": max(non_null_values) if non_null_values else None,
    }


def _feature_reason(
    column: str,
    dimension: str,
    non_null_count: int,
    row_count: int,
    min_non_null_ratio: float,
) -> str:
    if column in IDENTIFIER_FIELDS:
        return "identifier_or_label"

    if dimension in AXES and column.startswith("rms_") and column.endswith(f"_{dimension}"):
        if row_count == 0 or non_null_count / row_count < min_non_null_ratio:
            return "below_non_null_threshold"
        return "included"
    if column.startswith("rms_") and _axis_suffix(column) in AXES:
        return "axis_mismatch" if dimension in AXES else "dimension_mismatch"

    if dimension == TEMPERATURE_DIMENSION and column.startswith("temp_"):
        if row_count == 0 or non_null_count / row_count < min_non_null_ratio:
            return "below_non_null_threshold"
        return "included"
    if column.startswith("temp_"):
        return "dimension_mismatch"
    if column.startswith("impact_"):
        return "non_axis_specific"
    return "non_feature_column"


def _feature_matrix_row(
    row: dict[str, str],
    included_features: list[str],
    imputation_values: dict[str, Any],
) -> dict[str, Any]:
    output: dict[str, Any] = {field: row.get(field, "") for field in IDENTIFIER_FIELDS}
    for feature in included_features:
        value = _float_or_none(row.get(feature))
        output[feature] = value if value is not None else imputation_values[feature]
    return output


def _readiness_warnings(
    row_count: int,
    feature_count: int,
    summary_rows: list[dict[str, Any]],
    min_rows: int,
    min_features: int,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if row_count < min_rows:
        warnings.append(
            {
                "level": "error",
                "code": "too_few_rows",
                "message": f"Feature matrix has {row_count} rows; minimum is {min_rows}.",
            }
        )
    if feature_count < min_features:
        warnings.append(
            {
                "level": "error",
                "code": "too_few_features",
                "message": f"Feature matrix has {feature_count} included features; minimum is {min_features}.",
            }
        )

    for row in summary_rows:
        if row["included"] != "true":
            continue
        if row["min"] == row["max"]:
            warnings.append(
                {
                    "level": "warning",
                    "code": "zero_variance_feature",
                    "message": f"Feature {row['feature']} has zero variance.",
                    "feature": row["feature"],
                }
            )
        if int(row["imputed_count"]) > 0:
            warnings.append(
                {
                    "level": "warning",
                    "code": "imputed_missing_values",
                    "message": f"Feature {row['feature']} imputed {row['imputed_count']} missing values.",
                    "feature": row["feature"],
                    "imputed_count": row["imputed_count"],
                }
            )
    return warnings


def _axis_suffix(column: str) -> str:
    return column.rsplit("_", 1)[-1]


def _validate_dimension(dimension: str) -> str:
    normalized = dimension.strip().lower()
    if normalized not in VALID_FEATURE_DIMENSIONS:
        allowed = ", ".join(sorted(VALID_FEATURE_DIMENSIONS))
        raise ValueError(f"dimension must be one of: {allowed}")
    return normalized


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

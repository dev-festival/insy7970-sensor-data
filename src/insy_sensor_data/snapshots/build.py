from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import stdev
from typing import Any, Iterable

from insy_sensor_data.artifacts import read_json, write_csv_rows, write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths


ACCELERATION_G_TO_MPS2 = 9.8
VELOCITY_MM_S_TO_IN_S = 1 / 25.4

RMS_METRICS = {
    "acceleration": ("rms_accel", ACCELERATION_G_TO_MPS2),
    "velocity": ("rms_vel", VELOCITY_MM_S_TO_IN_S),
    "pk-pk": ("rms_pkpk", 1.0),
    "cf": ("rms_cf", 1.0),
}
AXES = ("x", "y", "z")
STATS = ("mean", "std", "max", "min")

SNAPSHOT_METADATA_FIELDS = [
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

IMPACT_FIELDS = [f"impact_{stat}" for stat in STATS]
RMS_FIELDS = [
    f"{prefix}_{stat}_{axis}"
    for _metric, (prefix, _factor) in RMS_METRICS.items()
    for stat in STATS
    for axis in AXES
]
TEMP_FIELDS = [f"temp_sensor_{stat}" for stat in STATS] + [
    f"temp_ambient_{stat}" for stat in STATS
]
SNAPSHOT_FIELDS = SNAPSHOT_METADATA_FIELDS + IMPACT_FIELDS + RMS_FIELDS + TEMP_FIELDS


def build_sensor_snapshot(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
) -> dict[str, Any]:
    if source != "mock":
        raise NotImplementedError("Only mock snapshots are implemented in sprint 0.2.0.")

    storage = get_storage_paths(settings.data_dir)
    raw_dir = storage.raw_waites_run_dir(run_date.isoformat())
    output_dir = storage.snapshot_dir(run_date.isoformat())

    equipment = read_json(raw_dir / "equipment.json")["list"]
    installation_points = read_json(raw_dir / "installation-points.json")["list"]
    rms = read_json(raw_dir / "readings-rms.json")["list"]
    impact = read_json(raw_dir / "readings-impact-vue.json")["list"]
    temperature = read_json(raw_dir / "readings-temperature.json")["list"]

    equipment_by_id = {str(row["equipment_id"]): row for row in equipment}
    points_by_id = {str(row["installation_point_id"]): row for row in installation_points}
    installation_ids = _all_installation_ids(installation_points, rms, impact, temperature)

    rms_stats = _rms_stats(rms)
    impact_stats = _single_metric_stats(
        impact,
        "impact_vue_acceleration",
        "impact",
        ACCELERATION_G_TO_MPS2,
    )
    sensor_temp_stats = _single_metric_stats(temperature, "value", "temp_sensor", _c_to_f)
    ambient_temp_stats = _single_metric_stats(temperature, "ambient", "temp_ambient", _c_to_f)

    rows: list[dict[str, Any]] = []
    for installation_id in installation_ids:
        point = points_by_id.get(installation_id, {})
        equipment_row = equipment_by_id.get(str(point.get("equipment_id")), {})
        row: dict[str, Any] = {
            "installation_point_id": installation_id,
            "installation_point_name": point.get("name"),
            "equipment_id": point.get("equipment_id"),
            "equipment_name": equipment_row.get("name"),
            "sensor_id": point.get("sensor_id"),
            "facility_id": point.get("facility_id") or equipment_row.get("facility_id"),
            "customer_asset_id": point.get("customer_asset_id") or equipment_row.get("customer_asset_id"),
            "installation_customer_asset_id": point.get("customer_asset_id"),
            "equipment_customer_asset_id": equipment_row.get("customer_asset_id"),
        }
        row.update(impact_stats.get(installation_id, {}))
        row.update(rms_stats.get(installation_id, {}))
        row.update(sensor_temp_stats.get(installation_id, {}))
        row.update(ambient_temp_stats.get(installation_id, {}))
        rows.append(row)

    snapshot_path = output_dir / "sensor_snapshot.csv"
    metadata_path = output_dir / "metadata.json"
    write_csv_rows(snapshot_path, rows, SNAPSHOT_FIELDS)
    metadata = {
        "source": source,
        "date": run_date.isoformat(),
        "built_at": _utc_now(),
        "input_dir": raw_dir.as_posix(),
        "outputs": {
            "sensor_snapshot": snapshot_path.as_posix(),
            "metadata": metadata_path.as_posix(),
        },
        "record_count": len(rows),
        "raw_record_counts": {
            "equipment": len(equipment),
            "installation-points": len(installation_points),
            "readings-rms": len(rms),
            "readings-impact-vue": len(impact),
            "readings-temperature": len(temperature),
        },
        "unit_conversions": {
            "impact_vue_acceleration": "g to m/s^2 using factor 9.8",
            "rms.acceleration": "g to m/s^2 using factor 9.8",
            "rms.velocity": "mm/s to in/s using divisor 25.4",
            "temperature.value": "C to F",
            "temperature.ambient": "C to F",
        },
    }
    write_json(metadata_path, metadata)

    return {
        "source": source,
        "date": run_date.isoformat(),
        "snapshot_path": snapshot_path.as_posix(),
        "metadata_path": metadata_path.as_posix(),
        "record_count": len(rows),
    }


def list_snapshot_dates(settings: AppSettings) -> list[str]:
    storage = get_storage_paths(settings.data_dir)
    if not storage.snapshots_dir.exists():
        return []
    return sorted(
        path.name.removeprefix("date=")
        for path in storage.snapshots_dir.glob("date=*")
        if (path / "sensor_snapshot.csv").exists()
    )


def load_snapshot(settings: AppSettings, run_date: date) -> dict[str, Any]:
    from insy_sensor_data.artifacts import read_csv_rows

    storage = get_storage_paths(settings.data_dir)
    snapshot_dir = storage.snapshot_dir(run_date.isoformat())
    return {
        "date": run_date.isoformat(),
        "metadata": read_json(snapshot_dir / "metadata.json"),
        "rows": read_csv_rows(snapshot_dir / "sensor_snapshot.csv"),
    }


def _all_installation_ids(
    installation_points: list[dict[str, Any]],
    *reading_sets: list[dict[str, Any]],
) -> list[str]:
    ids = {str(row["installation_point_id"]) for row in installation_points}
    for readings in reading_sets:
        ids.update(str(row["installation_point_id"]) for row in readings)
    return sorted(ids, key=lambda value: int(value) if value.isdigit() else value)


def _rms_stats(readings: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in readings:
        installation_id = str(row["installation_point_id"])
        axis = str(row.get("axis", "")).lower()
        if axis not in AXES:
            continue

        for metric, (prefix, factor) in RMS_METRICS.items():
            value = _numeric(row.get(metric))
            if value is not None:
                grouped[(installation_id, f"{prefix}_{axis}", metric)].append(value * factor)

    output: dict[str, dict[str, float]] = defaultdict(dict)
    for (installation_id, prefix_axis, _metric), values in grouped.items():
        prefix, axis = prefix_axis.rsplit("_", 1)
        for stat, stat_value in _stats(values).items():
            output[installation_id][f"{prefix}_{stat}_{axis}"] = stat_value
    return dict(output)


def _single_metric_stats(
    readings: list[dict[str, Any]],
    value_field: str,
    prefix: str,
    conversion: float | Any,
) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in readings:
        value = _numeric(row.get(value_field))
        if value is None:
            continue

        converted = conversion(value) if callable(conversion) else value * conversion
        grouped[str(row["installation_point_id"])].append(converted)

    return {
        installation_id: {
            f"{prefix}_{stat}": value for stat, value in _stats(values).items()
        }
        for installation_id, values in grouped.items()
    }


def _stats(values: Iterable[float]) -> dict[str, float]:
    values_list = list(values)
    if not values_list:
        return {}
    return {
        "mean": sum(values_list) / len(values_list),
        "std": stdev(values_list) if len(values_list) > 1 else 0.0,
        "max": max(values_list),
        "min": min(values_list),
    }


def _numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _c_to_f(value: float) -> float:
    return value * 1.8 + 32


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

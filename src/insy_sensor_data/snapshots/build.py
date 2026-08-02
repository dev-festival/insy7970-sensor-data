from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import stdev
from typing import Any, Iterable

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import persist_validated_waites_day
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.validate import ensure_waites_raw_valid
from insy_sensor_data.waites.asset_tree import asset_tree_records_from_payload


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

def build_sensor_snapshot(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
) -> dict[str, Any]:
    if source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")

    storage = get_storage_paths(settings.data_dir)
    raw_dir = storage.raw_waites_run_dir(run_date.isoformat())
    inputs = _load_snapshot_inputs(settings, run_date, source)
    rows = build_sensor_snapshot_rows(
        equipment=inputs["equipment"],
        installation_points=inputs["installation_points"],
        rms=inputs["rms"],
        impact=inputs["impact"],
        temperature=inputs["temperature"],
    )

    built_at = _utc_now()
    durable = persist_validated_waites_day(
        settings=settings,
        run_date=run_date,
        source=source,
        payloads=inputs["payloads"],
        snapshot_rows=rows,
        validation_report=inputs["validation"],
        manifest_path=raw_dir / "manifest.json",
        built_at=built_at,
    )
    snapshot_store = durable["snapshot_store"]
    ledger = durable["ledger"]
    store_load = {
        "database_path": durable["database_path"],
        "row_counts": durable["row_counts"],
        "staging_row_count": durable["staging_row_count"],
        "ingestion_state": durable["ingestion_state"],
    }
    metadata = {
        "source": source,
        "input_mode": "raw",
        "date": run_date.isoformat(),
        "built_at": built_at,
        "input_dir": raw_dir.as_posix(),
        "store_load": store_load,
        "outputs": {
            "sensor_snapshot": None,
            "metadata": None,
            "sensor_daily_snapshots": snapshot_store,
            "ingestion_ledger": ledger,
        },
        "record_count": len(rows),
        "validation": inputs["validation"],
        "raw_record_counts": {
            "equipment": len(inputs["equipment"]),
            "installation-points": len(inputs["installation_points"]),
            "readings-rms": len(inputs["rms"]),
            "readings-impact-vue": len(inputs["impact"]),
            "readings-temperature": len(inputs["temperature"]),
        },
        "unit_conversions": {
            "impact_vue_acceleration": "g to m/s^2 using factor 9.8",
            "rms.acceleration": "g to m/s^2 using factor 9.8",
            "rms.velocity": "mm/s to in/s using divisor 25.4",
            "temperature.value": "C to F",
            "temperature.ambient": "C to F",
        },
    }
    return {
        "source": source,
        "input_mode": "raw",
        "date": run_date.isoformat(),
        "snapshot_path": None,
        "metadata_path": None,
        "record_count": len(rows),
        "validation_status": inputs["validation"]["status"],
        "validation_warning_count": inputs["validation"]["warning_count"],
        "snapshot_store": snapshot_store,
        "ingestion_ledger": ledger,
        "metadata": metadata,
    }


def build_sensor_snapshot_rows(
    equipment: list[dict[str, Any]],
    installation_points: list[dict[str, Any]],
    rms: list[dict[str, Any]],
    impact: list[dict[str, Any]],
    temperature: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
    return rows


def _load_snapshot_inputs(
    settings: AppSettings,
    run_date: date,
    source: str,
) -> dict[str, Any]:
    storage = get_storage_paths(settings.data_dir)
    raw_dir = storage.raw_waites_run_dir(run_date.isoformat())
    validation_report = _with_validation_path_alias(
        ensure_waites_raw_valid(settings=settings, run_date=run_date, source=source)
    )
    asset_tree_payload = read_json(raw_dir / "asset-tree.json")
    payloads = {
        "asset-tree": asset_tree_records_from_payload(asset_tree_payload),
        "equipment": read_json(raw_dir / "equipment.json")["list"],
        "installation-points": read_json(raw_dir / "installation-points.json")["list"],
        "readings-rms": read_json(raw_dir / "readings-rms.json")["list"],
        "readings-impact-vue": read_json(raw_dir / "readings-impact-vue.json")["list"],
        "readings-temperature": read_json(raw_dir / "readings-temperature.json")["list"],
        "action-items": read_json(raw_dir / "action-items.json")["list"],
    }
    return {
        "equipment": payloads["equipment"],
        "installation_points": payloads["installation-points"],
        "rms": payloads["readings-rms"],
        "impact": payloads["readings-impact-vue"],
        "temperature": payloads["readings-temperature"],
        "payloads": payloads,
        "validation": validation_report,
    }


def _with_validation_path_alias(report: dict[str, Any]) -> dict[str, Any]:
    output = dict(report)
    output.setdefault("path", output.get("validation_path"))
    return output


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

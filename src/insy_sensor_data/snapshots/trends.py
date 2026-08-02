from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.schema import SNAPSHOT_FIELDS
from insy_sensor_data.store.errors import StoreNotFoundError
from insy_sensor_data.store.snapshots import query_trend_rows


SENSOR_TREND_FIELDS = [
    "date",
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
    "impact_min",
    "impact_mean",
    "impact_max",
    "temp_sensor_min",
    "temp_sensor_mean",
    "temp_sensor_max",
    "temp_ambient_min",
    "temp_ambient_mean",
    "temp_ambient_max",
    "rms_vel_min_x",
    "rms_vel_mean_x",
    "rms_vel_max_x",
    "rms_vel_min_y",
    "rms_vel_mean_y",
    "rms_vel_max_y",
    "rms_vel_min_z",
    "rms_vel_mean_z",
    "rms_vel_max_z",
]

EQUIPMENT_TREND_FIELDS = [
    "date",
    "equipment_id",
    "equipment_name",
    "customer_asset_id",
    "sensor_count",
    "impact_mean_avg",
    "temp_sensor_mean_avg",
    "rms_vel_mean_x_avg",
    "rms_vel_mean_y_avg",
    "rms_vel_mean_z_avg",
]

TREND_IDENTIFIER_FIELDS = {
    "date",
    "source",
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "facility_id",
    "customer_asset_id",
    "installation_customer_asset_id",
    "equipment_customer_asset_id",
}
SQLITE_TREND_IDENTIFIER_FIELDS = [
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
]
DEFAULT_SQLITE_TREND_VALUE_FIELDS = [
    "rms_vel_min_x",
    "rms_vel_mean_x",
    "rms_vel_max_x",
]


def query_sqlite_trends(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    value_fields: Iterable[str] | None = None,
    equipment_ids: Iterable[str] | None = None,
    installation_point_ids: Iterable[str] | None = None,
    sensor_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    if source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    selected_value_fields = _validate_sqlite_trend_fields(
        value_fields or DEFAULT_SQLITE_TREND_VALUE_FIELDS
    )
    try:
        queried = query_trend_rows(
            settings,
            start_date=start_date,
            end_date=end_date,
            source=source,
            value_fields=selected_value_fields,
            equipment_ids=equipment_ids,
            installation_point_ids=installation_point_ids,
            sensor_id=sensor_id,
            customer_asset_id=customer_asset_id,
        )
    except StoreNotFoundError as exc:
        raise FileNotFoundError(str(exc)) from exc
    sensor_rows = queried["rows"]
    selected_columns = [*SQLITE_TREND_IDENTIFIER_FIELDS, *selected_value_fields]
    equipment_record_count = len(
        {
            (str(row.get("date") or ""), str(row.get("equipment_id") or ""))
            for row in sensor_rows
        }
    )
    metadata = {
        "source": source,
        "input_mode": "sqlite",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sensor_record_count": len(sensor_rows),
        "equipment_record_count": equipment_record_count,
        "skipped_dates": queried["skipped_dates"],
        "source_mismatch_dates": [],
        "selected_fields": selected_columns,
        "data_revision": queried["data_revision"],
    }
    return {
        "source": source,
        "input": "sqlite",
        "input_mode": "sqlite",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "metadata": metadata,
        "sensor_rows": sensor_rows,
    }


def equipment_trends(sensor_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in sensor_rows:
        key = (row["date"], str(row.get("equipment_id") or ""))
        grouped.setdefault(key, []).append(row)

    output: list[dict[str, Any]] = []
    for (run_date, equipment_id), rows in sorted(grouped.items()):
        first = rows[0]
        equipment_row: dict[str, Any] = {
            "date": run_date,
            "source": first.get("source"),
            "equipment_id": equipment_id,
            "equipment_name": first.get("equipment_name"),
            "customer_asset_id": first.get("customer_asset_id"),
            "sensor_count": len(rows),
        }
        for field in _numeric_trend_fields(rows):
            average = _average(row.get(field) for row in rows)
            equipment_row[field] = average
            equipment_row[f"{field}_avg"] = average
        output.append(equipment_row)
    return output


def _validate_sqlite_trend_fields(fields: Iterable[str]) -> list[str]:
    allowed = set(SNAPSHOT_FIELDS)
    selected: list[str] = []
    for field in fields:
        value = str(field)
        if value not in allowed or value in TREND_IDENTIFIER_FIELDS:
            raise ValueError(f"Unsupported SQLite trend field: {value}")
        if value not in selected:
            selected.append(value)
    if not selected:
        raise ValueError("At least one SQLite trend value field is required")
    return selected


def _numeric_trend_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for row in rows:
        for field, value in row.items():
            if field not in TREND_IDENTIFIER_FIELDS and _numeric(value) is not None:
                fields.add(field)
    return sorted(fields)


def _average(values: Iterable[Any]) -> float | None:
    numeric_values = [value for value in (_numeric(item) for item in values) if value is not None]
    return sum(numeric_values) / len(numeric_values) if numeric_values else None


def _numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

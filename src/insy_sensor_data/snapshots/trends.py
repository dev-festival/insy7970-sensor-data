from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from insy_sensor_data.artifacts import read_csv_rows, read_json, write_csv_rows, write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths


SENSOR_TREND_FIELDS = [
    "date",
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
    "impact_mean",
    "temp_sensor_mean",
    "temp_ambient_mean",
    "rms_vel_mean_x",
    "rms_vel_mean_y",
    "rms_vel_mean_z",
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


def build_trends(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str = "mock",
) -> dict[str, Any]:
    if source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    storage = get_storage_paths(settings.data_dir)
    output_dir = storage.trend_dir(start_date.isoformat(), end_date.isoformat())
    sensor_rows: list[dict[str, Any]] = []
    skipped_dates: list[str] = []
    source_mismatch_dates: list[str] = []

    for run_date in _date_range(start_date, end_date):
        snapshot_dir = storage.snapshot_dir(run_date.isoformat())
        snapshot_path = snapshot_dir / "sensor_snapshot.csv"
        metadata_path = snapshot_dir / "metadata.json"
        if not snapshot_path.exists() or not metadata_path.exists():
            skipped_dates.append(run_date.isoformat())
            continue

        metadata = read_json(metadata_path)
        if metadata.get("source") != source:
            source_mismatch_dates.append(run_date.isoformat())
            continue

        for row in read_csv_rows(snapshot_path):
            sensor_rows.append({"date": run_date.isoformat(), **_sensor_trend_row(row)})

    if not sensor_rows:
        raise FileNotFoundError(f"No snapshot artifacts found for source {source} in the requested trend range.")

    equipment_rows = _equipment_trends(sensor_rows)
    sensor_path = output_dir / "sensor_trends.csv"
    equipment_path = output_dir / "equipment_trends.csv"
    metadata_path = output_dir / "metadata.json"

    write_csv_rows(sensor_path, sensor_rows, SENSOR_TREND_FIELDS)
    write_csv_rows(equipment_path, equipment_rows, EQUIPMENT_TREND_FIELDS)
    write_json(
        metadata_path,
        {
            "source": source,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "built_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "outputs": {
                "sensor_trends": sensor_path.as_posix(),
                "equipment_trends": equipment_path.as_posix(),
                "metadata": metadata_path.as_posix(),
            },
            "sensor_record_count": len(sensor_rows),
            "equipment_record_count": len(equipment_rows),
            "skipped_dates": skipped_dates,
            "source_mismatch_dates": source_mismatch_dates,
        },
    )

    return {
        "source": source,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sensor_trends_path": sensor_path.as_posix(),
        "equipment_trends_path": equipment_path.as_posix(),
        "metadata_path": metadata_path.as_posix(),
        "sensor_record_count": len(sensor_rows),
        "equipment_record_count": len(equipment_rows),
        "skipped_dates": skipped_dates,
        "source_mismatch_dates": source_mismatch_dates,
    }


def load_trends(settings: AppSettings, start_date: date, end_date: date) -> dict[str, Any]:
    storage = get_storage_paths(settings.data_dir)
    trend_dir = storage.trend_dir(start_date.isoformat(), end_date.isoformat())
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "metadata": read_json(trend_dir / "metadata.json"),
        "sensor_rows": read_csv_rows(trend_dir / "sensor_trends.csv"),
        "equipment_rows": read_csv_rows(trend_dir / "equipment_trends.csv"),
    }


def list_trend_ranges(settings: AppSettings) -> list[dict[str, str]]:
    storage = get_storage_paths(settings.data_dir)
    if not storage.trends_dir.exists():
        return []

    ranges: list[dict[str, str]] = []
    for path in sorted(storage.trends_dir.glob("start=*_end=*")):
        if not (path / "metadata.json").exists():
            continue
        name = path.name
        start_part, end_part = name.split("_end=", 1)
        ranges.append(
            {
                "start_date": start_part.removeprefix("start="),
                "end_date": end_part,
            }
        )
    return ranges


def _sensor_trend_row(row: dict[str, str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in SENSOR_TREND_FIELDS if field != "date"}


def _equipment_trends(sensor_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in sensor_rows:
        key = (row["date"], row.get("equipment_id") or "")
        grouped.setdefault(key, []).append(row)

    output: list[dict[str, Any]] = []
    for (run_date, equipment_id), rows in sorted(grouped.items()):
        first = rows[0]
        output.append(
            {
                "date": run_date,
                "equipment_id": equipment_id,
                "equipment_name": first.get("equipment_name"),
                "customer_asset_id": first.get("customer_asset_id"),
                "sensor_count": len(rows),
                "impact_mean_avg": _avg(row.get("impact_mean") for row in rows),
                "temp_sensor_mean_avg": _avg(row.get("temp_sensor_mean") for row in rows),
                "rms_vel_mean_x_avg": _avg(row.get("rms_vel_mean_x") for row in rows),
                "rms_vel_mean_y_avg": _avg(row.get("rms_vel_mean_y") for row in rows),
                "rms_vel_mean_z_avg": _avg(row.get("rms_vel_mean_z") for row in rows),
            }
        )
    return output


def _avg(values: Any) -> float | None:
    numeric_values = [float(value) for value in values if value not in (None, "")]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]

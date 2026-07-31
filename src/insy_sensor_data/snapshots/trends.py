from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

from insy_sensor_data.artifacts import read_csv_rows, read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import load_sensor_daily_snapshots
from insy_sensor_data.snapshots.build import SNAPSHOT_FIELDS
from insy_sensor_data.storage import get_storage_paths
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
VALID_TREND_INPUT_MODES = {"snapshots", "sqlite"}
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


def build_trends(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str = "mock",
    input_mode: str = "sqlite",
) -> dict[str, Any]:
    if source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if input_mode not in VALID_TREND_INPUT_MODES:
        allowed = ", ".join(sorted(VALID_TREND_INPUT_MODES))
        raise ValueError(f"input_mode must be one of: {allowed}")

    storage = get_storage_paths(settings.data_dir)
    sensor_rows: list[dict[str, Any]] = []
    skipped_dates: list[str] = []
    source_mismatch_dates: list[str] = []

    for run_date in _date_range(start_date, end_date):
        if input_mode == "sqlite":
            try:
                snapshot_rows = _sqlite_snapshot_rows(settings, run_date, source)
            except FileNotFoundError:
                skipped_dates.append(run_date.isoformat())
                continue
        else:
            snapshot_rows = _file_snapshot_rows(storage, run_date, source, skipped_dates, source_mismatch_dates)
            if snapshot_rows is None:
                continue

        for row in snapshot_rows:
            sensor_rows.append({"date": run_date.isoformat(), **_sensor_trend_row(row)})

    if not sensor_rows:
        input_label = "SQLite daily snapshots" if input_mode == "sqlite" else "snapshot artifacts"
        raise FileNotFoundError(f"No {input_label} found for source {source} in the requested trend range.")

    equipment_rows = _equipment_trends(sensor_rows)
    return {
        "source": source,
        "input_mode": input_mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sensor_trends_path": None,
        "equipment_trends_path": None,
        "metadata_path": None,
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


def query_sqlite_trends(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str = "mock",
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
    selected_columns = [
        *SQLITE_TREND_IDENTIFIER_FIELDS,
        *selected_value_fields,
    ]
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


def _file_snapshot_rows(
    storage: Any,
    run_date: date,
    source: str,
    skipped_dates: list[str],
    source_mismatch_dates: list[str],
) -> list[dict[str, str]] | None:
    snapshot_dir = storage.snapshot_dir(run_date.isoformat())
    snapshot_path = snapshot_dir / "sensor_snapshot.csv"
    metadata_path = snapshot_dir / "metadata.json"
    if not snapshot_path.exists() or not metadata_path.exists():
        skipped_dates.append(run_date.isoformat())
        return None

    metadata = read_json(metadata_path)
    if metadata.get("source") != source:
        source_mismatch_dates.append(run_date.isoformat())
        return None

    return read_csv_rows(snapshot_path)


def _sqlite_snapshot_rows(
    settings: AppSettings,
    run_date: date,
    source: str,
) -> list[dict[str, Any]]:
    return load_sensor_daily_snapshots(settings=settings, run_date=run_date, source=source)


def _sensor_trend_row(row: dict[str, str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in SENSOR_TREND_FIELDS if field != "date"}


def _sqlite_sensor_trend_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"date", "source"}}


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


def _equipment_trends(sensor_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            average = _avg(row.get(field) for row in rows)
            equipment_row[field] = average
            equipment_row[f"{field}_avg"] = average
        output.append(equipment_row)
    return output


def _avg(values: Any) -> float | None:
    numeric_values = [
        numeric_value
        for numeric_value in (_numeric(value) for value in values)
        if numeric_value is not None
    ]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


def _numeric_trend_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: set[str] = set()
    for row in rows:
        for field, value in row.items():
            if field in TREND_IDENTIFIER_FIELDS:
                continue
            if _numeric(value) is not None:
                fields.add(field)
    return sorted(fields)


def _numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from insy_sensor_data.artifacts import write_csv_rows, write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.schema import SNAPSHOT_FIELDS, SNAPSHOT_METADATA_FIELDS
from insy_sensor_data.snapshots.trends import (
    EQUIPMENT_TREND_FIELDS,
    SENSOR_TREND_FIELDS,
    _equipment_trends,
)
from insy_sensor_data.store.snapshots import load_snapshot_view, query_trend_rows


def export_snapshot_csv(
    settings: AppSettings,
    *,
    run_date: date,
    source: str,
    destination: Path,
) -> dict[str, Any]:
    payload = load_snapshot_view(
        settings,
        run_date=run_date,
        source=source,
        fields=SNAPSHOT_FIELDS,
    )
    write_csv_rows(destination, payload["rows"], SNAPSHOT_FIELDS)
    return {
        "source": source,
        "date": run_date.isoformat(),
        "destination": destination.as_posix(),
        "row_count": len(payload["rows"]),
        "fields": SNAPSHOT_FIELDS,
        "data_revision": payload["data_revision"],
    }


def export_trend_csvs(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    source: str,
    destination: Path,
) -> dict[str, Any]:
    value_fields = [field for field in SNAPSHOT_FIELDS if field not in SNAPSHOT_METADATA_FIELDS]
    queried = query_trend_rows(
        settings,
        start_date=start_date,
        end_date=end_date,
        source=source,
        value_fields=value_fields,
    )
    sensor_rows = [
        {field: row.get(field) for field in SENSOR_TREND_FIELDS}
        for row in queried["rows"]
    ]
    equipment_rows = _equipment_trends(sensor_rows)
    sensor_path = destination / "sensor_trends.csv"
    equipment_path = destination / "equipment_trends.csv"
    metadata_path = destination / "metadata.json"
    write_csv_rows(sensor_path, sensor_rows, SENSOR_TREND_FIELDS)
    write_csv_rows(equipment_path, equipment_rows, EQUIPMENT_TREND_FIELDS)
    metadata = {
        "source": source,
        "input_mode": "sqlite_export",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "exported_at": datetime.now(UTC).isoformat(),
        "sensor_record_count": len(sensor_rows),
        "equipment_record_count": len(equipment_rows),
        "skipped_dates": queried["skipped_dates"],
        "data_revision": queried["data_revision"],
    }
    write_json(metadata_path, metadata)
    return {
        **metadata,
        "sensor_trends_path": sensor_path.as_posix(),
        "equipment_trends_path": equipment_path.as_posix(),
        "metadata_path": metadata_path.as_posix(),
    }

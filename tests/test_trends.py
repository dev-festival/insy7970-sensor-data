from datetime import date
from pathlib import Path
import csv
import json

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites


def test_build_trends_writes_sensor_and_equipment_outputs(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    summary = build_trends(settings=settings, start_date=run_date, end_date=run_date)

    assert summary["sensor_record_count"] == 9
    assert summary["equipment_record_count"] >= 1
    trend_dir = tmp_path / "data" / "processed" / "trends" / "start=2025-07-09_end=2025-07-09"
    sensor_path = trend_dir / "sensor_trends.csv"
    equipment_path = trend_dir / "equipment_trends.csv"
    metadata_path = trend_dir / "metadata.json"
    assert sensor_path.exists()
    assert equipment_path.exists()
    assert metadata_path.exists()

    with sensor_path.open(newline="", encoding="utf-8") as csv_file:
        sensor_rows = list(csv.DictReader(csv_file))
    with equipment_path.open(newline="", encoding="utf-8") as csv_file:
        equipment_rows = list(csv.DictReader(csv_file))

    assert len(sensor_rows) == 9
    assert {"date", "installation_point_id", "impact_mean", "temp_sensor_mean"} <= set(sensor_rows[0])
    assert {"date", "equipment_id", "sensor_count", "impact_mean_avg"} <= set(equipment_rows[0])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["skipped_dates"] == []


def test_build_trends_requires_at_least_one_snapshot(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    with pytest.raises(FileNotFoundError, match="No snapshot artifacts"):
        build_trends(
            settings=settings,
            start_date=date(2025, 7, 9),
            end_date=date(2025, 7, 9),
        )

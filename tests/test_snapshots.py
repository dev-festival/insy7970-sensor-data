from datetime import date
from pathlib import Path
import csv
import json

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.waites.fetch import fetch_waites


def test_build_sensor_snapshot_writes_joined_converted_stats(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    summary = build_sensor_snapshot(settings=settings, run_date=run_date)

    assert summary["record_count"] == 9
    snapshot_path = tmp_path / "data" / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv"
    metadata_path = tmp_path / "data" / "processed" / "snapshots" / "date=2025-07-09" / "metadata.json"
    assert snapshot_path.exists()
    assert metadata_path.exists()

    rows = _rows_by_installation_id(snapshot_path)
    row = rows["201300"]
    assert row["installation_point_name"] == "Bottom Shaft - NDE"
    assert row["equipment_name"] == "BL - Aluminium Pinch Roll"
    assert row["sensor_id"] == "11414411"
    assert row["customer_asset_id"] == "LEVF412TS"
    assert float(row["impact_mean"]) == pytest.approx(((2.01 + 2.20) / 2) * 9.8)
    assert float(row["rms_accel_mean_x"]) == pytest.approx(((0.80 + 0.82 + 0.88) / 3) * 9.8)
    assert float(row["rms_vel_mean_x"]) == pytest.approx(((1.70 + 1.72 + 1.74) / 3) / 25.4)
    assert float(row["temp_sensor_mean"]) == pytest.approx(((35.92 + 36.10) / 2) * 1.8 + 32)

    orphan = rows["201999"]
    assert orphan["equipment_id"] == ""
    assert orphan["customer_asset_id"] == ""
    assert orphan["rms_accel_mean_x"] != ""

    temp_only = rows["201307"]
    assert temp_only["temp_sensor_mean"] != ""
    assert temp_only["rms_accel_mean_x"] == ""

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["unit_conversions"]["rms.velocity"] == "mm/s to in/s using divisor 25.4"
    assert metadata["raw_record_counts"]["readings-rms"] == 21


def test_build_sensor_snapshot_requires_raw_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    with pytest.raises(FileNotFoundError, match="equipment.json"):
        build_sensor_snapshot(settings=settings, run_date=date(2025, 7, 9))


def _rows_by_installation_id(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return {row["installation_point_id"]: row for row in csv.DictReader(csv_file)}

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


def test_build_trends_with_multi_day_mock_data_shows_movement(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 11)
    for run_date in [start_date, date(2025, 7, 10), end_date]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)

    summary = build_trends(settings=settings, start_date=start_date, end_date=end_date)

    assert summary["skipped_dates"] == []
    trend_path = tmp_path / "data" / "processed" / "trends" / "start=2025-07-09_end=2025-07-11"
    with (trend_path / "sensor_trends.csv").open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    rising = [_metric(rows, raw_date, "201300", "rms_vel_mean_x") for raw_date in _trend_dates()]
    stable = [_metric(rows, raw_date, "201301", "rms_vel_mean_x") for raw_date in _trend_dates()]
    normalizing = [_metric(rows, raw_date, "201303", "impact_mean") for raw_date in _trend_dates()]
    temp_spike = [_metric(rows, raw_date, "201307", "temp_sensor_mean") for raw_date in _trend_dates()]

    assert rising[0] < rising[1] < rising[2]
    assert stable[0] == pytest.approx(stable[1])
    assert stable[1] == pytest.approx(stable[2])
    assert normalizing[0] > normalizing[1] > normalizing[2]
    assert temp_spike[1] > temp_spike[0]
    assert temp_spike[1] > temp_spike[2]
    assert _row(rows, "2025-07-10", "201305")["rms_vel_mean_x"] == ""


def test_build_trends_reports_missing_snapshot_dates(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    for run_date in [date(2025, 7, 9), date(2025, 7, 11)]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)

    summary = build_trends(
        settings=settings,
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )

    assert summary["skipped_dates"] == ["2025-07-10"]
    assert summary["sensor_record_count"] == 18


def test_build_trends_reads_api_source_snapshots_only(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)
    _rewrite_snapshot_source(tmp_path / "data", "2025-07-09", "api")

    summary = build_trends(settings=settings, start_date=run_date, end_date=run_date, source="api")

    assert summary["source"] == "api"
    assert summary["sensor_record_count"] == 9
    assert summary["source_mismatch_dates"] == []


def test_build_trends_skips_snapshot_source_mismatches(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    with pytest.raises(FileNotFoundError, match="source api"):
        build_trends(settings=settings, start_date=run_date, end_date=run_date, source="api")


def _trend_dates() -> list[str]:
    return ["2025-07-09", "2025-07-10", "2025-07-11"]


def _row(rows: list[dict[str, str]], raw_date: str, installation_point_id: str) -> dict[str, str]:
    return next(
        row
        for row in rows
        if row["date"] == raw_date and row["installation_point_id"] == installation_point_id
    )


def _metric(
    rows: list[dict[str, str]],
    raw_date: str,
    installation_point_id: str,
    metric: str,
) -> float:
    return float(_row(rows, raw_date, installation_point_id)[metric])


def _rewrite_snapshot_source(data_dir: Path, raw_date: str, source: str) -> None:
    metadata_path = data_dir / "processed" / "snapshots" / f"date={raw_date}" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source"] = source
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

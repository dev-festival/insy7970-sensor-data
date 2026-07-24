from __future__ import annotations

from datetime import date
from pathlib import Path
import csv

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import (
    connect_observation_store,
    load_waites_observations,
    observation_db_path,
    query_daily_metric_rollups,
)
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites


def test_load_waites_observations_creates_schema_and_records_counts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    summary = load_waites_observations(settings=settings, run_date=run_date)

    assert observation_db_path(settings).exists()
    assert summary["source"] == "mock"
    assert summary["schema_version"] == 1
    assert summary["row_counts"] == {
        "equipment": 6,
        "installation_points": 8,
        "rms": 21,
        "impact": 9,
        "temperature": 9,
        "action_items": 4,
    }
    assert len(summary["manifest_sha256"]) == 64
    assert _table_count(settings, "waites_rms_observations") == 21
    assert _table_count(settings, "waites_loads") == 1


def test_load_waites_observations_is_idempotent_by_source_date(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    first = load_waites_observations(settings=settings, run_date=run_date)
    second = load_waites_observations(settings=settings, run_date=run_date)

    assert first["replaced_existing"] is False
    assert second["replaced_existing"] is True
    assert _table_count(settings, "waites_rms_observations") == 21
    assert _table_count(settings, "waites_loads") == 1

    with pytest.raises(ValueError, match="already loaded"):
        load_waites_observations(settings=settings, run_date=run_date, replace=False)


def test_load_waites_observations_preserves_duplicate_native_timestamps(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    load_waites_observations(settings=settings, run_date=run_date)

    with connect_observation_store(settings) as connection:
        rows = connection.execute(
            """
            SELECT timestamp, velocity
            FROM waites_rms_observations
            WHERE source_date = ? AND installation_point_id = ? AND axis = ?
            ORDER BY timestamp, source_row_number
            """,
            ("2025-07-09", 201300, "x"),
        ).fetchall()

    assert [row["timestamp"] for row in rows] == [
        "2025-07-09T00:05:00Z",
        "2025-07-09T00:05:00Z",
        "2025-07-09T06:05:00Z",
    ]
    assert [row["velocity"] for row in rows] == [1.70, 1.72, 1.74]


def test_daily_metric_rollup_helper_returns_native_day_stats(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    load_waites_observations(settings=settings, run_date=run_date)

    rollups = query_daily_metric_rollups(
        settings=settings,
        run_date=run_date,
        metric="rms.velocity",
        installation_point_id=201300,
    )
    x_rollup = next(row for row in rollups if row["axis"] == "x")

    assert x_rollup["sample_count"] == 3
    assert x_rollup["min_value"] == pytest.approx(1.70)
    assert x_rollup["max_value"] == pytest.approx(1.74)
    assert x_rollup["mean_value"] == pytest.approx((1.70 + 1.72 + 1.74) / 3)
    assert x_rollup["first_timestamp"] == "2025-07-09T00:05:00Z"
    assert x_rollup["last_timestamp"] == "2025-07-09T06:05:00Z"


def test_build_sensor_snapshot_reads_sqlite_observations(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    load_waites_observations(settings=settings, run_date=run_date)

    summary = build_sensor_snapshot(
        settings=settings,
        run_date=run_date,
        input_mode="sqlite",
    )

    assert summary["input_mode"] == "sqlite"
    assert summary["record_count"] == 9
    rows = _rows_by_installation_id(
        tmp_path / "data" / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv"
    )
    assert float(rows["201300"]["rms_vel_mean_x"]) == pytest.approx(((1.70 + 1.72 + 1.74) / 3) / 25.4)


def test_build_trends_reads_sqlite_observations(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 11)
    for run_date in [start_date, date(2025, 7, 10), end_date]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        load_waites_observations(settings=settings, run_date=run_date)

    summary = build_trends(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        input_mode="sqlite",
    )

    assert summary["input_mode"] == "sqlite"
    assert summary["sensor_record_count"] == 27
    trend_path = tmp_path / "data" / "processed" / "trends" / "start=2025-07-09_end=2025-07-11"
    with (trend_path / "sensor_trends.csv").open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    rising = [_metric(rows, raw_date, "201300", "rms_vel_mean_x") for raw_date in _trend_dates()]
    assert rising[0] < rising[1] < rising[2]


def _table_count(settings: AppSettings, table_name: str) -> int:
    with connect_observation_store(settings) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _rows_by_installation_id(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return {row["installation_point_id"]: row for row in csv.DictReader(csv_file)}


def _trend_dates() -> list[str]:
    return ["2025-07-09", "2025-07-10", "2025-07-11"]


def _metric(
    rows: list[dict[str, str]],
    raw_date: str,
    installation_point_id: str,
    metric: str,
) -> float:
    row = next(
        row
        for row in rows
        if row["date"] == raw_date and row["installation_point_id"] == installation_point_id
    )
    return float(row[metric])

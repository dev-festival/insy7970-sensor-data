from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from insy_sensor_data.api.main import create_app
from insy_sensor_data.artifacts import read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import (
    connect_observation_store,
    load_sensor_daily_snapshots,
    persist_validated_waites_day,
)
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.store.errors import StoreMigrationRequiredError
from insy_sensor_data.store.exports import export_snapshot_csv, export_trend_csvs
from insy_sensor_data.store.schema import (
    FIXED_SNAPSHOT_TABLE,
    active_snapshot_table,
)
from insy_sensor_data.waites.asset_tree import asset_tree_records_from_payload
from insy_sensor_data.waites.fetch import fetch_waites


def test_direct_ingestion_is_idempotent_and_writes_no_routine_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    first = build_sensor_snapshot(settings=settings, run_date=run_date)
    second = build_sensor_snapshot(settings=settings, run_date=run_date)

    assert first["snapshot_store"]["snapshot_revision"] == second["snapshot_store"]["snapshot_revision"]
    assert second["metadata"]["store_load"]["staging_row_count"] == 0
    with connect_observation_store(settings) as connection:
        assert _count(connection, FIXED_SNAPSHOT_TABLE) == 9
        assert _count(connection, "waites_installation_point_reference") == 8
        assert _count(connection, "waites_events") == 4
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "sensor_daily_snapshots" not in tables
        assert "waites_rms_observations" not in tables
        assert "waites_temperature_observations" not in tables
        assert "waites_impact_observations" not in tables
        state = connection.execute(
            "SELECT state FROM ingestion_runs WHERE source = 'mock' AND source_date = '2025-07-09'"
        ).fetchone()[0]
        assert state == "complete"
    assert not list((settings.data_dir / "processed" / "snapshots").rglob("*"))
    assert not list((settings.data_dir / "processed" / "trends").rglob("*"))


@pytest.mark.parametrize(
    "failure_point",
    ["after_validation", "after_references", "after_events", "after_snapshots"],
)
def test_direct_ingestion_failure_keeps_previous_date_atomic(
    tmp_path: Path,
    failure_point: str,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    successful = build_sensor_snapshot(settings=settings, run_date=run_date)
    original_rows = load_sensor_daily_snapshots(settings, run_date, source="mock")
    changed_rows = [dict(row) for row in original_rows]
    changed_rows[0]["rms_vel_mean_x"] = 999.0
    raw_dir = settings.data_dir / "raw" / "waites" / "date=2025-07-09"

    with pytest.raises(RuntimeError, match="Injected ingestion failure"):
        persist_validated_waites_day(
            settings=settings,
            run_date=run_date,
            source="mock",
            payloads=_payloads(raw_dir),
            snapshot_rows=changed_rows,
            validation_report=read_json(raw_dir / "validation.json"),
            manifest_path=raw_dir / "manifest.json",
            failure_point=failure_point,
        )

    assert load_sensor_daily_snapshots(settings, run_date, source="mock") == original_rows
    with connect_observation_store(settings) as connection:
        run = connection.execute(
            "SELECT state FROM ingestion_runs WHERE source = 'mock' AND source_date = '2025-07-09'"
        ).fetchone()
        assert run[0] == "failed"
        ledger = connection.execute(
            "SELECT snapshot_revision FROM waites_ingestion_ledger WHERE source = 'mock' AND source_date = '2025-07-09'"
        ).fetchone()
        assert ledger[0] == successful["snapshot_store"]["snapshot_revision"]


def test_clean_store_has_one_fixed_snapshot_authority(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    with connect_observation_store(settings) as connection:
        assert active_snapshot_table(connection) == FIXED_SNAPSHOT_TABLE
        assert connection.execute(
            "SELECT snapshot_authority FROM operational_store_state WHERE state_id = 1"
        ).fetchone()[0] == FIXED_SNAPSHOT_TABLE
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "sensor_daily_snapshots" not in tables


def test_exports_are_explicit_and_source_conflict_blocks_startup(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", source_mode="mock")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)
    export_dir = tmp_path / "exports"

    snapshot = export_snapshot_csv(
        settings,
        run_date=run_date,
        source="mock",
        destination=export_dir / "snapshot.csv",
    )
    trends = export_trend_csvs(
        settings,
        start_date=run_date,
        end_date=run_date,
        source="mock",
        destination=export_dir / "trends",
    )

    assert snapshot["row_count"] == 9
    assert (export_dir / "snapshot.csv").is_file()
    assert trends["sensor_record_count"] == 9
    assert (export_dir / "trends" / "sensor_trends.csv").is_file()
    with pytest.raises(StoreMigrationRequiredError, match="configured for source"):
        create_app(AppSettings(data_dir=settings.data_dir, source_mode="api"))


def _payloads(raw_dir: Path) -> dict[str, list[dict[str, object]]]:
    return {
        "asset-tree": asset_tree_records_from_payload(read_json(raw_dir / "asset-tree.json")),
        "equipment": read_json(raw_dir / "equipment.json")["list"],
        "installation-points": read_json(raw_dir / "installation-points.json")["list"],
        "readings-rms": read_json(raw_dir / "readings-rms.json")["list"],
        "readings-impact-vue": read_json(raw_dir / "readings-impact-vue.json")["list"],
        "readings-temperature": read_json(raw_dir / "readings-temperature.json")["list"],
        "action-items": read_json(raw_dir / "action-items.json")["list"],
    }


def _count(connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

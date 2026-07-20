from datetime import date
from pathlib import Path
import csv
import json

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.waites.client import WaitesApiResponse
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

    with pytest.raises(FileNotFoundError, match="Waites run directory"):
        build_sensor_snapshot(settings=settings, run_date=date(2025, 7, 9))


def test_build_sensor_snapshot_reads_api_raw_after_validation(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", waites_access_token="token-123")
    run_date = date(2025, 7, 9)
    fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=679,
        source="api",
        api_client=_FakeWaitesClient(_live_shaped_payloads()),
    )

    summary = build_sensor_snapshot(settings=settings, run_date=run_date, source="api")

    assert summary["source"] == "api"
    assert summary["record_count"] == 1
    assert summary["validation_status"] in {"valid", "valid_with_warnings"}
    snapshot_path = tmp_path / "data" / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv"
    metadata_path = tmp_path / "data" / "processed" / "snapshots" / "date=2025-07-09" / "metadata.json"
    rows = _rows_by_installation_id(snapshot_path)
    assert rows["201300"]["customer_asset_id"] == "LEVF412TS"

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["source"] == "api"
    assert metadata["validation"]["status"] in {"valid", "valid_with_warnings"}
    assert metadata["validation"]["path"].endswith("validation.json")


def _rows_by_installation_id(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return {row["installation_point_id"]: row for row in csv.DictReader(csv_file)}


class _FakeWaitesClient:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads

    def fetch(self, request: object) -> WaitesApiResponse:
        return WaitesApiResponse(
            endpoint=request.endpoint,
            status_code=200,
            elapsed_ms=7,
            payload=self.payloads[request.endpoint],
        )


def _live_shaped_payloads() -> dict[str, dict[str, object]]:
    return {
        "equipment": {
            "list": [
                {
                    "equipment_id": 55576,
                    "asset_tree_id": 12440,
                    "name": "BL - Aluminium Pinch Roll",
                    "facility_id": 679,
                    "customer_asset_id": "LEVF412TS",
                }
            ]
        },
        "installation-points": {
            "list": [
                {
                    "installation_point_id": 201300,
                    "name": "Bottom Shaft - NDE",
                    "equipment_id": 55576,
                    "sensor_id": 11414411,
                    "facility_id": 679,
                    "last_seen": "2025-07-08 13:24:18",
                    "is_route_collector": 0,
                    "idle_threshold": None,
                    "customer_asset_id": "LEVF412TS",
                    "idle_threshold_type": None,
                    "installation_date": "2024-10-01 09:00:00",
                    "alerts": [],
                }
            ]
        },
        "readings-rms": {
            "list": [
                {
                    "timestamp": "2025-07-09T00:55:42Z",
                    "installation_point_id": 201300,
                    "axis": "x",
                    "facility_id": 679,
                    "acceleration": 0.80,
                    "velocity": 1.70,
                    "pk-pk": 81.4,
                    "cf": 4.85,
                },
                {
                    "timestamp": "2025-07-09T01:55:42Z",
                    "installation_point_id": 201300,
                    "axis": "x",
                    "facility_id": 679,
                    "acceleration": 0.82,
                    "velocity": 1.74,
                    "pk-pk": 82.1,
                    "cf": 4.90,
                },
            ]
        },
        "readings-impact-vue": {
            "list": [
                {
                    "timestamp": "2025-07-09T00:59:31Z",
                    "installation_point_id": 201300,
                    "axis": "x",
                    "facility_id": 679,
                    "impact_vue_acceleration": 2.01,
                    "impact_vue_pk_pk": 45.2,
                }
            ]
        },
        "readings-temperature": {
            "list": [
                {
                    "timestamp": "2025-07-09T00:55:44Z",
                    "installation_point_id": 201300,
                    "value": 35.92,
                    "ambient": 33.25,
                    "facility_id": 679,
                }
            ]
        },
        "action-items": {
            "list": [
                {
                    "action_item_id": 9001,
                    "wo_number": "WO-1",
                    "wo_status": "OPEN",
                    "sensor_id": 11414411,
                    "type": "regular",
                    "status": "active",
                    "installation_point": {"installation_point_id": 201300},
                    "equipment": {"equipment_id": 55576},
                    "description": "Inspect vibration.",
                    "time_created": "2025-07-09T00:00:00Z",
                    "updated_at": "2025-07-09T01:00:00Z",
                    "urgency": "medium",
                    "title": "Inspection",
                    "closed_at": None,
                    "created_by": "waites",
                    "comments": [],
                    "node": None,
                    "router": None,
                    "gateway": None,
                    "facility_id": 679,
                    "location_id": 1,
                    "report": None,
                }
            ]
        },
    }

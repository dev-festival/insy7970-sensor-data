from datetime import date
from pathlib import Path
import csv
import json

from insy_sensor_data.config import AppSettings
from insy_sensor_data.waites.client import build_waites_requests, utc_day_bounds
from insy_sensor_data.waites.fetch import fetch_waites


def test_build_waites_requests_uses_expected_endpoint_params() -> None:
    requests = build_waites_requests(run_date=date(2025, 7, 9), facility_id=679)
    by_endpoint = {request.endpoint: request for request in requests}

    assert list(by_endpoint) == [
        "equipment",
        "installation-points",
        "readings-rms",
        "readings-impact-vue",
        "readings-temperature",
        "action-items",
    ]
    assert by_endpoint["equipment"].params == {"facility[]": 679}
    assert by_endpoint["readings-rms"].params["start_date"] == "2025-07-09T00:00:00Z"
    assert by_endpoint["readings-rms"].params["end_date"] == "2025-07-09T23:59:59Z"
    assert by_endpoint["action-items"].params["action_item_status"] == "active"


def test_utc_day_bounds() -> None:
    assert utc_day_bounds(date(2025, 7, 9)) == (
        "2025-07-09T00:00:00Z",
        "2025-07-09T23:59:59Z",
    )


def test_fetch_waites_mock_writes_raw_manifest_and_reference_tables(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    summary = fetch_waites(settings=settings, run_date=date(2025, 7, 9), facility_id=679)

    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    assert raw_dir.exists()
    assert summary["endpoint_count"] == 6
    assert summary["record_counts"]["installation-points"] == 8

    for filename in [
        "equipment.json",
        "installation-points.json",
        "readings-rms.json",
        "readings-impact-vue.json",
        "readings-temperature.json",
        "action-items.json",
    ]:
        payload = json.loads((raw_dir / filename).read_text(encoding="utf-8"))
        assert isinstance(payload["list"], list)

    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "mock"
    assert manifest["facility_id"] == 679
    assert manifest["date"] == "2025-07-09"
    assert len(manifest["endpoints"]) == 6
    rms_entry = next(endpoint for endpoint in manifest["endpoints"] if endpoint["name"] == "readings-rms")
    assert rms_entry["record_count"] == 21
    assert rms_entry["params"]["facility[]"] == 679
    assert rms_entry["params"]["start_date"] == "2025-07-09T00:00:00Z"

    reference_dir = tmp_path / "data" / "processed" / "waites" / "reference"
    with (reference_dir / "equipment.csv").open(newline="", encoding="utf-8") as csv_file:
        equipment_rows = list(csv.DictReader(csv_file))
    with (reference_dir / "installation_points.csv").open(newline="", encoding="utf-8") as csv_file:
        installation_rows = list(csv.DictReader(csv_file))

    assert {"equipment_id", "customer_asset_id"} <= set(equipment_rows[0])
    assert {"installation_point_id", "sensor_id", "customer_asset_id"} <= set(installation_rows[0])
    assert len(equipment_rows) == 6
    assert len(installation_rows) == 8
    assert (reference_dir / "metadata.json").exists()

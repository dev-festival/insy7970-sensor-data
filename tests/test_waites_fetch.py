from datetime import date
from pathlib import Path
import csv
import json

from insy_sensor_data.config import AppSettings
from insy_sensor_data.waites.client import WaitesApiError, WaitesApiResponse, build_waites_requests, utc_day_bounds
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.fixtures import MOCK_TREND_DATES
import pytest


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


def test_fetch_waites_mock_writes_supported_trend_dates(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    for raw_date in MOCK_TREND_DATES:
        run_date = date.fromisoformat(raw_date)
        summary = fetch_waites(settings=settings, run_date=run_date, facility_id=679)

        raw_dir = tmp_path / "data" / "raw" / "waites" / f"date={raw_date}"
        rms_payload = json.loads((raw_dir / "readings-rms.json").read_text(encoding="utf-8"))
        manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))

        assert summary["date"] == raw_date
        assert raw_dir.exists()
        assert manifest["mock_trend"]["date"] == raw_date
        assert all(row["timestamp"].startswith(raw_date) for row in rms_payload["list"])

    missing_day_raw = tmp_path / "data" / "raw" / "waites" / "date=2025-07-10"
    missing_day_rms = json.loads((missing_day_raw / "readings-rms.json").read_text(encoding="utf-8"))
    assert all(row["installation_point_id"] != 201305 for row in missing_day_rms["list"])


def test_fetch_waites_api_requires_configured_token(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", waites_access_token="")

    with pytest.raises(ValueError, match="WAITES_ACCESS_TOKEN"):
        fetch_waites(
            settings=settings,
            run_date=date(2025, 7, 9),
            facility_id=679,
            source="api",
        )


def test_fetch_waites_api_writes_raw_manifest_and_reference_tables(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        waites_access_token="token-123",
    )
    api_client = FakeWaitesClient(_api_payloads())

    summary = fetch_waites(
        settings=settings,
        run_date=date(2025, 7, 9),
        facility_id=679,
        source="api",
        api_client=api_client,
    )

    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest_text = json.dumps(manifest)

    assert summary["source"] == "api"
    assert summary["endpoint_count"] == 6
    assert summary["record_counts"]["equipment"] == 1
    assert (raw_dir / "equipment.json").exists()
    assert manifest["source"] == "api"
    assert manifest["endpoints"][0]["status_code"] == 200
    assert manifest["endpoints"][0]["elapsed_ms"] == 7
    assert "token-123" not in manifest_text
    assert "access-token" not in manifest_text
    assert (tmp_path / "data" / "processed" / "waites" / "reference" / "equipment.csv").exists()


def test_fetch_waites_api_writes_error_manifest_without_secret(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        waites_access_token="token-123",
    )
    api_client = ErrorWaitesClient(
        WaitesApiError(
            "equipment",
            "Waites API authorization failed for equipment with HTTP 401.",
            status_code=401,
            elapsed_ms=3,
        )
    )

    with pytest.raises(WaitesApiError, match="authorization failed"):
        fetch_waites(
            settings=settings,
            run_date=date(2025, 7, 9),
            facility_id=679,
            source="api",
            api_client=api_client,
        )

    manifest = json.loads(
        (tmp_path / "data" / "raw" / "waites" / "date=2025-07-09" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_text = json.dumps(manifest)
    assert manifest["endpoints"][0]["status_code"] == 401
    assert "authorization failed" in manifest["endpoints"][0]["error"]
    assert "token-123" not in manifest_text
    assert "access-token" not in manifest_text


class FakeWaitesClient:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads

    def fetch(self, request: object) -> WaitesApiResponse:
        return WaitesApiResponse(
            endpoint=request.endpoint,
            status_code=200,
            elapsed_ms=7,
            payload=self.payloads[request.endpoint],
        )


class ErrorWaitesClient:
    def __init__(self, error: WaitesApiError) -> None:
        self.error = error

    def fetch(self, _request: object) -> WaitesApiResponse:
        raise self.error


def _api_payloads() -> dict[str, dict[str, object]]:
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
                    "alerts": [],
                }
            ]
        },
        "readings-rms": {"list": []},
        "readings-impact-vue": {"list": []},
        "readings-temperature": {"list": []},
        "action-items": {"list": []},
    }

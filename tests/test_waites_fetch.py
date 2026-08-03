from datetime import date
from pathlib import Path
import csv
import json

from insy_sensor_data.config import AppSettings
from insy_sensor_data.waites.client import (
    WaitesApiError,
    WaitesApiResponse,
    build_waites_reference_requests,
    build_waites_requests,
    utc_day_bounds,
)
from insy_sensor_data.waites.fetch import fetch_waites, refresh_waites_references
from insy_sensor_data.waites.fixtures import MOCK_TREND_DATES
import pytest


def test_build_waites_requests_uses_expected_endpoint_params() -> None:
    requests = build_waites_requests(run_date=date(2025, 7, 9), facility_id=679)
    by_endpoint = {request.endpoint: request for request in requests}

    assert list(by_endpoint) == [
        "asset-tree",
        "equipment",
        "installation-points",
        "readings-rms",
        "readings-impact-vue",
        "readings-temperature",
        "action-items",
    ]
    assert by_endpoint["asset-tree"].params == {"facility[]": 679}
    assert by_endpoint["equipment"].params == {"facility[]": 679}
    assert by_endpoint["readings-rms"].params["start_date"] == "2025-07-09T00:00:00Z"
    assert by_endpoint["readings-rms"].params["end_date"] == "2025-07-09T23:59:59Z"
    assert by_endpoint["action-items"].params["action_item_status"] == "active"


def test_build_waites_reference_requests_are_facility_scoped() -> None:
    requests = build_waites_reference_requests(facility_id=679)

    assert [request.endpoint for request in requests] == [
        "asset-tree",
        "equipment",
        "installation-points",
    ]
    assert all(request.params == {"facility[]": 679} for request in requests)


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
    assert summary["endpoint_count"] == 7
    assert summary["record_counts"]["asset-tree"] == 2
    assert summary["record_counts"]["installation-points"] == 8

    for filename in [
        "asset-tree.json",
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
    assert len(manifest["endpoints"]) == 7
    rms_entry = next(endpoint for endpoint in manifest["endpoints"] if endpoint["name"] == "readings-rms")
    assert rms_entry["record_count"] == 21
    assert rms_entry["params"]["facility[]"] == 679
    assert rms_entry["params"]["start_date"] == "2025-07-09T00:00:00Z"

    reference_dir = tmp_path / "data" / "processed" / "waites" / "reference"
    assert summary["reference_outputs"] == {}
    assert not list(reference_dir.glob("*"))


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
    assert summary["endpoint_count"] == 7
    assert summary["record_counts"]["asset-tree"] == 1
    assert summary["record_counts"]["equipment"] == 1
    assert (raw_dir / "asset-tree.json").exists()
    assert (raw_dir / "equipment.json").exists()
    assert manifest["source"] == "api"
    assert manifest["endpoints"][0]["status_code"] == 200
    assert manifest["endpoints"][0]["elapsed_ms"] == 7
    assert "token-123" not in manifest_text
    assert "access-token" not in manifest_text
    assert summary["reference_outputs"] == {}


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


def test_refresh_waites_references_mock_writes_bounded_manifest_and_updates_store(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    summary = refresh_waites_references(
        settings=settings,
        source="mock",
        source_date=date(2025, 7, 9),
    )

    assert summary["status"] == "complete"
    assert summary["row_counts"] == {
        "asset_trees": 3,
        "equipment": 6,
        "installation_points": 8,
    }
    manifest_path = tmp_path / "data" / "raw" / "waites" / "reference-refresh.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["source_date"] == "2025-07-09"
    assert set(manifest["row_counts"]) == {
        "asset_trees",
        "equipment",
        "installation_points",
    }
    assert not list((tmp_path / "data" / "raw" / "waites").glob("date=*"))

    import sqlite3

    with sqlite3.connect(tmp_path / "data" / "processed" / "observations.sqlite") as connection:
        assert connection.execute(
            "SELECT name FROM waites_equipment_reference "
            "WHERE equipment_id = 55577"
        ).fetchone()[0] == " BL - Steel Pinch Roll "


def test_refresh_waites_references_api_calls_only_reference_endpoints(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", waites_access_token="token-123")
    client = ReferenceWaitesClient(_api_payloads())

    summary = refresh_waites_references(
        settings=settings,
        source="api",
        api_client=client,
        source_date=date(2025, 7, 9),
    )

    assert summary["status"] == "complete"
    assert client.endpoints == ["asset-tree", "equipment", "installation-points"]
    manifest_text = (
        tmp_path / "data" / "raw" / "waites" / "reference-refresh.json"
    ).read_text(encoding="utf-8")
    assert "token-123" not in manifest_text
    assert "access-token" not in manifest_text


def test_refresh_waites_references_invalid_payload_preserves_existing_rows(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    refresh_waites_references(settings=settings, source="mock", source_date=date(2025, 7, 9))

    invalid_payloads = _api_payloads()
    del invalid_payloads["equipment"]["list"][0]["name"]
    client = ReferenceWaitesClient(invalid_payloads)

    with pytest.raises(ValueError, match="reference validation failed"):
        refresh_waites_references(
            settings=settings,
            source="api",
            api_client=client,
            source_date=date(2025, 7, 10),
        )

    import sqlite3

    with sqlite3.connect(tmp_path / "data" / "processed" / "observations.sqlite") as connection:
        assert connection.execute(
            "SELECT name FROM waites_equipment_reference "
            "WHERE equipment_id = 55576"
        ).fetchone()[0] == "BL - Aluminium Pinch Roll"


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


class ReferenceWaitesClient(FakeWaitesClient):
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        super().__init__(payloads)
        self.endpoints: list[str] = []

    def fetch(self, request: object) -> WaitesApiResponse:
        self.endpoints.append(request.endpoint)
        return super().fetch(request)


class ErrorWaitesClient:
    def __init__(self, error: WaitesApiError) -> None:
        self.error = error

    def fetch(self, _request: object) -> WaitesApiResponse:
        raise self.error


def _api_payloads() -> dict[str, dict[str, object]]:
    return {
        "asset-tree": {
            "asset_tree_id": 12440,
            "name": "Body Line",
            "facility_id": 679,
        },
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

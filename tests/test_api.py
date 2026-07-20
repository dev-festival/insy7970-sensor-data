from pathlib import Path

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites
from datetime import date


def test_health_endpoint_returns_shared_health_payload(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", source_mode="mock")
    client = TestClient(create_app(settings=settings))

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["source_mode"] == "mock"
    assert payload["data_dir"] == str(tmp_path / "data")


def test_root_serves_static_shell(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    response = client.get("/")

    assert response.status_code == 200
    assert "INSY Sensor Data" in response.text


def test_waites_raw_runs_endpoint_lists_available_manifests(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    empty_response = client.get("/api/waites/raw-runs")
    assert empty_response.status_code == 200
    assert empty_response.json() == {"runs": [], "count": 0}

    fetch_waites(settings=settings, run_date=date(2025, 7, 9), facility_id=679)
    response = client.get("/api/waites/raw-runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["runs"][0]["date"] == "2025-07-09"
    assert payload["runs"][0]["endpoint_count"] == 6
    assert payload["runs"][0]["record_counts"]["readings-rms"] == 21


def test_snapshot_and_trend_endpoints_read_processed_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    run_date = date(2025, 7, 9)

    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=run_date, end_date=run_date)

    dates_response = client.get("/api/dates")
    assert dates_response.status_code == 200
    dates_payload = dates_response.json()
    assert dates_payload["raw_waites"] == ["2025-07-09"]
    assert dates_payload["snapshots"] == ["2025-07-09"]
    assert dates_payload["trends"] == [{"start_date": "2025-07-09", "end_date": "2025-07-09"}]

    snapshot_response = client.get("/api/snapshots/2025-07-09")
    assert snapshot_response.status_code == 200
    snapshot_payload = snapshot_response.json()
    assert snapshot_payload["metadata"]["record_count"] == 9
    assert len(snapshot_payload["rows"]) == 9

    trend_response = client.get("/api/trends?start_date=2025-07-09&end_date=2025-07-09")
    assert trend_response.status_code == 200
    trend_payload = trend_response.json()
    assert len(trend_payload["sensor_rows"]) == 9
    assert trend_payload["metadata"]["equipment_record_count"] >= 1


def test_trend_endpoint_reads_multi_day_mock_artifact(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    for run_date in [date(2025, 7, 9), date(2025, 7, 10), date(2025, 7, 11)]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=date(2025, 7, 9), end_date=date(2025, 7, 11))

    response = client.get("/api/trends?start_date=2025-07-09&end_date=2025-07-11")

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["sensor_record_count"] == 27
    assert payload["metadata"]["skipped_dates"] == []
    assert {row["date"] for row in payload["sensor_rows"]} == {
        "2025-07-09",
        "2025-07-10",
        "2025-07-11",
    }


def test_snapshot_endpoint_returns_404_for_missing_artifact(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    response = client.get("/api/snapshots/2025-07-09")

    assert response.status_code == 404

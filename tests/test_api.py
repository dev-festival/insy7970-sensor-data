from pathlib import Path

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.config import AppSettings
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

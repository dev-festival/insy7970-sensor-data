from pathlib import Path

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.config import AppSettings


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

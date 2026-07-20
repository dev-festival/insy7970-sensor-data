from __future__ import annotations

from insy_sensor_data.waites.client import (
    WaitesApiClient,
    WaitesRequest,
    build_waites_url,
)
import json


class FakeResponse:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status


def test_build_waites_url_uses_endpoint_paths_and_access_token_param() -> None:
    request = WaitesRequest(
        endpoint="readings-rms",
        filename="readings-rms.json",
        params={
            "facility[]": 679,
            "start_date": "2025-07-09T00:00:00Z",
            "end_date": "2025-07-09T23:59:59Z",
        },
    )

    url = build_waites_url("https://example.test/v1_1/", request, "token-123")

    assert url.startswith("https://example.test/v1_1/readings/rms?")
    assert "facility%5B%5D=679" in url
    assert "start_date=2025-07-09T00%3A00%3A00Z" in url
    assert "access-token=token-123" in url


def test_waites_api_client_fetches_json_with_accept_header() -> None:
    captured: dict[str, object] = {}

    def fake_transport(request: object, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["accept"] = request.get_header("Accept")
        captured["timeout"] = timeout
        return FakeResponse({"list": [{"equipment_id": 1}]})

    client = WaitesApiClient(
        base_url="https://example.test/v1_1",
        access_token="token-123",
        timeout_seconds=12,
        transport=fake_transport,
    )
    response = client.fetch(
        WaitesRequest(
            endpoint="equipment",
            filename="equipment.json",
            params={"facility[]": 679},
        )
    )

    assert response.status_code == 200
    assert response.payload == {"list": [{"equipment_id": 1}]}
    assert captured["timeout"] == 12
    assert captured["accept"] == "application/json"
    assert "access-token=token-123" in str(captured["url"])

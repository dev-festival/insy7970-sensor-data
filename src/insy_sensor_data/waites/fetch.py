from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
import json

from insy_sensor_data.config import AppSettings
from insy_sensor_data.raw_lifecycle import refresh_waites_manifest_artifacts
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.asset_tree import asset_tree_records_from_payload
from insy_sensor_data.waites.client import (
    WaitesApiClient,
    WaitesApiError,
    WaitesRequest,
    build_waites_requests,
)
from insy_sensor_data.waites.fixtures import describe_mock_trend_date, load_waites_fixture


def fetch_waites(
    settings: AppSettings,
    run_date: date,
    facility_id: int,
    source: str = "mock",
    fixture_dir: Path | None = None,
    api_client: Any | None = None,
) -> dict[str, Any]:
    if source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")

    storage = get_storage_paths(settings.data_dir)
    storage.ensure_base_dirs()
    run_dir = storage.raw_waites_run_dir(run_date.isoformat())
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source": source,
        "facility_id": facility_id,
        "date": run_date.isoformat(),
        "fetched_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "endpoints": [],
    }
    mock_trend = describe_mock_trend_date(run_date)
    if mock_trend is not None:
        manifest["mock_trend"] = mock_trend

    raw_envelopes: dict[str, dict[str, Any]] = {}
    for request in build_waites_requests(run_date=run_date, facility_id=facility_id):
        if source == "mock":
            envelope = load_waites_fixture(
                request.endpoint,
                fixture_dir=fixture_dir,
                run_date=run_date,
            )
            endpoint_manifest = _mock_manifest_entry(request, run_dir / request.filename, envelope)
        else:
            client = api_client or _build_api_client(settings)
            try:
                envelope, endpoint_manifest = _fetch_live_endpoint(client, request, run_dir / request.filename)
            except WaitesApiError as exc:
                manifest["endpoints"].append(
                    _api_error_manifest_entry(request, run_dir / request.filename, exc)
                )
                _write_json(run_dir / "manifest.json", manifest)
                raise
        raw_envelopes[request.endpoint] = envelope

        output_path = run_dir / request.filename
        _write_json(output_path, envelope)
        manifest["endpoints"].append(endpoint_manifest)

    manifest_path = run_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    refresh_waites_manifest_artifacts(settings=settings, run_date=run_date)
    reference_outputs: dict[str, str] = {}

    return {
        "source": source,
        "date": run_date.isoformat(),
        "facility_id": facility_id,
        "raw_dir": _path_string(run_dir),
        "manifest_path": _path_string(manifest_path),
        "endpoint_count": len(manifest["endpoints"]),
        "record_counts": {
            endpoint["name"]: endpoint["record_count"] for endpoint in manifest["endpoints"]
        },
        "reference_outputs": reference_outputs,
    }


def _build_api_client(settings: AppSettings) -> WaitesApiClient:
    if not settings.waites_token_configured:
        raise ValueError("WAITES_ACCESS_TOKEN must be configured for --source api.")
    return WaitesApiClient(
        base_url=settings.waites_base_url,
        access_token=settings.waites_access_token,
    )


def _fetch_live_endpoint(
    client: Any,
    request: WaitesRequest,
    output_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = client.fetch(request)
    _validate_waites_envelope(
        request.endpoint,
        response.payload,
        status_code=response.status_code,
        elapsed_ms=response.elapsed_ms,
    )

    return (
        response.payload,
        {
            "name": request.endpoint,
            "path": _path_string(output_path),
            "record_count": _record_count(request.endpoint, response.payload),
            "params": request.params,
            "status_code": response.status_code,
            "elapsed_ms": response.elapsed_ms,
        },
    )


def _mock_manifest_entry(
    request: WaitesRequest,
    output_path: Path,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": request.endpoint,
        "path": _path_string(output_path),
        "record_count": len(envelope["list"]),
        "params": request.params,
    }


def _api_error_manifest_entry(
    request: WaitesRequest,
    output_path: Path,
    exc: WaitesApiError,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": request.endpoint,
        "path": _path_string(output_path),
        "params": request.params,
        "error": str(exc),
    }
    if exc.status_code is not None:
        entry["status_code"] = exc.status_code
    if exc.elapsed_ms is not None:
        entry["elapsed_ms"] = exc.elapsed_ms
    return entry


def _validate_waites_envelope(
    endpoint: str,
    payload: dict[str, Any],
    status_code: int | None = None,
    elapsed_ms: int | None = None,
) -> None:
    if endpoint == "asset-tree":
        if asset_tree_records_from_payload(payload):
            return
        raise WaitesApiError(
            endpoint,
            "Waites API returned unsupported response shape for asset-tree: expected object with list or tree data.",
            status_code=status_code,
            elapsed_ms=elapsed_ms,
        )
    if not isinstance(payload.get("list"), list):
        raise WaitesApiError(
            endpoint,
            f"Waites API returned unsupported response shape for {endpoint}: expected object with list.",
            status_code=status_code,
            elapsed_ms=elapsed_ms,
        )


def _record_count(endpoint: str, payload: dict[str, Any]) -> int:
    if endpoint == "asset-tree":
        return len(asset_tree_records_from_payload(payload))
    return len(payload["list"])


def list_raw_waites_runs(settings: AppSettings) -> list[dict[str, Any]]:
    storage = get_storage_paths(settings.data_dir)
    if not storage.raw_waites_dir.exists():
        return []

    runs: list[dict[str, Any]] = []
    for manifest_path in sorted(storage.raw_waites_dir.glob("date=*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        runs.append(
            {
                "date": manifest.get("date"),
                "source": manifest.get("source"),
                "facility_id": manifest.get("facility_id"),
                "fetched_at": manifest.get("fetched_at"),
                "manifest_path": _path_string(manifest_path),
                "endpoint_count": len(manifest.get("endpoints", [])),
                "record_counts": {
                    endpoint.get("name"): endpoint.get("record_count")
                    for endpoint in manifest.get("endpoints", [])
                },
                "artifact_states": {
                    endpoint.get("name"): (endpoint.get("artifact") or {}).get("state")
                    for endpoint in manifest.get("endpoints", [])
                    if isinstance(endpoint, dict)
                },
            }
        )
    return runs


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _path_string(path: Path) -> str:
    return path.as_posix()

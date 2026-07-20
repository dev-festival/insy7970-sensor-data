from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
import csv
import json

from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import StoragePaths, get_storage_paths
from insy_sensor_data.waites.client import (
    WaitesApiClient,
    WaitesApiError,
    WaitesRequest,
    build_waites_requests,
)
from insy_sensor_data.waites.fixtures import describe_mock_trend_date, load_waites_fixture


REFERENCE_FIELDS = {
    "equipment": ["equipment_id", "asset_tree_id", "name", "facility_id", "customer_asset_id"],
    "installation-points": [
        "installation_point_id",
        "name",
        "equipment_id",
        "sensor_id",
        "facility_id",
        "last_seen",
        "is_route_collector",
        "idle_threshold",
        "customer_asset_id",
        "idle_threshold_type",
        "alerts",
    ],
}


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
    reference_outputs = write_waites_reference_tables(storage, raw_envelopes)

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
            "record_count": len(response.payload["list"]),
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
    if not isinstance(payload.get("list"), list):
        raise WaitesApiError(
            endpoint,
            f"Waites API returned unsupported response shape for {endpoint}: expected object with list.",
            status_code=status_code,
            elapsed_ms=elapsed_ms,
        )


def write_waites_reference_tables(
    storage: StoragePaths,
    envelopes: dict[str, dict[str, Any]],
) -> dict[str, str]:
    reference_dir = storage.waites_reference_dir()
    reference_dir.mkdir(parents=True, exist_ok=True)

    equipment_path = reference_dir / "equipment.csv"
    installation_points_path = reference_dir / "installation_points.csv"
    metadata_path = reference_dir / "metadata.json"

    _write_csv(equipment_path, envelopes["equipment"]["list"], REFERENCE_FIELDS["equipment"])
    _write_csv(
        installation_points_path,
        envelopes["installation-points"]["list"],
        REFERENCE_FIELDS["installation-points"],
    )
    _write_json(
        metadata_path,
        {
            "source": "waites",
            "tables": {
                "equipment": {
                    "path": _path_string(equipment_path),
                    "record_count": len(envelopes["equipment"]["list"]),
                },
                "installation_points": {
                    "path": _path_string(installation_points_path),
                    "record_count": len(envelopes["installation-points"]["list"]),
                },
            },
        },
    )

    return {
        "equipment": _path_string(equipment_path),
        "installation_points": _path_string(installation_points_path),
        "metadata": _path_string(metadata_path),
    }


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
            }
        )
    return runs


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = {field: _csv_value(row.get(field)) for field in fieldnames}
            writer.writerow(normalized)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return value


def _path_string(path: Path) -> str:
    return path.as_posix()

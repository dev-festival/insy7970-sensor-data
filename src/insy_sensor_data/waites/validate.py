from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
import json

from insy_sensor_data.artifacts import write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.client import ENDPOINT_FILENAMES


VALIDATION_REPORT_FILENAME = "validation.json"
NULL_WARNING_THRESHOLD = 0.80

REQUIRED_FIELDS = {
    "equipment": {"equipment_id", "asset_tree_id", "name", "facility_id", "customer_asset_id"},
    "installation-points": {
        "installation_point_id",
        "name",
        "equipment_id",
        "sensor_id",
        "facility_id",
        "last_seen",
        "customer_asset_id",
    },
    "readings-rms": {
        "timestamp",
        "installation_point_id",
        "axis",
        "facility_id",
        "acceleration",
        "velocity",
        "pk-pk",
        "cf",
    },
    "readings-impact-vue": {
        "timestamp",
        "installation_point_id",
        "axis",
        "facility_id",
        "impact_vue_acceleration",
    },
    "readings-temperature": {"timestamp", "installation_point_id", "value", "ambient", "facility_id"},
    "action-items": {"closed_at"},
}

KNOWN_OPTIONAL_FIELDS = {
    "equipment": set(),
    "installation-points": {
        "alerts",
        "idle_threshold",
        "idle_threshold_type",
        "installation_date",
        "is_route_collector",
    },
    "readings-rms": set(),
    "readings-impact-vue": {"impact_vue_pk_pk"},
    "readings-temperature": set(),
    "action-items": {
        "action_item_id",
        "action_item_status",
        "action_item_type",
        "comments",
        "created_by",
        "description",
        "equipment",
        "facility_id",
        "gateway",
        "installation_point",
        "location_id",
        "node",
        "report",
        "router",
        "sensor_id",
        "status",
        "time_created",
        "title",
        "type",
        "updated_at",
        "urgency",
        "wo_number",
        "wo_status",
    },
}

NESTED_WARNING_FIELDS = {
    "action-items": ("installation_point.installation_point_id",),
}

NULL_WARNING_EXEMPT_FIELDS = {
    "action-items": {"closed_at"},
}


def validate_waites_raw(
    settings: AppSettings,
    run_date: date,
    source: str | None = None,
) -> dict[str, Any]:
    if source is not None and source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")

    storage = get_storage_paths(settings.data_dir)
    raw_dir = storage.raw_waites_run_dir(run_date.isoformat())
    if not raw_dir.exists():
        raise FileNotFoundError(f"Missing raw Waites run directory: {raw_dir}")

    validation_path = raw_dir / VALIDATION_REPORT_FILENAME
    report: dict[str, Any] = {
        "schema_version": 1,
        "date": run_date.isoformat(),
        "expected_source": source,
        "source": None,
        "validated_at": _utc_now(),
        "raw_dir": raw_dir.as_posix(),
        "manifest_path": (raw_dir / "manifest.json").as_posix(),
        "validation_path": validation_path.as_posix(),
        "status": "invalid",
        "error_count": 0,
        "warning_count": 0,
        "endpoints": [],
        "issues": [],
    }

    manifest = _read_json_with_issue(raw_dir / "manifest.json", report, "manifest")
    manifest_entries = _manifest_entries(manifest, report) if isinstance(manifest, dict) else {}
    if isinstance(manifest, dict):
        _validate_manifest(manifest, run_date, source, report)
        report["source"] = manifest.get("source")

    for endpoint, filename in ENDPOINT_FILENAMES.items():
        endpoint_summary = _validate_endpoint(raw_dir / filename, endpoint, manifest_entries, report)
        report["endpoints"].append(endpoint_summary)

    _finalize_report(report)
    write_json(validation_path, report)
    return report


def validation_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "date": report.get("date"),
        "source": report.get("source"),
        "status": report.get("status"),
        "error_count": report.get("error_count"),
        "warning_count": report.get("warning_count"),
        "validation_path": report.get("validation_path"),
        "endpoint_record_counts": {
            endpoint.get("name"): endpoint.get("record_count")
            for endpoint in report.get("endpoints", [])
        },
        "issues": report.get("issues", []),
    }


def ensure_waites_raw_valid(
    settings: AppSettings,
    run_date: date,
    source: str,
) -> dict[str, Any]:
    report = validate_waites_raw(settings=settings, run_date=run_date, source=source)
    if report["error_count"]:
        raise ValueError(
            "Raw Waites validation failed with "
            f"{report['error_count']} error(s); see {report['validation_path']}"
        )
    return report


def _validate_manifest(
    manifest: Mapping[str, Any],
    run_date: date,
    source: str | None,
    report: dict[str, Any],
) -> None:
    required = {"source", "facility_id", "date", "fetched_at", "endpoints"}
    missing = sorted(required - set(manifest))
    if missing:
        _add_issue(report, "error", "manifest", "missing_manifest_fields", f"Missing manifest fields: {missing}")

    manifest_date = manifest.get("date")
    if manifest_date is not None and manifest_date != run_date.isoformat():
        _add_issue(
            report,
            "error",
            "manifest",
            "date_mismatch",
            f"Manifest date {manifest_date!r} does not match requested date {run_date.isoformat()!r}.",
        )

    manifest_source = manifest.get("source")
    if source is not None and manifest_source != source:
        _add_issue(
            report,
            "error",
            "manifest",
            "source_mismatch",
            f"Manifest source {manifest_source!r} does not match requested source {source!r}.",
        )

    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, list):
        _add_issue(report, "error", "manifest", "bad_manifest_endpoints", "Manifest endpoints must be a list.")


def _manifest_entries(manifest: Mapping[str, Any], report: dict[str, Any]) -> dict[str, Mapping[str, Any]]:
    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, list):
        return {}

    output: dict[str, Mapping[str, Any]] = {}
    for index, entry in enumerate(endpoints):
        if not isinstance(entry, dict):
            _add_issue(
                report,
                "error",
                "manifest",
                "bad_manifest_endpoint",
                f"Manifest endpoint at index {index} must be an object.",
            )
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            _add_issue(
                report,
                "error",
                "manifest",
                "bad_manifest_endpoint_name",
                f"Manifest endpoint at index {index} is missing a string name.",
            )
            continue
        output[name] = entry
    return output


def _validate_endpoint(
    path: Path,
    endpoint: str,
    manifest_entries: Mapping[str, Mapping[str, Any]],
    report: dict[str, Any],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": endpoint,
        "path": path.as_posix(),
        "record_count": 0,
        "manifest_record_count": None,
        "required_fields": sorted(REQUIRED_FIELDS[endpoint]),
        "extra_fields": [],
        "null_counts": {},
    }

    manifest_entry = manifest_entries.get(endpoint)
    if manifest_entry is None:
        _add_issue(report, "error", endpoint, "missing_manifest_endpoint", "Endpoint is missing from manifest.")
    else:
        summary["manifest_record_count"] = manifest_entry.get("record_count")
        _validate_manifest_endpoint(endpoint, manifest_entry, report)

    payload = _read_json_with_issue(path, report, endpoint)
    if not isinstance(payload, dict):
        return summary

    records = payload.get("list")
    if not isinstance(records, list):
        _add_issue(report, "error", endpoint, "bad_envelope", "Expected endpoint JSON object with list array.")
        return summary

    summary["record_count"] = len(records)
    if manifest_entry is not None and manifest_entry.get("record_count") != len(records):
        _add_issue(
            report,
            "error",
            endpoint,
            "record_count_mismatch",
            "Manifest record_count does not match endpoint file.",
            {"manifest_record_count": manifest_entry.get("record_count"), "actual_record_count": len(records)},
        )

    _validate_records(endpoint, records, summary, report)
    return summary


def _validate_manifest_endpoint(
    endpoint: str,
    entry: Mapping[str, Any],
    report: dict[str, Any],
) -> None:
    required = {"name", "path", "record_count", "params"}
    missing = sorted(required - set(entry))
    if missing:
        _add_issue(
            report,
            "error",
            endpoint,
            "missing_manifest_endpoint_fields",
            f"Manifest endpoint entry is missing fields: {missing}",
        )

    status_code = entry.get("status_code")
    if status_code is not None and status_code != 200:
        _add_issue(
            report,
            "error",
            endpoint,
            "endpoint_http_error",
            f"Endpoint manifest has HTTP status {status_code}.",
        )

    if "error" in entry:
        _add_issue(
            report,
            "error",
            endpoint,
            "endpoint_fetch_error",
            "Endpoint manifest contains a fetch error.",
            {"error": entry.get("error")},
        )


def _validate_records(
    endpoint: str,
    records: list[Any],
    summary: dict[str, Any],
    report: dict[str, Any],
) -> None:
    if not records:
        _add_issue(report, "warning", endpoint, "empty_endpoint", "Endpoint returned no records.")
        return

    required = REQUIRED_FIELDS[endpoint]
    known = required | KNOWN_OPTIONAL_FIELDS[endpoint]
    present_fields: set[str] = set()
    missing_counts = {field: 0 for field in required}
    null_counts = {field: 0 for field in required}
    nested_missing = {field: 0 for field in NESTED_WARNING_FIELDS.get(endpoint, ())}

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            _add_issue(
                report,
                "error",
                endpoint,
                "bad_record",
                f"Record at index {index} must be an object.",
            )
            continue

        present_fields.update(record)
        for field in required:
            if field not in record:
                missing_counts[field] += 1
            elif _is_nullish(record.get(field)):
                null_counts[field] += 1

        for dotted_field in nested_missing:
            if _get_dotted(record, dotted_field) is None:
                nested_missing[dotted_field] += 1

    extra_fields = sorted(present_fields - known)
    summary["extra_fields"] = extra_fields
    summary["null_counts"] = {field: count for field, count in sorted(null_counts.items()) if count}

    missing = {field: count for field, count in missing_counts.items() if count}
    if missing:
        _add_issue(
            report,
            "error",
            endpoint,
            "missing_required_fields",
            "Required fields are missing from one or more records.",
            {"missing_counts": missing},
        )

    null_warning_exempt = NULL_WARNING_EXEMPT_FIELDS.get(endpoint, set())
    for field, count in sorted(null_counts.items()):
        if field in null_warning_exempt:
            continue
        if count and count / len(records) >= NULL_WARNING_THRESHOLD:
            _add_issue(
                report,
                "warning",
                endpoint,
                "null_heavy_required_field",
                f"Required field {field!r} is null or blank in {count} of {len(records)} records.",
                {"field": field, "null_count": count, "record_count": len(records)},
            )

    for dotted_field, count in sorted(nested_missing.items()):
        if count:
            _add_issue(
                report,
                "warning",
                endpoint,
                "missing_nested_reference",
                f"Nested field {dotted_field!r} is missing in {count} of {len(records)} records.",
                {"field": dotted_field, "missing_count": count, "record_count": len(records)},
            )

    if extra_fields:
        _add_issue(
            report,
            "warning",
            endpoint,
            "unexpected_fields",
            "Endpoint contains fields not yet in the Waites contract.",
            {"fields": extra_fields},
        )


def _read_json_with_issue(path: Path, report: dict[str, Any], endpoint: str) -> Any:
    if not path.exists():
        _add_issue(
            report,
            "error",
            endpoint,
            "missing_file",
            f"Missing required artifact: {path}",
        )
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _add_issue(
            report,
            "error",
            endpoint,
            "invalid_json",
            f"Invalid JSON in {path}: {exc.msg}",
            {"line": exc.lineno, "column": exc.colno},
        )
        return None


def _add_issue(
    report: dict[str, Any],
    level: str,
    endpoint: str,
    code: str,
    message: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    issue: dict[str, Any] = {
        "level": level,
        "endpoint": endpoint,
        "code": code,
        "message": message,
    }
    if detail:
        issue["detail"] = dict(detail)
    report["issues"].append(issue)


def _finalize_report(report: dict[str, Any]) -> None:
    report["error_count"] = sum(1 for issue in report["issues"] if issue["level"] == "error")
    report["warning_count"] = sum(1 for issue in report["issues"] if issue["level"] == "warning")
    if report["error_count"]:
        report["status"] = "invalid"
    elif report["warning_count"]:
        report["status"] = "valid_with_warnings"
    else:
        report["status"] = "valid"


def _get_dotted(record: Mapping[str, Any], dotted_field: str) -> Any:
    current: Any = record
    for part in dotted_field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _is_nullish(value: Any) -> bool:
    return value is None or value == ""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

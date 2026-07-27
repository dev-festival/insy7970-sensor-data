from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.clustering.window import build_cluster_window
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import (
    VALID_RAW_RETENTION_MODES,
    load_waites_observations,
    purge_waites_native_observations,
    update_ingestion_retention,
    verify_sensor_daily_snapshot,
)
from insy_sensor_data.raw_lifecycle import compress_raw_waites, release_raw_waites, verify_raw_waites
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.validate import validate_waites_raw, validation_summary


def run_mock_day_workflow(
    settings: AppSettings,
    run_date: date,
    facility_id: int = 679,
    snapshot_input: str = "sqlite",
    raw_retention: str = "keep",
    keep_native: bool = False,
) -> dict[str, Any]:
    retention_mode = _validate_raw_retention(raw_retention)
    fetch_summary = fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=facility_id,
        source="mock",
    )
    validation_report = validate_waites_raw(settings=settings, run_date=run_date, source="mock")
    load_summary = load_waites_observations(settings=settings, run_date=run_date, source="mock")
    snapshot_summary = build_sensor_snapshot(
        settings=settings,
        run_date=run_date,
        source="mock",
        input_mode=snapshot_input,
    )
    retention_summary = _apply_retention(
        settings=settings,
        run_date=run_date,
        source="mock",
        snapshot_summary=snapshot_summary,
        raw_retention=retention_mode,
        keep_native=keep_native,
    )

    summary = {
        "workflow": "mock-day",
        "source": "mock",
        "date": run_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "raw_retention": retention_mode,
        "keep_native": keep_native,
        "fetch": fetch_summary,
        "validation": validation_summary(validation_report),
        "load": load_summary,
        "snapshot": snapshot_summary,
        "retention": retention_summary,
        "next_steps": [
            "Run a multi-day workflow when you want trend outputs.",
            (
                "uv run sensor-data workflow mock-trend "
                "--start-date 2025-07-09 --end-date 2025-07-11"
            ),
        ],
    }
    summary["steps"] = [
        _fetch_step(fetch_summary),
        _validation_step(summary["validation"]),
        _load_step(load_summary),
        _snapshot_step(snapshot_summary),
    ]
    if retention_summary["raw_retention_mode"] != "keep":
        summary["steps"].append(_retention_step(retention_summary))
    return summary


def run_mock_trend_workflow(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    facility_id: int = 679,
    trend_input: str = "snapshots",
    snapshot_input: str = "sqlite",
    raw_retention: str = "keep",
    keep_native: bool = False,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    retention_mode = _validate_raw_retention(raw_retention)

    days: list[dict[str, Any]] = []
    for run_date in _date_range(start_date, end_date):
        day = run_mock_day_workflow(
            settings=settings,
            run_date=run_date,
            facility_id=facility_id,
            snapshot_input=snapshot_input,
            raw_retention=retention_mode,
            keep_native=keep_native,
        )
        days.append(day)

    trend_summary = build_trends(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        source="mock",
        input_mode=trend_input,
    )

    summary = {
        "workflow": "mock-trend",
        "source": "mock",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "trend_input": trend_input,
        "raw_retention": retention_mode,
        "keep_native": keep_native,
        "days": days,
        "trend": trend_summary,
        "next_steps": [
            "Open the trend CSVs or generate the evidence report in sprint 0.2.7.",
            (
                "uv run sensor-data report mock-trend "
                f"--start-date {start_date.isoformat()} --end-date {end_date.isoformat()}"
            ),
        ],
    }
    summary["steps"] = _mock_trend_steps(days, trend_summary, trend_input)
    return summary


def run_mock_range_workflow(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    facility_id: int = 679,
    trend_input: str = "sqlite",
    snapshot_input: str = "sqlite",
    raw_retention: str = "keep",
    keep_native: bool = False,
    dimensions: list[str] | None = None,
    k: int = 4,
    skip_fetch: bool = False,
    skip_cluster: bool = False,
    force: bool = False,
    max_days: int | None = 31,
) -> dict[str, Any]:
    return _run_range_workflow(
        settings=settings,
        source="mock",
        start_date=start_date,
        end_date=end_date,
        facility_id=facility_id,
        trend_input=trend_input,
        snapshot_input=snapshot_input,
        raw_retention=raw_retention,
        keep_native=keep_native,
        dimensions=dimensions or ["x"],
        k=k,
        skip_fetch=skip_fetch,
        skip_cluster=skip_cluster,
        force=force,
        max_days=max_days,
    )


def run_api_day_workflow(
    settings: AppSettings,
    run_date: date,
    facility_id: int = 679,
    snapshot_input: str = "sqlite",
    raw_retention: str = "release",
    keep_native: bool = False,
) -> dict[str, Any]:
    retention_mode = _validate_raw_retention(raw_retention)
    fetch_summary = fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=facility_id,
        source="api",
    )
    validation_report = validate_waites_raw(settings=settings, run_date=run_date, source="api")
    verify_summary = verify_raw_waites(settings=settings, run_date=run_date)
    load_summary = load_waites_observations(settings=settings, run_date=run_date, source="api")
    snapshot_summary = build_sensor_snapshot(
        settings=settings,
        run_date=run_date,
        source="api",
        input_mode=snapshot_input,
    )
    retention_summary = _apply_retention(
        settings=settings,
        run_date=run_date,
        source="api",
        snapshot_summary=snapshot_summary,
        raw_retention=retention_mode,
        keep_native=keep_native,
    )

    summary = {
        "workflow": "api-day",
        "source": "api",
        "date": run_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "raw_retention": retention_mode,
        "keep_native": keep_native,
        "fetch": fetch_summary,
        "validation": validation_summary(validation_report),
        "verify": verify_summary,
        "load": load_summary,
        "snapshot": snapshot_summary,
        "retention": retention_summary,
        "next_steps": [
            "Review validation warnings and the ingestion ledger before using live data for decisions.",
            f"uv run sensor-data trend build --source api --input sqlite --start-date {run_date.isoformat()} --end-date {run_date.isoformat()}",
        ],
    }
    summary["steps"] = [
        _fetch_step(fetch_summary),
        _validation_step(summary["validation"]),
        _verify_step(verify_summary),
        _load_step(load_summary),
        _snapshot_step(snapshot_summary),
    ]
    if retention_summary["raw_retention_mode"] != "keep":
        summary["steps"].append(_retention_step(retention_summary))
    return summary


def run_api_range_workflow(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    facility_id: int = 679,
    trend_input: str = "sqlite",
    snapshot_input: str = "sqlite",
    raw_retention: str = "release",
    keep_native: bool = False,
    dimensions: list[str] | None = None,
    k: int = 4,
    skip_fetch: bool = False,
    skip_cluster: bool = False,
    force: bool = False,
    max_days: int | None = 31,
) -> dict[str, Any]:
    return _run_range_workflow(
        settings=settings,
        source="api",
        start_date=start_date,
        end_date=end_date,
        facility_id=facility_id,
        trend_input=trend_input,
        snapshot_input=snapshot_input,
        raw_retention=raw_retention,
        keep_native=keep_native,
        dimensions=dimensions or ["x"],
        k=k,
        skip_fetch=skip_fetch,
        skip_cluster=skip_cluster,
        force=force,
        max_days=max_days,
    )


def _run_range_workflow(
    settings: AppSettings,
    source: str,
    start_date: date,
    end_date: date,
    facility_id: int,
    trend_input: str,
    snapshot_input: str,
    raw_retention: str,
    keep_native: bool,
    dimensions: list[str],
    k: int,
    skip_fetch: bool,
    skip_cluster: bool,
    force: bool,
    max_days: int | None,
) -> dict[str, Any]:
    if source not in {"mock", "api"}:
        raise ValueError("source must be one of: api, mock")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    dates = _date_range(start_date, end_date)
    if max_days is not None and len(dates) > max_days:
        raise ValueError(
            f"range has {len(dates)} dates, which exceeds --max-days {max_days}; "
            "raise --max-days or choose a smaller range"
        )
    retention_mode = _validate_raw_retention(raw_retention)

    days = [
        _run_range_day(
            settings=settings,
            source=source,
            run_date=run_date,
            facility_id=facility_id,
            snapshot_input=snapshot_input,
            raw_retention=retention_mode,
            keep_native=keep_native,
            skip_fetch=skip_fetch,
            force=force,
        )
        for run_date in dates
    ]
    trend_summary = build_trends(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        source=source,
        input_mode=trend_input,
    )

    cluster_windows: list[dict[str, Any]] = []
    if not skip_cluster:
        for dimension in dimensions:
            cluster_windows.append(
                build_cluster_window(
                    settings=settings,
                    start_date=start_date,
                    end_date=end_date,
                    source=source,
                    dimension=dimension,
                    k=k,
                    force=force,
                )
            )

    summary = {
        "workflow": f"{source}-range",
        "source": source,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "trend_input": trend_input,
        "raw_retention": retention_mode,
        "keep_native": keep_native,
        "dimensions": dimensions,
        "k": k,
        "skip_fetch": skip_fetch,
        "skip_cluster": skip_cluster,
        "force": force,
        "max_days": max_days,
        "days": days,
        "trend": trend_summary,
        "cluster_windows": cluster_windows,
        "next_steps": [
            "Inspect cluster window quality summaries before using drift operationally.",
            (
                "uv run sensor-data cluster window "
                f"--source {source} --start-date {start_date.isoformat()} "
                f"--end-date {end_date.isoformat()} --dimension {dimensions[0]} --k {k}"
            ),
        ],
    }
    summary["steps"] = _range_steps(summary)
    return summary


def format_workflow_summary(summary: dict[str, Any]) -> str:
    lines = [_workflow_title(summary), ""]
    steps = summary.get("steps", [])
    for index, step in enumerate(steps, start=1):
        lines.append(f"[{index}/{len(steps)}] {step['title']}")
        for detail in step.get("details", []):
            lines.append(f"      {detail}")
        lines.append("")

    lines.append("Done.")
    next_steps = summary.get("next_steps") or []
    if next_steps:
        lines.append("")
        lines.append("Next:")
        for next_step in next_steps:
            lines.append(f"      {next_step}")
    return "\n".join(lines)


def _workflow_title(summary: dict[str, Any]) -> str:
    workflow = summary.get("workflow")
    if workflow == "mock-day":
        return f"Mock day workflow: {summary['date']}"
    if workflow == "mock-trend":
        return f"Mock trend workflow: {summary['start_date']} to {summary['end_date']}"
    if workflow == "api-day":
        return f"API day workflow: {summary['date']}"
    if workflow == "mock-range":
        return f"Mock range workflow: {summary['start_date']} to {summary['end_date']}"
    if workflow == "api-range":
        return f"API range workflow: {summary['start_date']} to {summary['end_date']}"
    return "Workflow"


def _fetch_step(fetch_summary: dict[str, Any]) -> dict[str, Any]:
    counts = fetch_summary.get("record_counts", {})
    return {
        "title": "Fetched Waites raw evidence",
        "details": [
            f"Raw directory: {fetch_summary.get('raw_dir')}",
            f"Endpoints: {fetch_summary.get('endpoint_count')}",
            f"RMS rows: {counts.get('readings-rms', 0)}",
            f"Temperature rows: {counts.get('readings-temperature', 0)}",
        ],
    }


def _validation_step(validation: dict[str, Any]) -> dict[str, Any]:
    details = [
        f"Status: {validation.get('status')}",
        f"Warnings: {validation.get('warning_count', 0)}",
        f"Errors: {validation.get('error_count', 0)}",
        f"Report: {validation.get('validation_path')}",
    ]
    return {"title": "Validated raw evidence", "details": details}


def _verify_step(verify_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Verified raw evidence checksums",
        "details": [
            f"Status: {verify_summary.get('status')}",
            f"Warnings: {verify_summary.get('warning_count', 0)}",
            f"Errors: {verify_summary.get('error_count', 0)}",
        ],
    }


def _load_step(load_summary: dict[str, Any]) -> dict[str, Any]:
    counts = load_summary.get("row_counts", {})
    native_count = counts.get("rms", 0) + counts.get("impact", 0) + counts.get("temperature", 0)
    return {
        "title": "Loaded SQLite observations",
        "details": [
            f"Database: {load_summary.get('database_path')}",
            f"Equipment: {counts.get('equipment', 0)}",
            f"Installation points: {counts.get('installation_points', 0)}",
            f"Native observations: {native_count}",
            f"Daily rollups: {load_summary.get('rollup_count', 0)}",
        ],
    }


def _snapshot_step(snapshot_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Built sensor snapshot",
        "details": [
            f"Input: {snapshot_summary.get('input_mode')}",
            f"Sensors: {snapshot_summary.get('record_count')}",
            f"Snapshot: {snapshot_summary.get('snapshot_path')}",
            f"SQLite snapshot rows: {(snapshot_summary.get('snapshot_store') or {}).get('row_count')}",
        ],
    }


def _retention_step(retention_summary: dict[str, Any]) -> dict[str, Any]:
    native = retention_summary.get("native") or {}
    raw = retention_summary.get("raw") or {}
    return {
        "title": "Applied retention policy",
        "details": [
            f"Mode: {retention_summary.get('raw_retention_mode')}",
            f"Raw status: {retention_summary.get('raw_retention_status')}",
            f"Native status: {retention_summary.get('native_retention_status')}",
            f"Raw files changed: {raw.get('released_count', raw.get('compressed_count', 0))}",
            f"Native rows deleted: {native.get('rows_deleted', 0)}",
        ],
    }


def _mock_trend_steps(
    days: list[dict[str, Any]],
    trend_summary: dict[str, Any],
    trend_input: str,
) -> list[dict[str, Any]]:
    warning_count = sum(int(day["validation"].get("warning_count") or 0) for day in days)
    rms_count = sum(int(day["fetch"]["record_counts"].get("readings-rms", 0)) for day in days)
    native_count = sum(
        int(day["load"]["row_counts"].get("rms", 0))
        + int(day["load"]["row_counts"].get("impact", 0))
        + int(day["load"]["row_counts"].get("temperature", 0))
        for day in days
    )
    return [
        {
            "title": "Prepared mock dates",
            "details": [
                f"Dates: {len(days)}",
                f"Raw RMS rows: {rms_count}",
                f"Validation warnings: {warning_count}",
            ],
        },
        {
            "title": "Loaded SQLite observations",
            "details": [
                f"Database: {days[-1]['load'].get('database_path') if days else ''}",
                f"Native observations: {native_count}",
            ],
        },
        {
            "title": "Built daily snapshots",
            "details": [
                f"Snapshots: {len(days)}",
                f"Sensors per completed date: {_sensor_counts(days)}",
            ],
        },
        {
            "title": "Built trend outputs",
            "details": [
                f"Input: {trend_input}",
                f"Sensor trend rows: {trend_summary.get('sensor_record_count')}",
                f"Equipment trend rows: {trend_summary.get('equipment_record_count')}",
                f"Sensor trends: {trend_summary.get('sensor_trends_path')}",
            ],
        },
    ]


def _sensor_counts(days: list[dict[str, Any]]) -> str:
    return ", ".join(str(day["snapshot"].get("record_count")) for day in days)


def _range_steps(summary: dict[str, Any]) -> list[dict[str, Any]]:
    days = summary.get("days", [])
    trend = summary.get("trend", {})
    cluster_windows = summary.get("cluster_windows", [])
    status_counts: dict[str, int] = {}
    for day in days:
        status = str(day.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
    steps = [
        {
            "title": "Prepared daily snapshots",
            "details": [
                f"Dates: {len(days)}",
                f"Statuses: {_format_counts(status_counts)}",
                f"Sensors per date: {_sensor_counts(days)}",
            ],
        },
        {
            "title": "Built trend outputs",
            "details": [
                f"Input: {summary.get('trend_input')}",
                f"Sensor trend rows: {trend.get('sensor_record_count')}",
                f"Equipment trend rows: {trend.get('equipment_record_count')}",
                f"Sensor trends: {trend.get('sensor_trends_path')}",
            ],
        },
    ]
    if summary.get("skip_cluster"):
        steps.append({"title": "Skipped clustering", "details": ["Reason: --skip-cluster"]})
    else:
        steps.append(
            {
                "title": "Built cluster window interpretation",
                "details": [
                    f"Dimensions: {', '.join(summary.get('dimensions', []))}",
                    f"Windows: {len(cluster_windows)}",
                    f"Warnings: {sum(int(window.get('warning_count') or 0) for window in cluster_windows)}",
                ],
            }
        )
    return steps


def _run_range_day(
    settings: AppSettings,
    source: str,
    run_date: date,
    facility_id: int,
    snapshot_input: str,
    raw_retention: str,
    keep_native: bool,
    skip_fetch: bool,
    force: bool,
) -> dict[str, Any]:
    reusable_snapshot = None if force else _reusable_snapshot(settings, run_date, source)
    if reusable_snapshot is not None:
        return {
            "workflow": f"{source}-range-day",
            "source": source,
            "date": run_date.isoformat(),
            "facility_id": facility_id,
            "status": "skipped_existing",
            "snapshot_input": reusable_snapshot.get("input_mode"),
            "raw_retention": raw_retention,
            "keep_native": keep_native,
            "snapshot": reusable_snapshot,
            "steps": [
                {
                    "title": "Reused daily snapshot",
                    "details": [
                        f"Sensors: {reusable_snapshot.get('record_count')}",
                        f"Snapshot: {reusable_snapshot.get('snapshot_path')}",
                    ],
                }
            ],
        }

    if skip_fetch:
        day = _run_existing_raw_day_workflow(
            settings=settings,
            source=source,
            run_date=run_date,
            facility_id=facility_id,
            snapshot_input=snapshot_input,
            raw_retention=raw_retention,
            keep_native=keep_native,
        )
    elif source == "mock":
        day = run_mock_day_workflow(
            settings=settings,
            run_date=run_date,
            facility_id=facility_id,
            snapshot_input=snapshot_input,
            raw_retention=raw_retention,
            keep_native=keep_native,
        )
    else:
        day = run_api_day_workflow(
            settings=settings,
            run_date=run_date,
            facility_id=facility_id,
            snapshot_input=snapshot_input,
            raw_retention=raw_retention,
            keep_native=keep_native,
        )
    return {**day, "status": "completed"}


def _run_existing_raw_day_workflow(
    settings: AppSettings,
    source: str,
    run_date: date,
    facility_id: int,
    snapshot_input: str,
    raw_retention: str,
    keep_native: bool,
) -> dict[str, Any]:
    validation_report = validate_waites_raw(settings=settings, run_date=run_date, source=source)
    verify_summary = verify_raw_waites(settings=settings, run_date=run_date)
    load_summary = load_waites_observations(settings=settings, run_date=run_date, source=source)
    snapshot_summary = build_sensor_snapshot(
        settings=settings,
        run_date=run_date,
        source=source,
        input_mode=snapshot_input,
    )
    retention_summary = _apply_retention(
        settings=settings,
        run_date=run_date,
        source=source,
        snapshot_summary=snapshot_summary,
        raw_retention=raw_retention,
        keep_native=keep_native,
    )
    summary = {
        "workflow": f"{source}-day-existing-raw",
        "source": source,
        "date": run_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "raw_retention": raw_retention,
        "keep_native": keep_native,
        "validation": validation_summary(validation_report),
        "verify": verify_summary,
        "load": load_summary,
        "snapshot": snapshot_summary,
        "retention": retention_summary,
    }
    summary["steps"] = [
        _validation_step(summary["validation"]),
        _verify_step(verify_summary),
        _load_step(load_summary),
        _snapshot_step(snapshot_summary),
    ]
    if retention_summary["raw_retention_mode"] != "keep":
        summary["steps"].append(_retention_step(retention_summary))
    return summary


def _reusable_snapshot(settings: AppSettings, run_date: date, source: str) -> dict[str, Any] | None:
    storage = get_storage_paths(settings.data_dir)
    snapshot_dir = storage.snapshot_dir(run_date.isoformat())
    snapshot_path = snapshot_dir / "sensor_snapshot.csv"
    metadata_path = snapshot_dir / "metadata.json"
    if not snapshot_path.exists() or not metadata_path.exists():
        return None
    metadata = read_json(metadata_path)
    if metadata.get("source") != source:
        return None
    expected_count = int(metadata.get("record_count") or 0)
    verification = verify_sensor_daily_snapshot(
        settings=settings,
        run_date=run_date,
        source=source,
        expected_row_count=expected_count,
    )
    if verification["error_count"]:
        return None
    return {
        "source": source,
        "date": run_date.isoformat(),
        "input_mode": metadata.get("input_mode"),
        "snapshot_path": snapshot_path.as_posix(),
        "metadata_path": metadata_path.as_posix(),
        "record_count": expected_count,
        "snapshot_store": verification,
    }


def _apply_retention(
    settings: AppSettings,
    run_date: date,
    source: str,
    snapshot_summary: dict[str, Any],
    raw_retention: str,
    keep_native: bool,
) -> dict[str, Any]:
    retention_mode = _validate_raw_retention(raw_retention)
    snapshot_verification = verify_sensor_daily_snapshot(
        settings=settings,
        run_date=run_date,
        source=source,
        expected_row_count=int(snapshot_summary.get("record_count") or 0),
    )
    if snapshot_verification["error_count"]:
        raise ValueError(
            "Daily snapshot persistence could not be verified; refusing retention action."
        )

    if retention_mode == "keep":
        ledger_update = update_ingestion_retention(
            settings=settings,
            run_date=run_date,
            source=source,
            raw_retention_mode="keep",
            raw_retention_status="kept",
            native_retention_status="kept",
        )
        return {
            "source": source,
            "date": run_date.isoformat(),
            "raw_retention_mode": retention_mode,
            "raw_retention_status": "kept",
            "native_retention_status": "kept",
            "snapshot_verification": snapshot_verification,
            "ledger": ledger_update,
        }

    if retention_mode == "compress":
        raw_summary = compress_raw_waites(settings=settings, run_date=run_date)
        ledger_update = update_ingestion_retention(
            settings=settings,
            run_date=run_date,
            source=source,
            raw_retention_mode="compress",
            raw_retention_status="compressed",
            native_retention_status="kept",
        )
        return {
            "source": source,
            "date": run_date.isoformat(),
            "raw_retention_mode": retention_mode,
            "raw_retention_status": "compressed",
            "native_retention_status": "kept",
            "snapshot_verification": snapshot_verification,
            "raw": raw_summary,
            "ledger": ledger_update,
        }

    raw_summary = release_raw_waites(settings=settings, run_date=run_date)
    if keep_native:
        native_summary = {
            "source": source,
            "date": run_date.isoformat(),
            "dry_run": False,
            "rows_deleted": 0,
            "purged_dates": [],
            "reason": "keep_native",
        }
        native_status = "kept"
    else:
        native_summary = purge_waites_native_observations(
            settings=settings,
            source=source,
            run_date=run_date,
            dry_run=False,
            confirm_delete=True,
        )
        native_status = "purged"

    ledger_update = update_ingestion_retention(
        settings=settings,
        run_date=run_date,
        source=source,
        raw_retention_mode="release",
        raw_retention_status="released",
        native_retention_status=native_status,
    )
    return {
        "source": source,
        "date": run_date.isoformat(),
        "raw_retention_mode": retention_mode,
        "raw_retention_status": "released",
        "native_retention_status": native_status,
        "snapshot_verification": snapshot_verification,
        "raw": raw_summary,
        "native": native_summary,
        "ledger": ledger_update,
    }


def _validate_raw_retention(raw_retention: str) -> str:
    retention_mode = raw_retention.strip().lower()
    if retention_mode not in VALID_RAW_RETENTION_MODES:
        allowed = ", ".join(sorted(VALID_RAW_RETENTION_MODES))
        raise ValueError(f"raw_retention must be one of: {allowed}")
    return retention_mode


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) if counts else "none"

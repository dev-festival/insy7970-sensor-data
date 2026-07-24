from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import load_waites_observations
from insy_sensor_data.raw_lifecycle import verify_raw_waites
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.validate import validate_waites_raw, validation_summary


def run_mock_day_workflow(
    settings: AppSettings,
    run_date: date,
    facility_id: int = 679,
    snapshot_input: str = "sqlite",
) -> dict[str, Any]:
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

    summary = {
        "workflow": "mock-day",
        "source": "mock",
        "date": run_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "fetch": fetch_summary,
        "validation": validation_summary(validation_report),
        "load": load_summary,
        "snapshot": snapshot_summary,
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
    return summary


def run_mock_trend_workflow(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    facility_id: int = 679,
    trend_input: str = "snapshots",
    snapshot_input: str = "sqlite",
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    days: list[dict[str, Any]] = []
    for run_date in _date_range(start_date, end_date):
        day = run_mock_day_workflow(
            settings=settings,
            run_date=run_date,
            facility_id=facility_id,
            snapshot_input=snapshot_input,
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


def run_api_day_workflow(
    settings: AppSettings,
    run_date: date,
    facility_id: int = 679,
    snapshot_input: str = "sqlite",
) -> dict[str, Any]:
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

    summary = {
        "workflow": "api-day",
        "source": "api",
        "date": run_date.isoformat(),
        "facility_id": facility_id,
        "snapshot_input": snapshot_input,
        "fetch": fetch_summary,
        "validation": validation_summary(validation_report),
        "verify": verify_summary,
        "load": load_summary,
        "snapshot": snapshot_summary,
        "next_steps": [
            "Review validation warnings before using live data for decisions.",
            f"uv run sensor-data raw verify --source waites --date {run_date.isoformat()}",
        ],
    }
    summary["steps"] = [
        _fetch_step(fetch_summary),
        _validation_step(summary["validation"]),
        _verify_step(verify_summary),
        _load_step(load_summary),
        _snapshot_step(snapshot_summary),
    ]
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


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]

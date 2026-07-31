from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
import json
import shutil
import subprocess

from insy_sensor_data.artifacts import read_csv_rows, read_json, write_csv_rows, write_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store, observation_db_path
from insy_sensor_data.observations import load_sensor_daily_snapshots
from insy_sensor_data.snapshots.trends import SENSOR_TREND_FIELDS, query_sqlite_trends
from insy_sensor_data.storage import get_storage_paths


REPORT_CHECKS = {
    "201300_rising_vibration": "201300 rms_vel_mean_x increases across the range",
    "201301_stable_vibration": "201301 rms_vel_mean_x stays nearly flat across the range",
    "201303_normalizing_impact": "201303 impact_mean decreases across the range",
    "201307_temperature_spike": "201307 temp_sensor_mean peaks on the middle mock date",
    "201305_missing_readings": "201305 has missing vibration readings on 2025-07-10",
}


def build_mock_trend_report(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    render_quarto: bool = True,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    dates = _date_range(start_date, end_date)
    storage = get_storage_paths(settings.data_dir)
    report_dir = _mock_trend_report_dir(settings, start_date, end_date)
    samples_dir = report_dir / "samples"
    charts_dir = report_dir / "charts"
    samples_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    raw_counts = _raw_counts(settings, dates)
    sqlite_loads = _sqlite_loads(settings, dates)
    snapshot_counts = _snapshot_counts(settings, dates)
    identifier_fields = {
        "date", "installation_point_id", "installation_point_name",
        "equipment_id", "equipment_name", "sensor_id", "customer_asset_id",
    }
    trend_payload = query_sqlite_trends(
        settings,
        start_date,
        end_date,
        source="mock",
        value_fields=[
            field for field in SENSOR_TREND_FIELDS if field not in identifier_fields
        ],
    )
    trend_metadata = trend_payload["metadata"]
    sensor_trend_rows = trend_payload["sensor_rows"]
    equipment_record_count = len(
        {
            (str(row.get("date") or ""), str(row.get("equipment_id") or ""))
            for row in sensor_trend_rows
        }
    )
    trend_counts = [
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "sensor_record_count": trend_metadata.get("sensor_record_count", len(sensor_trend_rows)),
            "equipment_record_count": trend_metadata.get(
                "equipment_record_count",
                equipment_record_count,
            ),
            "input_mode": trend_metadata.get("input_mode", "snapshots"),
        }
    ]
    checks = _mock_behavior_checks(sensor_trend_rows, dates)
    feature_readiness = _feature_readiness(settings, dates)

    sample_paths = _write_samples(
        settings=settings,
        dates=dates,
        samples_dir=samples_dir,
        raw_counts=raw_counts,
        sqlite_loads=sqlite_loads,
        snapshot_counts=snapshot_counts,
        trend_counts=trend_counts,
        sensor_trend_rows=sensor_trend_rows,
        feature_readiness=feature_readiness,
    )
    chart_paths = _write_charts(charts_dir, sensor_trend_rows, dates)

    report_context = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "generated_at": _utc_now(),
        "report_dir": report_dir.as_posix(),
        "raw_counts": raw_counts,
        "sqlite_loads": sqlite_loads,
        "snapshot_counts": snapshot_counts,
        "trend_counts": trend_counts,
        "feature_readiness": feature_readiness,
        "checks": checks,
        "sample_paths": sample_paths,
        "chart_paths": chart_paths,
    }
    report_md = _render_markdown_report(report_context)
    report_qmd = _render_qmd_report(report_context, report_md)
    report_md_path = report_dir / "report.md"
    report_qmd_path = report_dir / "report.qmd"
    checks_path = report_dir / "checks.json"
    report_md_path.write_text(report_md, encoding="utf-8")
    report_qmd_path.write_text(report_qmd, encoding="utf-8")
    write_json(checks_path, {"checks": checks})
    html_summary = _write_html_report(report_dir / "report.html", report_context)
    quarto_summary = _render_quarto_html(report_dir, report_qmd_path) if render_quarto else _quarto_skipped()

    return {
        "report": "mock-trend",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "report_dir": report_dir.as_posix(),
        "report_md_path": report_md_path.as_posix(),
        "report_qmd_path": report_qmd_path.as_posix(),
        "report_html_path": (report_dir / "report.html").as_posix(),
        "checks_path": checks_path.as_posix(),
        "sample_paths": sample_paths,
        "chart_paths": chart_paths,
        "check_count": len(checks),
        "failed_check_count": sum(1 for check in checks if not check["passed"]),
        "raw_count_rows": len(raw_counts),
        "sqlite_load_rows": len(sqlite_loads),
        "snapshot_count_rows": len(snapshot_counts),
        "trend_counts": trend_counts[0],
        "html": html_summary,
        "quarto": quarto_summary,
    }


def _mock_trend_report_dir(settings: AppSettings, start_date: date, end_date: date) -> Path:
    report_root = settings.data_dir.parent / "reports"
    return (
        report_root
        / "mock-trend"
        / f"start={start_date.isoformat()}_end={end_date.isoformat()}"
    )


def _require_report_input(path: Path, message: str, start_date: date, end_date: date) -> None:
    if path.exists():
        return
    command = (
        "uv run sensor-data workflow mock-trend "
        f"--start-date {start_date.isoformat()} --end-date {end_date.isoformat()}"
    )
    raise FileNotFoundError(f"{message}: {path}. Run `{command}` first.")


def _raw_counts(settings: AppSettings, dates: list[date]) -> list[dict[str, Any]]:
    storage = get_storage_paths(settings.data_dir)
    rows: list[dict[str, Any]] = []
    for run_date in dates:
        raw_dir = storage.raw_waites_run_dir(run_date.isoformat())
        manifest_path = raw_dir / "manifest.json"
        _require_report_input(manifest_path, "Missing raw Waites manifest", dates[0], dates[-1])
        manifest = read_json(manifest_path)
        for endpoint in manifest.get("endpoints", []):
            if not isinstance(endpoint, dict):
                continue
            artifact = endpoint.get("artifact") if isinstance(endpoint.get("artifact"), dict) else {}
            rows.append(
                {
                    "date": run_date.isoformat(),
                    "source": manifest.get("source"),
                    "endpoint": endpoint.get("name"),
                    "record_count": endpoint.get("record_count", 0),
                    "artifact_state": artifact.get("state", "unknown"),
                    "storage_path": artifact.get("storage_path") or endpoint.get("path"),
                }
            )
    return rows


def _sqlite_loads(settings: AppSettings, dates: list[date]) -> list[dict[str, Any]]:
    db_path = observation_db_path(settings)
    if not db_path.exists():
        _require_report_input(db_path, "Missing SQLite observation store", dates[0], dates[-1])

    date_values = [run_date.isoformat() for run_date in dates]
    placeholders = ", ".join("?" for _date in date_values)
    with connect_observation_store(settings) as connection:
        ledger_rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT
                    source_date,
                    source,
                    facility_id,
                    endpoint_counts_json,
                    snapshot_row_count,
                    updated_at,
                    manifest_sha256
                FROM waites_ingestion_ledger
                WHERE source = 'mock' AND source_date IN ({placeholders})
                ORDER BY source_date
                """,
                tuple(date_values),
            )
        ]
    rows = []
    for ledger in ledger_rows:
        counts = json.loads(ledger.pop("endpoint_counts_json"))
        rows.append(
            {
                **ledger,
                "equipment_count": counts.get("equipment", 0),
                "installation_point_count": counts.get("installation-points", 0),
                "rms_count": counts.get("readings-rms", 0),
                "impact_count": counts.get("readings-impact-vue", 0),
                "temperature_count": counts.get("readings-temperature", 0),
                "action_item_count": counts.get("action-items", 0),
                "rollup_count": 0,
                "loaded_at": ledger.get("updated_at"),
            }
        )

    loaded_dates = {row["source_date"] for row in rows}
    missing = [raw_date for raw_date in date_values if raw_date not in loaded_dates]
    if missing:
        raise FileNotFoundError(
            "Missing SQLite mock loads for dates "
            f"{', '.join(missing)}. Run `uv run sensor-data workflow mock-trend "
            f"--start-date {dates[0].isoformat()} --end-date {dates[-1].isoformat()}` first."
        )
    return rows


def _snapshot_counts(settings: AppSettings, dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_date in dates:
        snapshot_rows = load_sensor_daily_snapshots(settings, run_date, source="mock")
        rows.append(
            {
                "date": run_date.isoformat(),
                "source": "mock",
                "input_mode": "sqlite",
                "record_count": len(snapshot_rows),
                "snapshot_path": "sqlite",
            }
        )
    return rows


def _write_samples(
    settings: AppSettings,
    dates: list[date],
    samples_dir: Path,
    raw_counts: list[dict[str, Any]],
    sqlite_loads: list[dict[str, Any]],
    snapshot_counts: list[dict[str, Any]],
    trend_counts: list[dict[str, Any]],
    sensor_trend_rows: list[dict[str, str]],
    feature_readiness: list[dict[str, Any]],
) -> dict[str, str]:
    paths = {
        "raw_counts": samples_dir / "raw_counts.csv",
        "sqlite_loads": samples_dir / "sqlite_loads.csv",
        "snapshot_counts": samples_dir / "snapshot_counts.csv",
        "trend_counts": samples_dir / "trend_counts.csv",
        "equipment_sample": samples_dir / "equipment_sample.csv",
        "installation_points_sample": samples_dir / "installation_points_sample.csv",
        "native_observations_sample": samples_dir / "native_observations_sample.csv",
        "sensor_snapshot_sample": samples_dir / "sensor_snapshot_sample.csv",
        "sensor_trends_sample": samples_dir / "sensor_trends_sample.csv",
    }
    if feature_readiness:
        paths["feature_readiness"] = samples_dir / "feature_readiness.csv"
    write_csv_rows(
        paths["raw_counts"],
        raw_counts,
        ["date", "source", "endpoint", "record_count", "artifact_state", "storage_path"],
    )
    write_csv_rows(
        paths["sqlite_loads"],
        sqlite_loads,
        [
            "source_date",
            "source",
            "facility_id",
            "equipment_count",
            "installation_point_count",
            "rms_count",
            "impact_count",
            "temperature_count",
            "action_item_count",
            "rollup_count",
            "loaded_at",
            "manifest_sha256",
        ],
    )
    write_csv_rows(
        paths["snapshot_counts"],
        snapshot_counts,
        ["date", "source", "input_mode", "record_count", "snapshot_path"],
    )
    write_csv_rows(
        paths["trend_counts"],
        trend_counts,
        ["start_date", "end_date", "sensor_record_count", "equipment_record_count", "input_mode"],
    )

    equipment_sample, point_sample, native_sample = _sqlite_samples(settings, dates)
    write_csv_rows(
        paths["equipment_sample"],
        equipment_sample,
        ["source_date", "equipment_id", "name", "facility_id", "customer_asset_id"],
    )
    write_csv_rows(
        paths["installation_points_sample"],
        point_sample,
        [
            "source_date",
            "installation_point_id",
            "name",
            "equipment_id",
            "sensor_id",
            "facility_id",
            "customer_asset_id",
        ],
    )
    write_csv_rows(
        paths["native_observations_sample"],
        native_sample,
        [
            "source_table",
            "source_date",
            "timestamp",
            "installation_point_id",
            "axis",
            "metric",
            "value",
        ],
    )
    write_csv_rows(
        paths["sensor_snapshot_sample"],
        _snapshot_sample(settings, dates),
        [
            "date",
            "installation_point_id",
            "installation_point_name",
            "equipment_id",
            "equipment_name",
            "customer_asset_id",
            "impact_mean",
            "rms_vel_mean_x",
            "temp_sensor_mean",
        ],
    )
    write_csv_rows(
        paths["sensor_trends_sample"],
        sensor_trend_rows[:20],
        [
            "date",
            "installation_point_id",
            "installation_point_name",
            "equipment_id",
            "equipment_name",
            "customer_asset_id",
            "impact_min",
            "impact_mean",
            "impact_max",
            "temp_sensor_min",
            "temp_sensor_mean",
            "temp_sensor_max",
            "rms_vel_min_x",
            "rms_vel_mean_x",
            "rms_vel_max_x",
            "rms_vel_mean_y",
            "rms_vel_mean_z",
        ],
    )
    if feature_readiness:
        write_csv_rows(
            paths["feature_readiness"],
            feature_readiness,
            [
                "date",
                "source",
                "dimension",
                "status",
                "row_count",
                "feature_count",
                "imputed_value_count",
                "matrix_path",
                "summary_path",
            ],
        )
    return {name: path.as_posix() for name, path in paths.items()}


def _feature_readiness(settings: AppSettings, dates: list[date]) -> list[dict[str, Any]]:
    storage = get_storage_paths(settings.data_dir)
    rows: list[dict[str, Any]] = []
    for run_date in dates:
        metadata_path = storage.feature_dir(run_date.isoformat(), "mock") / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = read_json(metadata_path)
        dimensions = metadata.get("dimensions") or metadata.get("axes", {})
        for dimension, dimension_summary in sorted(dimensions.items()):
            rows.append(
                {
                    "date": run_date.isoformat(),
                    "source": metadata.get("source"),
                    "dimension": dimension,
                    "status": dimension_summary.get("status"),
                    "row_count": dimension_summary.get("row_count"),
                    "feature_count": dimension_summary.get("feature_count"),
                    "imputed_value_count": dimension_summary.get("imputed_value_count"),
                    "matrix_path": dimension_summary.get("matrix_path"),
                    "summary_path": dimension_summary.get("summary_path"),
                }
            )
    return rows


def _sqlite_samples(
    settings: AppSettings,
    dates: list[date],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    date_values = [run_date.isoformat() for run_date in dates]
    placeholders = ", ".join("?" for _date in date_values)
    with connect_observation_store(settings) as connection:
        equipment_sample = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT source_date, equipment_id, name, facility_id, customer_asset_id
                FROM waites_equipment
                WHERE source_date IN ({placeholders})
                ORDER BY source_date, equipment_id
                LIMIT 10
                """,
                tuple(date_values),
            )
        ]
        point_sample = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT
                    source_date,
                    installation_point_id,
                    name,
                    equipment_id,
                    sensor_id,
                    facility_id,
                    customer_asset_id
                FROM waites_installation_points
                WHERE source_date IN ({placeholders})
                ORDER BY source_date, installation_point_id
                LIMIT 10
                """,
                tuple(date_values),
            )
        ]
        native_sample = _native_observation_sample(connection, date_values, placeholders)
    return equipment_sample, point_sample, native_sample


def _native_observation_sample(
    connection: Any,
    date_values: list[str],
    placeholders: str,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in connection.execute(
        f"""
        SELECT source_date, timestamp, installation_point_id, axis, velocity
        FROM waites_rms_observations
        WHERE source_date IN ({placeholders})
        ORDER BY source_date, installation_point_id, axis, timestamp, source_row_number
        LIMIT 8
        """,
        tuple(date_values),
    ):
        samples.append(
            {
                "source_table": "waites_rms_observations",
                "source_date": row["source_date"],
                "timestamp": row["timestamp"],
                "installation_point_id": row["installation_point_id"],
                "axis": row["axis"],
                "metric": "velocity",
                "value": row["velocity"],
            }
        )
    for row in connection.execute(
        f"""
        SELECT source_date, timestamp, installation_point_id, value
        FROM waites_temperature_observations
        WHERE source_date IN ({placeholders})
        ORDER BY source_date, installation_point_id, timestamp, source_row_number
        LIMIT 6
        """,
        tuple(date_values),
    ):
        samples.append(
            {
                "source_table": "waites_temperature_observations",
                "source_date": row["source_date"],
                "timestamp": row["timestamp"],
                "installation_point_id": row["installation_point_id"],
                "axis": "",
                "metric": "value",
                "value": row["value"],
            }
        )
    for row in connection.execute(
        f"""
        SELECT source_date, timestamp, installation_point_id, axis, impact_vue_acceleration
        FROM waites_impact_observations
        WHERE source_date IN ({placeholders})
        ORDER BY source_date, installation_point_id, axis, timestamp, source_row_number
        LIMIT 6
        """,
        tuple(date_values),
    ):
        samples.append(
            {
                "source_table": "waites_impact_observations",
                "source_date": row["source_date"],
                "timestamp": row["timestamp"],
                "installation_point_id": row["installation_point_id"],
                "axis": row["axis"],
                "metric": "impact_vue_acceleration",
                "value": row["impact_vue_acceleration"],
            }
        )
    return samples


def _snapshot_sample(settings: AppSettings, dates: list[date]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for run_date in dates:
        for row in load_sensor_daily_snapshots(settings, run_date, source="mock")[:5]:
            rows.append(
                {
                    "date": run_date.isoformat(),
                    "installation_point_id": row.get("installation_point_id", ""),
                    "installation_point_name": row.get("installation_point_name", ""),
                    "equipment_id": row.get("equipment_id", ""),
                    "equipment_name": row.get("equipment_name", ""),
                    "customer_asset_id": row.get("customer_asset_id", ""),
                    "impact_mean": row.get("impact_mean", ""),
                    "rms_vel_mean_x": row.get("rms_vel_mean_x", ""),
                    "temp_sensor_mean": row.get("temp_sensor_mean", ""),
                }
            )
    return rows[:20]


def _mock_behavior_checks(sensor_trend_rows: list[dict[str, str]], dates: list[date]) -> list[dict[str, Any]]:
    date_labels = [run_date.isoformat() for run_date in dates]
    rising = _series(sensor_trend_rows, date_labels, "201300", "rms_vel_mean_x")
    stable = _series(sensor_trend_rows, date_labels, "201301", "rms_vel_mean_x")
    normalizing = _series(sensor_trend_rows, date_labels, "201303", "impact_mean")
    temp_spike = _series(sensor_trend_rows, date_labels, "201307", "temp_sensor_mean")
    missing = _series(sensor_trend_rows, date_labels, "201305", "rms_vel_mean_x")

    return [
        _check(
            "201300_rising_vibration",
            _all_increasing(rising),
            rising,
        ),
        _check(
            "201301_stable_vibration",
            _stable(stable),
            stable,
        ),
        _check(
            "201303_normalizing_impact",
            _all_decreasing(normalizing),
            normalizing,
        ),
        _check(
            "201307_temperature_spike",
            _middle_peak(temp_spike),
            temp_spike,
        ),
        _check(
            "201305_missing_readings",
            _has_missing_middle(missing, date_labels),
            missing,
        ),
    ]


def _check(code: str, passed: bool, series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "code": code,
        "description": REPORT_CHECKS[code],
        "passed": passed,
        "observed": series,
    }


def _series(
    rows: list[dict[str, str]],
    date_labels: list[str],
    installation_point_id: str,
    metric: str,
) -> list[dict[str, Any]]:
    min_metric, max_metric = _metric_bound_fields(metric)
    by_date = {
        row["date"]: row
        for row in rows
        if row.get("installation_point_id") == installation_point_id
    }
    output: list[dict[str, Any]] = []
    for raw_date in date_labels:
        row = by_date.get(raw_date, {})
        value = _float_or_none(row.get(metric, ""))
        output.append(
            {
                "date": raw_date,
                "value": value,
                "min": _float_or_none(row.get(min_metric, "")) if min_metric else value,
                "max": _float_or_none(row.get(max_metric, "")) if max_metric else value,
            }
        )
    return output


def _metric_bound_fields(metric: str) -> tuple[str | None, str | None]:
    if "_mean_" in metric:
        return metric.replace("_mean_", "_min_"), metric.replace("_mean_", "_max_")
    if metric.endswith("_mean"):
        base = metric.removesuffix("_mean")
        return f"{base}_min", f"{base}_max"
    return None, None


def _all_increasing(series: list[dict[str, Any]]) -> bool:
    values = [item["value"] for item in series]
    return all(value is not None for value in values) and all(
        earlier < later for earlier, later in zip(values, values[1:])
    )


def _all_decreasing(series: list[dict[str, Any]]) -> bool:
    values = [item["value"] for item in series]
    return all(value is not None for value in values) and all(
        earlier > later for earlier, later in zip(values, values[1:])
    )


def _stable(series: list[dict[str, Any]], tolerance: float = 0.000001) -> bool:
    values = [item["value"] for item in series if item["value"] is not None]
    return len(values) == len(series) and max(values) - min(values) <= tolerance


def _middle_peak(series: list[dict[str, Any]]) -> bool:
    values = [item["value"] for item in series]
    if len(values) != 3 or any(value is None for value in values):
        return False
    return values[1] > values[0] and values[1] > values[2]


def _has_missing_middle(series: list[dict[str, Any]], date_labels: list[str]) -> bool:
    if len(series) < 3 or "2025-07-10" not in date_labels:
        return False
    index = date_labels.index("2025-07-10")
    return series[index]["value"] is None


def _write_charts(
    charts_dir: Path,
    sensor_trend_rows: list[dict[str, str]],
    dates: list[date],
) -> dict[str, str]:
    date_labels = [run_date.isoformat() for run_date in dates]
    chart_specs = {
        "rising-vibration": (
            "201300 rising vibration",
            _series(sensor_trend_rows, date_labels, "201300", "rms_vel_mean_x"),
            "rms_vel_mean_x",
        ),
        "stable-vibration": (
            "201301 stable vibration",
            _series(sensor_trend_rows, date_labels, "201301", "rms_vel_mean_x"),
            "rms_vel_mean_x",
        ),
        "normalizing-impact": (
            "201303 normalizing impact",
            _series(sensor_trend_rows, date_labels, "201303", "impact_mean"),
            "impact_mean",
        ),
        "temperature-spike": (
            "201307 temperature spike",
            _series(sensor_trend_rows, date_labels, "201307", "temp_sensor_mean"),
            "temp_sensor_mean",
        ),
        "missing-readings": (
            "201305 missing readings",
            _series(sensor_trend_rows, date_labels, "201305", "rms_vel_mean_x"),
            "rms_vel_mean_x",
        ),
    }
    output: dict[str, str] = {}
    for name, (title, series, y_label) in chart_specs.items():
        path = charts_dir / f"{name}.svg"
        path.write_text(_line_chart_svg(title, series, y_label), encoding="utf-8")
        output[name] = path.as_posix()
    return output


def _line_chart_svg(title: str, series: list[dict[str, Any]], y_label: str) -> str:
    width = 760
    height = 320
    left = 76
    right = 96
    top = 42
    bottom = 64
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [
        value
        for item in series
        for value in (item.get("min"), item.get("value"), item.get("max"))
        if value is not None
    ]
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 1.0
    if min_value == max_value:
        min_value -= 0.5
        max_value += 0.5

    def x_at(index: int) -> float:
        return left + (plot_width * index / max(1, len(series) - 1))

    def y_at(value: float) -> float:
        return top + ((max_value - value) / (max_value - min_value) * plot_height)

    min_points: list[str] = []
    avg_points: list[str] = []
    max_points: list[str] = []
    circles: list[str] = []
    labels: list[str] = []
    for index, item in enumerate(series):
        x_value = x_at(index)
        labels.append(
            f'<text x="{x_value:.1f}" y="{height - 28}" text-anchor="middle" '
            f'font-size="12">{escape(item["date"])}</text>'
        )
        value = item["value"]
        if value is None:
            circles.append(
                f'<text x="{x_value:.1f}" y="{top + plot_height / 2:.1f}" '
                'text-anchor="middle" font-size="12" fill="#9a3412">missing</text>'
            )
            continue
        min_item = item.get("min") if item.get("min") is not None else value
        max_item = item.get("max") if item.get("max") is not None else value
        y_value = y_at(value)
        avg_points.append(f"{x_value:.1f},{y_value:.1f}")
        min_points.append(f"{x_value:.1f},{y_at(min_item):.1f}")
        max_points.append(f"{x_value:.1f},{y_at(max_item):.1f}")
        circles.append(
            f'<circle cx="{x_value:.1f}" cy="{y_value:.1f}" r="4" fill="#1f6feb" />'
            f'<text x="{x_value:.1f}" y="{y_value - 10:.1f}" text-anchor="middle" '
            f'font-size="11">{value:.4g}</text>'
        )

    upper_band = (
        f'<polygon points="{" ".join(max_points + list(reversed(avg_points)))}" '
        'fill="#16a34a" opacity="0.22" />'
        if len(max_points) >= 2 and len(avg_points) >= 2
        else ""
    )
    lower_band = (
        f'<polygon points="{" ".join(avg_points + list(reversed(min_points)))}" '
        'fill="#dc2626" opacity="0.18" />'
        if len(avg_points) >= 2 and len(min_points) >= 2
        else ""
    )
    max_line = (
        f'<polyline points="{" ".join(max_points)}" fill="none" stroke="#064e3b" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />'
        if len(max_points) >= 2
        else ""
    )
    avg_line = (
        f'<polyline points="{" ".join(avg_points)}" fill="none" stroke="#3b44ff" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />'
        if len(avg_points) >= 2
        else ""
    )
    min_line = (
        f'<polyline points="{" ".join(min_points)}" fill="none" stroke="#7f1d1d" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />'
        if len(min_points) >= 2
        else ""
    )
    legend_x = left + plot_width + 24
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            f"<title>{escape(title)}</title>",
            f"<desc>{escape(y_label)} across the selected mock trend dates.</desc>",
            '<rect width="100%" height="100%" fill="#ffffff" />',
            f'<text x="{left}" y="22" font-size="16" font-weight="600">{escape(title)}</text>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#444" />',
            f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" '
            f'y2="{top + plot_height}" stroke="#444" />',
            f'<text x="18" y="{top + plot_height / 2:.1f}" transform="rotate(-90 18 '
            f'{top + plot_height / 2:.1f})" font-size="12">{escape(y_label)}</text>',
            f'<text x="{left - 8}" y="{y_at(max_value) + 4:.1f}" text-anchor="end" '
            f'font-size="11">{max_value:.4g}</text>',
            f'<text x="{left - 8}" y="{y_at(min_value) + 4:.1f}" text-anchor="end" '
            f'font-size="11">{min_value:.4g}</text>',
            upper_band,
            lower_band,
            max_line,
            avg_line,
            min_line,
            *circles,
            *labels,
            f'<line x1="{legend_x}" y1="{top + 14}" x2="{legend_x + 22}" y2="{top + 14}" stroke="#064e3b" stroke-width="2" />',
            f'<text x="{legend_x + 30}" y="{top + 19}" font-size="13">Max</text>',
            f'<line x1="{legend_x}" y1="{top + 38}" x2="{legend_x + 22}" y2="{top + 38}" stroke="#3b44ff" stroke-width="2.5" />',
            f'<text x="{legend_x + 30}" y="{top + 43}" font-size="13">Avg</text>',
            f'<line x1="{legend_x}" y1="{top + 62}" x2="{legend_x + 22}" y2="{top + 62}" stroke="#7f1d1d" stroke-width="2" />',
            f'<text x="{legend_x + 30}" y="{top + 67}" font-size="13">Min</text>',
            "</svg>",
        ]
    )


def _render_markdown_report(context: dict[str, Any]) -> str:
    checks = context["checks"]
    chart_paths = context["chart_paths"]
    sample_paths = context["sample_paths"]
    lines = [
        "# Mock Trend Evidence Report",
        "",
        f"Range: `{context['start_date']}` to `{context['end_date']}`",
        "",
        f"Generated at: `{context['generated_at']}`",
        "",
        "## Source Evidence Counts",
        "",
        _markdown_table(context["raw_counts"], ["date", "endpoint", "record_count", "artifact_state"]),
        "",
        "## SQLite Loads",
        "",
        _markdown_table(
            context["sqlite_loads"],
            ["source_date", "equipment_count", "installation_point_count", "rms_count", "impact_count", "temperature_count"],
        ),
        "",
        "## Snapshot And Trend Counts",
        "",
        _markdown_table(context["snapshot_counts"], ["date", "input_mode", "record_count"]),
        "",
        _markdown_table(context["trend_counts"], ["start_date", "end_date", "sensor_record_count", "equipment_record_count", "input_mode"]),
        "",
        "## Feature Readiness",
        "",
        _markdown_table(
            context["feature_readiness"],
            ["date", "dimension", "status", "row_count", "feature_count", "imputed_value_count"],
        )
        if context["feature_readiness"]
        else "No feature readiness artifacts were found for this range.",
        "",
        "## Expected Versus Observed Checks",
        "",
        _markdown_table(
            [
                {
                    "check": check["code"],
                    "passed": "yes" if check["passed"] else "no",
                    "description": check["description"],
                }
                for check in checks
            ],
            ["check", "passed", "description"],
        ),
        "",
        "## Charts",
        "",
    ]
    for name, path in chart_paths.items():
        lines.extend([f"### {name.replace('-', ' ').title()}", "", f"![{name}](charts/{Path(path).name})", ""])
    lines.extend(
        [
            "## Sample Files",
            "",
            _markdown_table(
                [{"sample": name, "path": Path(path).as_posix()} for name, path in sample_paths.items()],
                ["sample", "path"],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _render_qmd_report(context: dict[str, Any], report_md: str) -> str:
    return "\n".join(
        [
            "---",
            'title: "Mock Trend Evidence Report"',
            "format: html",
            "---",
            "",
            report_md,
        ]
    )


def _write_html_report(path: Path, context: dict[str, Any]) -> dict[str, Any]:
    checks = context["checks"]
    charts = context["chart_paths"]
    html = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Mock Trend Evidence Report</title>",
        "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.5}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border-bottom:1px solid #ddd;padding:.35rem;text-align:left}img{max-width:100%;height:auto}code{background:#f4f4f4;padding:.1rem .25rem}</style>",
        "</head>",
        "<body>",
        "<h1>Mock Trend Evidence Report</h1>",
        f"<p>Range: <code>{escape(context['start_date'])}</code> to <code>{escape(context['end_date'])}</code></p>",
        "<h2>Expected Versus Observed Checks</h2>",
        _html_table(
            [
                {
                    "check": check["code"],
                    "passed": "yes" if check["passed"] else "no",
                    "description": check["description"],
                }
                for check in checks
            ],
            ["check", "passed", "description"],
        ),
        "<h2>Charts</h2>",
    ]
    for name, chart_path in charts.items():
        html.append(f"<h3>{escape(name.replace('-', ' ').title())}</h3>")
        html.append(f'<img src="charts/{escape(Path(chart_path).name)}" alt="{escape(name)} chart">')
    html.extend(["</body>", "</html>"])
    path.write_text("\n".join(html), encoding="utf-8")
    return {"path": path.as_posix(), "renderer": "fallback"}


def _render_quarto_html(report_dir: Path, report_qmd_path: Path) -> dict[str, Any]:
    quarto = shutil.which("quarto")
    if quarto is None:
        return {"available": False, "rendered": False, "reason": "quarto_not_found"}

    result = subprocess.run(
        [quarto, "render", report_qmd_path.name, "--to", "html", "--output", "report.html"],
        cwd=report_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "available": True,
        "rendered": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-1000:],
        "stderr": result.stderr[-1000:],
    }


def _quarto_skipped() -> dict[str, Any]:
    return {"available": False, "rendered": False, "reason": "render_disabled"}


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _column in columns) + " |"
    body = [
        "| " + " | ".join(_markdown_cell(row.get(column, "")) for column in columns) + " |"
        for row in rows
    ]
    if not body:
        body = ["| " + " | ".join("" for _column in columns) + " |"]
    return "\n".join([header, separator, *body])


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|")


def _html_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    output = ["<table>", "<thead><tr>"]
    output.extend(f"<th>{escape(column)}</th>" for column in columns)
    output.append("</tr></thead><tbody>")
    for row in rows:
        output.append("<tr>")
        output.extend(f"<td>{escape(str(row.get(column, '')))}</td>" for column in columns)
        output.append("</tr>")
    output.append("</tbody></table>")
    return "\n".join(output)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

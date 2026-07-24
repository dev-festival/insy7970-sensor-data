from __future__ import annotations

from datetime import date
from pathlib import Path
import json

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.reports import build_mock_trend_report
from insy_sensor_data.workflows import run_mock_trend_workflow


def test_build_mock_trend_report_writes_evidence_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 11)
    run_mock_trend_workflow(settings=settings, start_date=start_date, end_date=end_date)

    summary = build_mock_trend_report(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        render_quarto=False,
    )

    report_dir = tmp_path / "reports" / "mock-trend" / "start=2025-07-09_end=2025-07-11"
    assert summary["report_dir"] == report_dir.as_posix()
    assert summary["check_count"] == 5
    assert summary["failed_check_count"] == 0
    assert (report_dir / "report.md").exists()
    assert (report_dir / "report.qmd").exists()
    assert (report_dir / "report.html").exists()
    assert (report_dir / "checks.json").exists()
    assert (report_dir / "samples" / "raw_counts.csv").exists()
    assert (report_dir / "samples" / "sqlite_loads.csv").exists()
    assert (report_dir / "samples" / "sensor_snapshot_sample.csv").exists()
    assert (report_dir / "samples" / "sensor_trends_sample.csv").exists()

    checks = json.loads((report_dir / "checks.json").read_text(encoding="utf-8"))["checks"]
    assert {check["code"] for check in checks} == {
        "201300_rising_vibration",
        "201301_stable_vibration",
        "201303_normalizing_impact",
        "201307_temperature_spike",
        "201305_missing_readings",
    }
    assert all(check["passed"] for check in checks)

    for chart_name in [
        "rising-vibration.svg",
        "stable-vibration.svg",
        "normalizing-impact.svg",
        "temperature-spike.svg",
        "missing-readings.svg",
    ]:
        chart_path = report_dir / "charts" / chart_name
        assert chart_path.exists()
        assert chart_path.stat().st_size > 100

    report_text = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "Mock Trend Evidence Report" in report_text
    assert "Expected Versus Observed Checks" in report_text
    assert "201300_rising_vibration" in report_text


def test_build_mock_trend_report_handles_quarto_absence(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 11)
    run_mock_trend_workflow(settings=settings, start_date=start_date, end_date=end_date)
    monkeypatch.setattr("insy_sensor_data.reports.shutil.which", lambda _name: None)

    summary = build_mock_trend_report(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        render_quarto=True,
    )

    assert summary["quarto"] == {
        "available": False,
        "rendered": False,
        "reason": "quarto_not_found",
    }
    assert Path(summary["report_html_path"]).exists()


def test_build_mock_trend_report_requires_existing_trend_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    with pytest.raises(FileNotFoundError, match="workflow mock-trend"):
        build_mock_trend_report(
            settings=settings,
            start_date=date(2025, 7, 9),
            end_date=date(2025, 7, 11),
            render_quarto=False,
        )

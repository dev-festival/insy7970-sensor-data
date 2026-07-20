from __future__ import annotations

from datetime import date
from pathlib import Path
import json

from insy_sensor_data.config import AppSettings
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.validate import validate_waites_raw, validation_summary


def test_validate_waites_raw_accepts_mock_fetch_with_warnings(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    report = validate_waites_raw(settings=settings, run_date=run_date, source="mock")

    assert report["error_count"] == 0
    assert report["status"] in {"valid", "valid_with_warnings"}
    assert report["source"] == "mock"
    assert (tmp_path / "data" / "raw" / "waites" / "date=2025-07-09" / "validation.json").exists()
    assert validation_summary(report)["endpoint_record_counts"]["readings-rms"] == 21


def test_validate_waites_raw_reports_missing_required_field(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    payload = json.loads((raw_dir / "equipment.json").read_text(encoding="utf-8"))
    del payload["list"][0]["equipment_id"]
    (raw_dir / "equipment.json").write_text(json.dumps(payload), encoding="utf-8")

    report = validate_waites_raw(settings=settings, run_date=run_date, source="mock")

    assert report["status"] == "invalid"
    assert any(issue["code"] == "missing_required_fields" for issue in report["issues"])


def test_validate_waites_raw_reports_malformed_envelope(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    (raw_dir / "readings-rms.json").write_text(json.dumps({"records": []}), encoding="utf-8")

    report = validate_waites_raw(settings=settings, run_date=run_date, source="mock")

    assert report["status"] == "invalid"
    assert any(issue["code"] == "bad_envelope" for issue in report["issues"])


def test_validate_waites_raw_warns_on_unexpected_and_null_heavy_fields(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    payload = json.loads((raw_dir / "readings-temperature.json").read_text(encoding="utf-8"))
    for row in payload["list"]:
        row["ambient"] = None
        row["new_live_field"] = "observed"
    (raw_dir / "readings-temperature.json").write_text(json.dumps(payload), encoding="utf-8")

    report = validate_waites_raw(settings=settings, run_date=run_date, source="mock")

    assert report["error_count"] == 0
    assert report["status"] == "valid_with_warnings"
    assert any(issue["code"] == "null_heavy_required_field" for issue in report["issues"])
    assert any(issue["code"] == "unexpected_fields" for issue in report["issues"])


def test_validate_waites_raw_allows_open_action_item_closed_at_nulls(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    payload = json.loads((raw_dir / "action-items.json").read_text(encoding="utf-8"))
    for row in payload["list"]:
        row["closed_at"] = None
    (raw_dir / "action-items.json").write_text(json.dumps(payload), encoding="utf-8")

    report = validate_waites_raw(settings=settings, run_date=run_date, source="mock")

    assert not any(
        issue["code"] == "null_heavy_required_field"
        and issue.get("detail", {}).get("field") == "closed_at"
        for issue in report["issues"]
    )


def test_validate_waites_raw_reports_source_mismatch(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    report = validate_waites_raw(settings=settings, run_date=run_date, source="api")

    assert report["status"] == "invalid"
    assert any(issue["code"] == "source_mismatch" for issue in report["issues"])

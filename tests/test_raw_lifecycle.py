from __future__ import annotations

from datetime import date
from pathlib import Path
import json

import pytest

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.raw_lifecycle import compress_raw_waites, prune_raw_waites, verify_raw_waites
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.validate import validate_waites_raw


def test_fetch_records_raw_lifecycle_metadata(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")

    fetch_waites(settings=settings, run_date=date(2025, 7, 9), facility_id=679)

    manifest = _manifest(tmp_path, "2025-07-09")
    artifact = _endpoint(manifest, "equipment")["artifact"]
    assert manifest["raw_lifecycle"]["schema_version"] == 1
    assert artifact["state"] == "plain"
    assert artifact["compression"] == "none"
    assert artifact["byte_count"] > 0
    assert len(artifact["sha256"]) == 64
    assert artifact["compressed_byte_count"] is None


def test_compress_raw_waites_updates_manifest_and_readers(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    summary = compress_raw_waites(settings=settings, run_date=run_date)

    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    assert summary["compressed_count"] == 6
    assert not (raw_dir / "equipment.json").exists()
    assert (raw_dir / "equipment.json.gz").exists()

    payload = read_json(raw_dir / "equipment.json")
    assert len(payload["list"]) == 6

    manifest = _manifest(tmp_path, "2025-07-09")
    artifact = _endpoint(manifest, "equipment")["artifact"]
    assert artifact["state"] == "compressed"
    assert artifact["compression"] == "gzip"
    assert artifact["logical_path"].endswith("equipment.json")
    assert artifact["storage_path"].endswith("equipment.json.gz")
    assert artifact["compressed_byte_count"] > 0
    assert len(artifact["compressed_sha256"]) == 64


def test_verify_raw_waites_passes_for_plain_and_compressed_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)

    plain = verify_raw_waites(settings=settings, run_date=run_date)
    compress_raw_waites(settings=settings, run_date=run_date)
    compressed = verify_raw_waites(settings=settings, run_date=run_date)

    assert plain["status"] == "valid"
    assert plain["error_count"] == 0
    assert compressed["status"] == "valid"
    assert compressed["error_count"] == 0
    assert {artifact["state"] for artifact in compressed["artifacts"]} == {"compressed"}


def test_verify_raw_waites_fails_on_checksum_mismatch(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    raw_dir = tmp_path / "data" / "raw" / "waites" / "date=2025-07-09"
    payload = json.loads((raw_dir / "equipment.json").read_text(encoding="utf-8"))
    payload["list"][0]["name"] = "changed after manifest"
    (raw_dir / "equipment.json").write_text(json.dumps(payload), encoding="utf-8")

    report = verify_raw_waites(settings=settings, run_date=run_date)

    assert report["status"] == "invalid"
    assert any(issue["code"] == "sha256_mismatch" for issue in report["issues"])


def test_validate_and_snapshot_read_compressed_raw_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    compress_raw_waites(settings=settings, run_date=run_date)

    validation = validate_waites_raw(settings=settings, run_date=run_date, source="mock")
    snapshot = build_sensor_snapshot(settings=settings, run_date=run_date, source="mock")

    assert validation["error_count"] == 0
    assert snapshot["record_count"] == 9


def test_prune_raw_waites_dry_run_lists_without_deleting(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    for raw_date in [date(2025, 7, 9), date(2025, 7, 11)]:
        fetch_waites(settings=settings, run_date=raw_date, facility_id=679)

    summary = prune_raw_waites(
        settings=settings,
        older_than_days=1,
        today=date(2025, 7, 12),
    )

    assert summary["dry_run"] is True
    assert summary["candidate_count"] == 1
    assert summary["candidates"][0]["date"] == "2025-07-09"
    assert (tmp_path / "data" / "raw" / "waites" / "date=2025-07-09").exists()


def test_prune_raw_waites_delete_requires_confirmation(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    fetch_waites(settings=settings, run_date=date(2025, 7, 9), facility_id=679)

    with pytest.raises(ValueError, match="confirm-delete"):
        prune_raw_waites(
            settings=settings,
            older_than_days=1,
            dry_run=False,
            today=date(2025, 7, 12),
        )


def test_prune_raw_waites_delete_removes_verified_candidates(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    for raw_date in [date(2025, 7, 9), date(2025, 7, 11)]:
        fetch_waites(settings=settings, run_date=raw_date, facility_id=679)

    summary = prune_raw_waites(
        settings=settings,
        older_than_days=1,
        dry_run=False,
        confirm_delete=True,
        today=date(2025, 7, 12),
    )

    assert summary["deleted_dates"] == ["2025-07-09"]
    assert not (tmp_path / "data" / "raw" / "waites" / "date=2025-07-09").exists()
    assert (tmp_path / "data" / "raw" / "waites" / "date=2025-07-11").exists()


def _manifest(tmp_path: Path, raw_date: str) -> dict[str, object]:
    return json.loads(
        (
            tmp_path
            / "data"
            / "raw"
            / "waites"
            / f"date={raw_date}"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )


def _endpoint(manifest: dict[str, object], name: str) -> dict[str, object]:
    return next(endpoint for endpoint in manifest["endpoints"] if endpoint["name"] == name)

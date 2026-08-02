from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import sqlite3
import subprocess
import sys

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store
from insy_sensor_data.retirement import (
    apply_retirement_manifest,
    build_retirement_manifest,
    database_integrity,
)
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.schema import SNAPSHOT_FIELDS
from insy_sensor_data.waites.fetch import fetch_waites


RUN_DATE = date(2025, 7, 9)


def test_retirement_manifest_is_exact_source_safe_and_excludes_raw(tmp_path: Path) -> None:
    settings = _legacy_store(tmp_path)
    candidate = settings.data_dir / "processed" / "trends" / "old.csv"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("old trend\n", encoding="utf-8")
    reference = settings.data_dir / "processed" / "waites" / "reference" / "old.json"
    reference.parent.mkdir(parents=True)
    reference.write_text('{"old": true}\n', encoding="utf-8")
    raw = settings.data_dir / "raw" / "waites" / "sentinel.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('{"keep": true}\n', encoding="utf-8")

    result = build_retirement_manifest(settings, tmp_path / "retirement-manifest.json")

    assert result["status"] == "dry_run"
    assert result["raw_evidence_targeted"] is False
    assert result["file_count"] == 2
    assert result["file_bytes"] == candidate.stat().st_size + reference.stat().st_size
    assert {row["absolute_path"] for row in result["files"]} == {
        candidate.resolve().as_posix(),
        reference.resolve().as_posix(),
    }
    assert result["database"]["legacy_snapshot_parity"]["status"] == "ready"
    assert result["database"]["legacy_snapshot_parity"]["legacy_row_count"] == 9
    assert result["database"]["protected_table_rows"]["sensor_daily_facts"] == 9
    assert result["database"]["legacy_snapshot_other_source_rows"] == 1
    assert raw.is_file()


def test_retirement_apply_backs_up_restores_and_migrates_disposable_store(tmp_path: Path) -> None:
    settings = _legacy_store(tmp_path)
    candidate = settings.data_dir / "processed" / "clusters" / "old.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text('{"retired": true}\n', encoding="utf-8")
    raw = settings.data_dir / "raw" / "waites" / "sentinel.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('{"keep": true}\n', encoding="utf-8")
    manifest_path = tmp_path / "retirement-manifest.json"
    manifest = build_retirement_manifest(settings, manifest_path)

    result = apply_retirement_manifest(
        manifest_path,
        expected_manifest_sha256=manifest["manifest_sha256"],
        backup_path=tmp_path / "backup.sqlite",
        restore_test_path=tmp_path / "restored.sqlite",
        vacuum_output=tmp_path / "vacuumed.sqlite",
    )

    assert result["status"] == "complete"
    assert result["deleted_file_count"] == 1
    assert result["database_integrity_check"] == "ok"
    assert result["backup"]["integrity_check"] == "ok"
    assert result["restore_rehearsal"]["integrity_check"] == "ok"
    assert result["vacuum_output"]["integrity_check"] == "ok"
    assert {row["status"] for row in result["table_actions"]} == {"dropped"}
    assert result["file_actions"] == [
        {
            "absolute_path": candidate.resolve().as_posix(),
            "byte_count": manifest["files"][0]["byte_count"],
            "sha256": manifest["files"][0]["sha256"],
            "status": "deleted",
        }
    ]
    assert database_integrity(tmp_path / "backup.sqlite") == "ok"
    assert database_integrity(tmp_path / "restored.sqlite") == "ok"
    assert candidate.exists() is False
    assert raw.is_file()

    with connect_observation_store(settings) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "sensor_daily_snapshots" not in tables
        assert "waites_rms_observations" not in tables
        assert connection.execute("SELECT COUNT(*) FROM sensor_daily_facts").fetchone()[0] == 9
        state = connection.execute(
            "SELECT snapshot_authority, migration_status, migration_version "
            "FROM operational_store_state WHERE state_id = 1"
        ).fetchone()
        assert tuple(state) == ("sensor_daily_facts", "complete", 10)
        audit = connection.execute(
            "SELECT status, legacy_row_count, fixed_row_count "
            "FROM snapshot_migration_audit WHERE source = 'mock' AND migration_version = 10"
        ).fetchone()
        assert tuple(audit) == ("complete", 9, 9)

    with sqlite3.connect(tmp_path / "restored.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM sensor_daily_snapshots").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM sensor_daily_facts").fetchone()[0] == 9


def test_retirement_apply_rejects_stale_target_before_backup(tmp_path: Path) -> None:
    settings = _legacy_store(tmp_path)
    candidate = settings.data_dir / "processed" / "features" / "old.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("original\n", encoding="utf-8")
    manifest_path = tmp_path / "retirement-manifest.json"
    manifest = build_retirement_manifest(settings, manifest_path)
    candidate.write_text("changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Manifest target changed"):
        apply_retirement_manifest(
            manifest_path,
            expected_manifest_sha256=manifest["manifest_sha256"],
            backup_path=tmp_path / "backup.sqlite",
            restore_test_path=tmp_path / "restored.sqlite",
        )

    assert not (tmp_path / "backup.sqlite").exists()
    with connect_observation_store(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sensor_daily_snapshots").fetchone()[0] == 10


def test_retirement_manifest_must_be_outside_processed_data(tmp_path: Path) -> None:
    settings = _legacy_store(tmp_path)

    with pytest.raises(ValueError, match="outside data/processed"):
        build_retirement_manifest(
            settings,
            settings.data_dir / "processed" / "manifest.json",
        )


def test_retirement_apply_requires_maintenance_outputs_outside_processed(
    tmp_path: Path,
) -> None:
    settings = _legacy_store(tmp_path)
    manifest_path = tmp_path / "retirement-manifest.json"
    manifest = build_retirement_manifest(settings, manifest_path)

    with pytest.raises(ValueError, match="outside data/processed"):
        apply_retirement_manifest(
            manifest_path,
            expected_manifest_sha256=manifest["manifest_sha256"],
            backup_path=settings.data_dir / "processed" / "unsafe-backup.sqlite",
            restore_test_path=tmp_path / "restored.sqlite",
        )


def test_retirement_accepts_schema9_compatible_store(tmp_path: Path) -> None:
    settings = _legacy_store(tmp_path)
    with connect_observation_store(settings) as connection:
        connection.execute("DELETE FROM observation_schema")
        connection.execute(
            "INSERT INTO observation_schema (version, applied_at) VALUES (9, 'rehearsal')"
        )
        connection.commit()
    manifest_path = tmp_path / "retirement-manifest.json"
    manifest = build_retirement_manifest(settings, manifest_path)

    assert manifest["database"]["schema_version"] == 9
    result = apply_retirement_manifest(
        manifest_path,
        expected_manifest_sha256=manifest["manifest_sha256"],
        backup_path=tmp_path / "backup.sqlite",
        restore_test_path=tmp_path / "restored.sqlite",
    )

    assert result["status"] == "complete"
    with connect_observation_store(settings) as connection:
        assert connection.execute("SELECT MAX(version) FROM observation_schema").fetchone()[0] == 10


def test_retirement_script_records_failed_apply(tmp_path: Path) -> None:
    settings = _legacy_store(tmp_path)
    manifest_path = tmp_path / "retirement-manifest.json"
    build_retirement_manifest(settings, manifest_path)
    result_path = tmp_path / "failed-result.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/retire_0_6_6.py",
            "--apply",
            "--manifest",
            str(manifest_path),
            "--confirm-manifest-sha256",
            "wrong",
            "--backup",
            str(tmp_path / "backup.sqlite"),
            "--restore-test",
            str(tmp_path / "restored.sqlite"),
            "--result",
            str(result_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["error_type"] == "ValueError"
    assert "checksum" in result["error"].lower()
    assert not (tmp_path / "backup.sqlite").exists()


def _legacy_store(tmp_path: Path) -> AppSettings:
    settings = AppSettings(data_dir=tmp_path / "data", source_mode="mock")
    fetch_waites(settings=settings, run_date=RUN_DATE, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=RUN_DATE, source="mock")
    with connect_observation_store(settings) as connection:
        connection.execute(
            """
            CREATE TABLE sensor_daily_snapshots (
                source TEXT NOT NULL,
                source_date TEXT NOT NULL,
                installation_point_id TEXT NOT NULL,
                built_at TEXT NOT NULL,
                snapshot_csv_path TEXT,
                snapshot_json TEXT NOT NULL,
                PRIMARY KEY (source, source_date, installation_point_id)
            )
            """
        )
        rows = connection.execute(
            "SELECT * FROM sensor_daily_facts WHERE source = 'mock' ORDER BY installation_point_id"
        ).fetchall()
        for row in rows:
            record = dict(row)
            payload = {field: record.get(field) for field in SNAPSHOT_FIELDS}
            connection.execute(
                """
                INSERT INTO sensor_daily_snapshots (
                    source, source_date, installation_point_id,
                    built_at, snapshot_csv_path, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record["source"],
                    record["source_date"],
                    record["installation_point_id"],
                    record["built_at"],
                    "retired.csv",
                    json.dumps(payload, sort_keys=True),
                ),
            )
        connection.execute(
            """
            INSERT INTO sensor_daily_snapshots (
                source, source_date, installation_point_id,
                built_at, snapshot_csv_path, snapshot_json
            ) VALUES ('api', '2025-07-09', 'cross-source', '2025-07-10T00:00:00Z',
                      'retired.csv', '{}')
            """
        )
        for table in [
            "waites_loads",
            "waites_equipment",
            "waites_installation_points",
            "waites_rms_observations",
            "waites_temperature_observations",
            "waites_impact_observations",
            "waites_action_items",
            "waites_daily_metric_rollups",
        ]:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.commit()
    return settings

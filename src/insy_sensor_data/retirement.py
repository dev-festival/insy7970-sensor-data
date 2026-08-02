from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.schema import (
    OPERATIONAL_SCHEMA_VERSION,
    audit_legacy_snapshot_parity,
    configured_source,
)
from insy_sensor_data.storage import get_storage_paths


RETIREMENT_VERSION = "0.6.6"
CANDIDATE_DIRECTORIES = (
    Path("waites/reference"),
    Path("snapshots"),
    Path("trends"),
    Path("features"),
    Path("clusters"),
    Path("drift"),
    Path("cluster_windows"),
    Path("cluster_models"),
    Path("cluster_model_drift"),
)
LEGACY_TABLE_CANDIDATES = (
    "sensor_daily_snapshots",
    "waites_loads",
    "waites_equipment",
    "waites_installation_points",
    "waites_rms_observations",
    "waites_temperature_observations",
    "waites_impact_observations",
    "waites_action_items",
    "waites_daily_metric_rollups",
)
PROTECTED_TABLES = (
    "sensor_daily_facts",
    "waites_asset_tree_reference",
    "waites_equipment_reference",
    "waites_installation_point_reference",
    "waites_events",
    "waites_event_coverage",
    "waites_ingestion_ledger",
    "ingestion_runs",
    "ingestion_run_transitions",
    "snapshot_revisions",
    "snapshot_migration_audit",
    "cluster_model_runs",
    "cluster_model_assignments",
    "cluster_model_centroids",
    "cluster_drift_runs",
    "cluster_drift_assignments",
    "cluster_centroid_alignment",
    "sync_control",
    "sync_date_runs",
    "admin_writer_lease",
    "admin_action_audit",
)


def build_retirement_manifest(
    settings: AppSettings,
    destination: Path,
) -> dict[str, Any]:
    """Inventory exact historical targets without changing operational data."""
    data_dir = settings.data_dir.resolve()
    processed_dir = (data_dir / "processed").resolve()
    database_path = get_storage_paths(data_dir).observations_db_path.resolve()
    if processed_dir == data_dir or processed_dir.parent != data_dir:
        raise ValueError("Processed-data directory did not resolve beneath the configured data directory.")
    if not database_path.is_file():
        raise FileNotFoundError(f"Operational SQLite store is missing: {database_path.as_posix()}")

    files: list[dict[str, Any]] = []
    directories: list[dict[str, Any]] = []
    for relative in CANDIDATE_DIRECTORIES:
        root = (processed_dir / relative).resolve()
        _require_within(root, processed_dir)
        selected_files = sorted(path for path in root.rglob("*") if path.is_file()) if root.is_dir() else []
        entries = [_file_record(path, processed_dir) for path in selected_files]
        files.extend(entries)
        directories.append(
            {
                "relative_path": relative.as_posix(),
                "absolute_path": root.as_posix(),
                "exists": root.is_dir(),
                "file_count": len(entries),
                "byte_count": sum(int(entry["byte_count"]) for entry in entries),
            }
        )

    before = database_path.stat()
    database_sha256 = _sha256(database_path)
    after = database_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Operational database changed while the dry-run manifest was generated.")
    database = _database_inventory(database_path, settings.source_mode)
    database.update(
        {
            "absolute_path": database_path.as_posix(),
            "byte_count": after.st_size,
            "modified_time_ns": after.st_mtime_ns,
            "sha256": database_sha256,
        }
    )
    manifest = {
        "retirement_version": RETIREMENT_VERSION,
        "operation": "legacy_retirement",
        "status": "dry_run",
        "generated_at": _utc_now(),
        "source": settings.source_mode,
        "data_dir": data_dir.as_posix(),
        "processed_dir": processed_dir.as_posix(),
        "raw_evidence_targeted": False,
        "directories": directories,
        "files": files,
        "file_count": len(files),
        "file_bytes": sum(int(entry["byte_count"]) for entry in files),
        "database": database,
        "backup": None,
        "approved": False,
    }
    destination = destination.resolve()
    if _is_within(destination, processed_dir):
        raise ValueError("Retirement manifest must be stored outside data/processed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        **manifest,
        "manifest_path": destination.as_posix(),
        "manifest_sha256": _sha256(destination),
    }


def create_database_backup(database_path: Path, destination: Path) -> dict[str, Any]:
    """Create and verify a transactionally consistent SQLite backup."""
    source = database_path.resolve()
    target = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Operational SQLite store is missing: {source.as_posix()}")
    if source == target:
        raise ValueError("Backup destination must differ from the operational database.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Backup destination already exists: {target.as_posix()}")
    with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)
    integrity = database_integrity(target)
    if integrity != "ok":
        raise RuntimeError(f"Backup integrity check failed: {integrity}")
    return {
        "absolute_path": target.as_posix(),
        "byte_count": target.stat().st_size,
        "sha256": _sha256(target),
        "integrity_check": integrity,
        "created_at": _utc_now(),
    }


def rehearse_database_restore(backup_path: Path, destination: Path) -> dict[str, Any]:
    """Restore a verified backup into a separate SQLite database and verify it."""
    restored = create_database_backup(backup_path, destination)
    restored["source_backup_sha256"] = _sha256(backup_path.resolve())
    restored["restored_at"] = _utc_now()
    return restored


def apply_retirement_manifest(
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    backup_path: Path,
    restore_test_path: Path,
    vacuum_output: Path | None = None,
) -> dict[str, Any]:
    """Apply only a verified manifest; intended for rehearsals until separately approved."""
    manifest_path = manifest_path.resolve()
    actual_manifest_sha256 = _sha256(manifest_path)
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise ValueError("Manifest checksum does not match the explicit confirmation value.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "dry_run" or manifest.get("raw_evidence_targeted") is not False:
        raise ValueError("Manifest is not an eligible 0.6.6 dry run.")
    processed_dir = Path(str(manifest["processed_dir"])).resolve()
    database_path = Path(str(manifest["database"]["absolute_path"])).resolve()
    maintenance_paths = [backup_path.resolve(), restore_test_path.resolve()]
    if vacuum_output is not None:
        maintenance_paths.append(vacuum_output.resolve())
    if len(set(maintenance_paths)) != len(maintenance_paths):
        raise ValueError("Backup, restore rehearsal, and vacuum destinations must be distinct.")
    for path in maintenance_paths:
        if _is_within(path, processed_dir):
            raise ValueError("Maintenance outputs must be stored outside data/processed.")
    _verify_database_identity(database_path, manifest["database"])
    _verify_manifest_files(manifest["files"], processed_dir)

    backup = create_database_backup(database_path, backup_path)
    restore_rehearsal = rehearse_database_restore(backup_path, restore_test_path)
    _verify_database_identity(database_path, manifest["database"])
    _verify_manifest_files(manifest["files"], processed_dir)
    started_at = _utc_now()
    table_actions: list[dict[str, Any]] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if _writer_active(connection):
            raise RuntimeError("An administrative writer lease is active; retirement is blocked.")
        source = configured_source(connection)
        if source != manifest.get("source"):
            raise RuntimeError(
                f"Configured source changed from {manifest.get('source')!r} to {source!r}."
            )
        parity = audit_legacy_snapshot_parity(connection, str(source))
        if parity["status"] not in {"ready", "absent"}:
            raise RuntimeError("Legacy/fixed snapshot parity changed; retirement is blocked.")
        connection.execute("BEGIN IMMEDIATE")
        for table in LEGACY_TABLE_CANDIDATES:
            if _table_exists(connection, table):
                connection.execute(f'DROP TABLE "{table}"')
                status = "dropped"
            else:
                status = "absent"
            table_actions.append(
                {
                    "table": table,
                    "status": status,
                    "row_count": manifest["database"]["candidate_table_rows"].get(table),
                }
            )
        completed_at = _utc_now()
        connection.execute(
            """
            UPDATE operational_store_state
            SET snapshot_authority = 'sensor_daily_facts',
                migration_status = 'complete', migration_version = ?, updated_at = ?
            WHERE state_id = 1
            """,
            (OPERATIONAL_SCHEMA_VERSION, completed_at),
        )
        connection.execute(
            "INSERT OR IGNORE INTO observation_schema (version, applied_at) VALUES (?, ?)",
            (OPERATIONAL_SCHEMA_VERSION, completed_at),
        )
        connection.execute(
            """
            INSERT INTO snapshot_migration_audit (
                source, migration_version, status, started_at, completed_at,
                legacy_row_count, fixed_row_count, legacy_hash, fixed_hash,
                null_value_count, zero_value_count, database_bytes_before,
                database_bytes_side_by_side, error
            ) VALUES (?, ?, 'complete', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(source, migration_version) DO UPDATE SET
                status = 'complete', started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                legacy_row_count = excluded.legacy_row_count,
                fixed_row_count = excluded.fixed_row_count,
                legacy_hash = excluded.legacy_hash,
                fixed_hash = excluded.fixed_hash,
                null_value_count = excluded.null_value_count,
                zero_value_count = excluded.zero_value_count,
                database_bytes_before = excluded.database_bytes_before,
                database_bytes_side_by_side = excluded.database_bytes_side_by_side,
                error = NULL
            """,
            (
                source,
                OPERATIONAL_SCHEMA_VERSION,
                started_at,
                completed_at,
                parity["legacy_row_count"],
                parity["fixed_row_count"],
                parity["legacy_hash"],
                parity["fixed_hash"],
                parity["null_value_count"],
                parity["zero_value_count"],
                manifest["database"]["byte_count"],
                manifest["database"]["byte_count"],
            ),
        )
        connection.commit()

    deleted_files = 0
    deleted_bytes = 0
    file_actions: list[dict[str, Any]] = []
    for entry in manifest["files"]:
        path = Path(str(entry["absolute_path"])).resolve()
        _require_within(path, processed_dir)
        deleted_bytes += int(entry["byte_count"])
        path.unlink()
        deleted_files += 1
        file_actions.append(
            {
                "absolute_path": path.as_posix(),
                "byte_count": int(entry["byte_count"]),
                "sha256": entry["sha256"],
                "status": "deleted",
            }
        )
    directory_actions: list[dict[str, str]] = []
    for entry in sorted(manifest["directories"], key=lambda row: len(str(row["absolute_path"])), reverse=True):
        root = Path(str(entry["absolute_path"])).resolve()
        if root.is_dir():
            for directory in sorted(
                (path for path in root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                if not any(directory.iterdir()):
                    directory.rmdir()
            if not any(root.iterdir()):
                root.rmdir()
        directory_actions.append(
            {
                "absolute_path": root.as_posix(),
                "status": "removed" if not root.exists() else "retained_nonempty",
            }
        )

    vacuum: dict[str, Any] | None = None
    if vacuum_output is not None:
        output = vacuum_output.resolve()
        if output.exists():
            raise FileExistsError(f"Vacuum output already exists: {output.as_posix()}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            escaped = output.as_posix().replace("'", "''")
            connection.execute(f"VACUUM INTO '{escaped}'")
        vacuum = {
            "absolute_path": output.as_posix(),
            "byte_count": output.stat().st_size,
            "sha256": _sha256(output),
            "integrity_check": database_integrity(output),
        }
    return {
        "operation": "legacy_retirement",
        "status": "complete",
        "source": manifest["source"],
        "manifest_path": manifest_path.as_posix(),
        "manifest_sha256": actual_manifest_sha256,
        "backup": backup,
        "restore_rehearsal": restore_rehearsal,
        "table_actions": table_actions,
        "file_actions": file_actions,
        "directory_actions": directory_actions,
        "deleted_file_count": deleted_files,
        "deleted_file_bytes": deleted_bytes,
        "database_integrity_check": database_integrity(database_path),
        "vacuum_output": vacuum,
        "completed_at": _utc_now(),
    }


def database_integrity(path: Path) -> str:
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "missing_result"


def _database_inventory(path: Path, expected_source: str) -> dict[str, Any]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        source = configured_source(connection)
        if source not in (None, expected_source):
            raise ValueError(
                f"Operational store is configured for source {source!r}, not {expected_source!r}."
            )
        schema_version = (
            int(connection.execute("SELECT MAX(version) FROM observation_schema").fetchone()[0])
            if "observation_schema" in tables
            else None
        )
        candidates = {
            table: _table_count(connection, table) if table in tables else None
            for table in LEGACY_TABLE_CANDIDATES
        }
        protected = {
            table: _table_count(connection, table) if table in tables else None
            for table in PROTECTED_TABLES
        }
        parity = audit_legacy_snapshot_parity(connection, expected_source)
        other_source_rows = (
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {LEGACY_TABLE_CANDIDATES[0]} WHERE source <> ?",
                    (expected_source,),
                ).fetchone()[0]
            )
            if LEGACY_TABLE_CANDIDATES[0] in tables
            else 0
        )
        writer = (
            dict(connection.execute("SELECT * FROM admin_writer_lease WHERE lease_id = 1").fetchone())
            if "admin_writer_lease" in tables
            and connection.execute("SELECT 1 FROM admin_writer_lease WHERE lease_id = 1").fetchone()
            else None
        )
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    return {
        "schema_version": schema_version,
        "target_schema_version": OPERATIONAL_SCHEMA_VERSION,
        "configured_source": source,
        "integrity_check": integrity,
        "legacy_snapshot_parity": parity,
        "legacy_snapshot_other_source_rows": other_source_rows,
        "candidate_table_rows": candidates,
        "protected_table_rows": protected,
        "active_writer": writer,
    }


def _verify_database_identity(path: Path, expected: dict[str, Any]) -> None:
    stat = path.stat()
    if stat.st_size != int(expected["byte_count"]):
        raise RuntimeError("Operational database size changed after manifest generation.")
    if _sha256(path) != expected["sha256"]:
        raise RuntimeError("Operational database checksum changed after manifest generation.")


def _verify_manifest_files(files: Iterable[dict[str, Any]], processed_dir: Path) -> None:
    for entry in files:
        path = Path(str(entry["absolute_path"])).resolve()
        _require_within(path, processed_dir)
        if not path.is_file():
            raise RuntimeError(f"Manifest target disappeared: {path.as_posix()}")
        if path.stat().st_size != int(entry["byte_count"]) or _sha256(path) != entry["sha256"]:
            raise RuntimeError(f"Manifest target changed: {path.as_posix()}")


def _file_record(path: Path, processed_dir: Path) -> dict[str, Any]:
    resolved = path.resolve()
    _require_within(resolved, processed_dir)
    return {
        "relative_path": resolved.relative_to(processed_dir).as_posix(),
        "absolute_path": resolved.as_posix(),
        "byte_count": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _writer_active(connection: sqlite3.Connection) -> bool:
    return _table_exists(connection, "admin_writer_lease") and connection.execute(
        "SELECT 1 FROM admin_writer_lease WHERE lease_id = 1"
    ).fetchone() is not None


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _require_within(path: Path, parent: Path) -> None:
    if not _is_within(path, parent) or path == parent:
        raise ValueError(f"Retirement target escapes its allowed directory: {path.as_posix()}")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

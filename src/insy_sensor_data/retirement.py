from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
import gc
import hashlib
import json
import sqlite3
import zipfile

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
    with closing(sqlite3.connect(source)) as source_connection, closing(
        sqlite3.connect(target)
    ) as target_connection:
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


def prepare_retirement_checkpoint(
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    backup_path: Path,
    restore_test_path: Path,
    artifact_archive_path: Path,
    approval_bundle_path: Path,
) -> dict[str, Any]:
    """Create the verified, non-destructive evidence required to approve Checkpoint B."""
    manifest_path, manifest = _load_manifest(manifest_path, expected_manifest_sha256)
    processed_dir = Path(str(manifest["processed_dir"])).resolve()
    database_path = Path(str(manifest["database"]["absolute_path"])).resolve()
    outputs = [
        backup_path.resolve(),
        restore_test_path.resolve(),
        artifact_archive_path.resolve(),
        approval_bundle_path.resolve(),
    ]
    _validate_maintenance_paths(outputs, processed_dir)
    if manifest_path in outputs:
        raise ValueError("Preparation outputs must differ from the manifest.")

    _verify_live_targets(manifest, processed_dir, database_path)
    backup = create_database_backup(database_path, backup_path)
    backup["database"] = _database_inventory(backup_path.resolve(), str(manifest["source"]))
    _verify_inventory_matches_manifest(backup["database"], manifest["database"])

    restore = rehearse_database_restore(backup_path, restore_test_path)
    restore["database"] = _database_inventory(
        restore_test_path.resolve(), str(manifest["source"])
    )
    if _inventory_signature(restore["database"]) != _inventory_signature(backup["database"]):
        raise RuntimeError("Restored database inventory does not match the verified backup.")

    archive = create_artifact_archive(manifest, artifact_archive_path)
    _verify_live_targets(manifest, processed_dir, database_path)

    bundle = {
        "retirement_version": RETIREMENT_VERSION,
        "operation": "checkpoint_b_preparation",
        "status": "prepared",
        "prepared_at": _utc_now(),
        "manifest": {
            "absolute_path": manifest_path.as_posix(),
            "sha256": expected_manifest_sha256,
            "generated_at": manifest["generated_at"],
        },
        "source": manifest["source"],
        "database": manifest["database"],
        "backup": backup,
        "restore_rehearsal": restore,
        "artifact_archive": archive,
        "approved_cleanup": {
            "file_count": manifest["file_count"],
            "file_bytes": manifest["file_bytes"],
            "candidate_table_rows": manifest["database"]["candidate_table_rows"],
            "raw_evidence_targeted": False,
        },
        "approved": False,
    }
    destination = approval_bundle_path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Approval bundle already exists: {destination.as_posix()}")
    destination.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        **bundle,
        "approval_bundle_path": destination.as_posix(),
        "approval_bundle_sha256": _sha256(destination),
    }


def create_artifact_archive(manifest: dict[str, Any], destination: Path) -> dict[str, Any]:
    """Archive only the exact manifested processed files and verify every payload hash."""
    processed_dir = Path(str(manifest["processed_dir"])).resolve()
    target = destination.resolve()
    if _is_within(target, processed_dir):
        raise ValueError("Artifact archive must be stored outside data/processed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Artifact archive already exists: {target.as_posix()}")
    _verify_manifest_files(manifest["files"], processed_dir)
    with zipfile.ZipFile(
        target,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for entry in manifest["files"]:
            path = Path(str(entry["absolute_path"])).resolve()
            archive.write(path, arcname=f"processed/{entry['relative_path']}")
    verification = _verify_artifact_archive(target, manifest)
    return {
        "absolute_path": target.as_posix(),
        "byte_count": target.stat().st_size,
        "sha256": _sha256(target),
        "format": "zip",
        **verification,
        "created_at": _utc_now(),
    }


def apply_retirement_manifest(
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    approval_bundle_path: Path,
    expected_approval_bundle_sha256: str,
    vacuum_output: Path,
) -> dict[str, Any]:
    """Apply a separately approved preparation bundle to its still-identical live targets."""
    manifest_path, manifest = _load_manifest(manifest_path, expected_manifest_sha256)
    processed_dir = Path(str(manifest["processed_dir"])).resolve()
    database_path = Path(str(manifest["database"]["absolute_path"])).resolve()
    vacuum_output = vacuum_output.resolve()
    _validate_maintenance_paths(
        [approval_bundle_path.resolve(), vacuum_output], processed_dir
    )
    preparation = verify_retirement_checkpoint(
        manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
        approval_bundle_path=approval_bundle_path,
        expected_approval_bundle_sha256=expected_approval_bundle_sha256,
    )
    if vacuum_output.exists():
        raise FileExistsError(f"Vacuum output already exists: {vacuum_output.as_posix()}")
    started_at = _utc_now()
    table_actions: list[dict[str, Any]] = []
    with closing(sqlite3.connect(database_path)) as connection:
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

    vacuum_output.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        escaped = vacuum_output.as_posix().replace("'", "''")
        connection.execute(f"VACUUM INTO '{escaped}'")
    vacuum = _database_record(vacuum_output, str(manifest["source"]))
    database_after = _database_record(database_path, str(manifest["source"]))
    if _inventory_signature(vacuum["database"]) != _inventory_signature(
        database_after["database"]
    ):
        raise RuntimeError("Compacted database inventory does not match the retired live database.")
    return {
        "operation": "legacy_retirement",
        "status": "complete",
        "source": manifest["source"],
        "manifest_path": manifest_path.as_posix(),
        "manifest_sha256": expected_manifest_sha256,
        "approval_bundle_path": approval_bundle_path.resolve().as_posix(),
        "approval_bundle_sha256": expected_approval_bundle_sha256,
        "backup": preparation["backup"],
        "restore_rehearsal": preparation["restore_rehearsal"],
        "artifact_archive": preparation["artifact_archive"],
        "table_actions": table_actions,
        "file_actions": file_actions,
        "directory_actions": directory_actions,
        "deleted_file_count": deleted_files,
        "deleted_file_bytes": deleted_bytes,
        "database_integrity_check": database_integrity(database_path),
        "database_after": database_after,
        "vacuum_output": vacuum,
        "completed_at": _utc_now(),
    }


def verify_retirement_checkpoint(
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    approval_bundle_path: Path,
    expected_approval_bundle_sha256: str,
) -> dict[str, Any]:
    """Verify the complete approval bundle and prove that every live target is unchanged."""
    manifest_path, manifest = _load_manifest(manifest_path, expected_manifest_sha256)
    bundle_path = approval_bundle_path.resolve()
    if _sha256(bundle_path) != expected_approval_bundle_sha256:
        raise ValueError("Approval bundle checksum does not match the explicit confirmation value.")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if bundle.get("status") != "prepared" or bundle.get("approved") is not False:
        raise ValueError("Approval bundle is not an eligible Checkpoint B preparation.")
    recorded_manifest = bundle.get("manifest", {})
    if (
        Path(str(recorded_manifest.get("absolute_path"))).resolve() != manifest_path
        or recorded_manifest.get("sha256") != expected_manifest_sha256
    ):
        raise ValueError("Approval bundle does not identify the confirmed manifest.")

    processed_dir = Path(str(manifest["processed_dir"])).resolve()
    database_path = Path(str(manifest["database"]["absolute_path"])).resolve()
    if bundle.get("source") != manifest.get("source"):
        raise ValueError("Approval bundle source does not match the manifest.")
    if bundle.get("database") != manifest.get("database"):
        raise ValueError("Approval bundle database evidence does not match the manifest.")

    backup = bundle["backup"]
    restore = bundle["restore_rehearsal"]
    archive = bundle["artifact_archive"]
    maintenance_paths = [
        bundle_path,
        Path(str(backup["absolute_path"])).resolve(),
        Path(str(restore["absolute_path"])).resolve(),
        Path(str(archive["absolute_path"])).resolve(),
    ]
    _validate_maintenance_paths(maintenance_paths, processed_dir)
    _verify_recorded_file(backup)
    if database_integrity(Path(str(backup["absolute_path"]))) != "ok":
        raise RuntimeError("Prepared database backup no longer passes integrity checks.")
    backup_inventory = _database_inventory(
        Path(str(backup["absolute_path"])).resolve(), str(manifest["source"])
    )
    _verify_inventory_matches_manifest(backup_inventory, manifest["database"])

    _verify_recorded_file(restore)
    if restore.get("source_backup_sha256") != backup["sha256"]:
        raise RuntimeError("Restore rehearsal is not bound to the prepared backup.")
    restore_inventory = _database_inventory(
        Path(str(restore["absolute_path"])).resolve(), str(manifest["source"])
    )
    if _inventory_signature(restore_inventory) != _inventory_signature(backup_inventory):
        raise RuntimeError("Restore rehearsal inventory no longer matches the backup.")

    _verify_recorded_file(archive)
    _verify_artifact_archive(Path(str(archive["absolute_path"])), manifest)
    _verify_live_targets(manifest, processed_dir, database_path)
    return bundle


def activate_compacted_database(
    apply_result_path: Path,
    *,
    expected_apply_result_sha256: str,
    expected_vacuum_sha256: str,
    displaced_database_path: Path,
) -> dict[str, Any]:
    """Atomically activate a verified compacted database while retaining its predecessor."""
    result_path = apply_result_path.resolve()
    if _sha256(result_path) != expected_apply_result_sha256:
        raise ValueError("Apply-result checksum does not match the explicit confirmation value.")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "complete":
        raise ValueError("Apply result is not complete and cannot be activated.")
    live_record = result["database_after"]
    vacuum_record = result["vacuum_output"]
    live_path = Path(str(live_record["absolute_path"])).resolve()
    vacuum_path = Path(str(vacuum_record["absolute_path"])).resolve()
    displaced = displaced_database_path.resolve()
    processed_dir = live_path.parent.resolve()
    _validate_maintenance_paths([result_path, vacuum_path, displaced], processed_dir)
    if displaced.exists():
        raise FileExistsError(f"Displaced database destination already exists: {displaced.as_posix()}")
    if vacuum_record.get("sha256") != expected_vacuum_sha256:
        raise ValueError("Vacuum checksum does not match the apply result.")
    _verify_recorded_file(live_record)
    _verify_recorded_file(vacuum_record)
    if database_integrity(live_path) != "ok" or database_integrity(vacuum_path) != "ok":
        raise RuntimeError("Live or compacted database failed integrity checks before activation.")
    live_inventory = live_record["database"]
    current_live_inventory = _database_inventory(live_path, str(result["source"]))
    compacted_inventory = _database_inventory(vacuum_path, str(result["source"]))
    if current_live_inventory.get("active_writer") is not None:
        raise RuntimeError("An administrative writer lease is active; activation is blocked.")
    if (
        _inventory_signature(current_live_inventory) != _inventory_signature(live_inventory)
        or _inventory_signature(compacted_inventory) != _inventory_signature(live_inventory)
    ):
        raise RuntimeError("Live and compacted database inventories do not match.")
    sidecars = [Path(f"{live_path}{suffix}") for suffix in ("-wal", "-shm", "-journal")]
    existing_sidecars = [path.as_posix() for path in sidecars if path.exists()]
    if existing_sidecars:
        raise RuntimeError(f"SQLite sidecar files remain; stop the service first: {existing_sidecars}")

    # sqlite3 connection/statement cycles can otherwise retain a Windows file handle
    # briefly after verification, even though every explicit connection is closed.
    gc.collect()
    displaced.parent.mkdir(parents=True, exist_ok=True)
    live_path.replace(displaced)
    try:
        vacuum_path.replace(live_path)
        activated = _database_record(live_path, str(result["source"]))
        if activated["sha256"] != expected_vacuum_sha256:
            raise RuntimeError(
                "Activated database checksum does not match the verified compacted output."
            )
    except Exception:
        if live_path.exists() and not vacuum_path.exists():
            live_path.replace(vacuum_path)
        if displaced.exists() and not live_path.exists():
            displaced.replace(live_path)
        raise
    return {
        "operation": "checkpoint_b_activation",
        "status": "complete",
        "source": result["source"],
        "apply_result_path": result_path.as_posix(),
        "apply_result_sha256": expected_apply_result_sha256,
        "activated_database": activated,
        "displaced_database": _database_record(displaced, str(result["source"])),
        "completed_at": _utc_now(),
    }


def database_integrity(path: Path) -> str:
    with closing(
        sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    ) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "missing_result"


def _load_manifest(
    manifest_path: Path,
    expected_manifest_sha256: str,
) -> tuple[Path, dict[str, Any]]:
    path = manifest_path.resolve()
    if _sha256(path) != expected_manifest_sha256:
        raise ValueError("Manifest checksum does not match the explicit confirmation value.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "dry_run" or manifest.get("raw_evidence_targeted") is not False:
        raise ValueError("Manifest is not an eligible 0.6.6 dry run.")
    return path, manifest


def _validate_maintenance_paths(paths: Iterable[Path], processed_dir: Path) -> None:
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Maintenance outputs must use distinct paths.")
    for path in resolved:
        if _is_within(path, processed_dir):
            raise ValueError("Maintenance outputs must be stored outside data/processed.")


def _verify_live_targets(
    manifest: dict[str, Any],
    processed_dir: Path,
    database_path: Path,
) -> None:
    _verify_database_identity(database_path, manifest["database"])
    _verify_manifest_files(manifest["files"], processed_dir)
    inventory = _database_inventory(database_path, str(manifest["source"]))
    if inventory.get("active_writer") is not None:
        raise RuntimeError("An administrative writer lease is active; retirement is blocked.")
    _verify_inventory_matches_manifest(inventory, manifest["database"])


def _database_record(path: Path, source: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "absolute_path": resolved.as_posix(),
        "byte_count": resolved.stat().st_size,
        "sha256": _sha256(resolved),
        "integrity_check": database_integrity(resolved),
        "database": _database_inventory(resolved, source),
    }


def _verify_recorded_file(record: dict[str, Any]) -> None:
    path = Path(str(record["absolute_path"])).resolve()
    if not path.is_file():
        raise RuntimeError(f"Prepared maintenance file is missing: {path.as_posix()}")
    if path.stat().st_size != int(record["byte_count"]) or _sha256(path) != record["sha256"]:
        raise RuntimeError(f"Prepared maintenance file changed: {path.as_posix()}")


def _verify_inventory_matches_manifest(
    inventory: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    if _inventory_signature(inventory) != _inventory_signature(expected):
        raise RuntimeError("Database inventory no longer matches the retirement manifest.")


def _inventory_signature(inventory: dict[str, Any]) -> dict[str, Any]:
    parity = inventory.get("legacy_snapshot_parity") or {}
    return {
        "schema_version": inventory.get("schema_version"),
        "configured_source": inventory.get("configured_source"),
        "integrity_check": inventory.get("integrity_check"),
        "legacy_snapshot_parity": {
            key: parity.get(key)
            for key in (
                "status",
                "source",
                "legacy_row_count",
                "fixed_row_count",
                "legacy_hash",
                "fixed_hash",
                "null_value_count",
                "zero_value_count",
            )
        },
        "legacy_snapshot_other_source_rows": inventory.get(
            "legacy_snapshot_other_source_rows"
        ),
        "candidate_table_rows": inventory.get("candidate_table_rows"),
        "protected_table_rows": inventory.get("protected_table_rows"),
    }


def _verify_artifact_archive(
    archive_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    expected = {
        f"processed/{entry['relative_path']}": entry for entry in manifest["files"]
    }
    with zipfile.ZipFile(archive_path.resolve(), mode="r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise RuntimeError("Artifact archive contents do not match the retirement manifest.")
        for name, entry in expected.items():
            info = archive.getinfo(name)
            digest = hashlib.sha256()
            with archive.open(info, mode="r") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if info.file_size != int(entry["byte_count"]) or digest.hexdigest() != entry["sha256"]:
                raise RuntimeError(f"Artifact archive payload failed verification: {name}")
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"Artifact archive CRC check failed: {bad_member}")
    return {
        "file_count": len(expected),
        "payload_bytes": sum(int(entry["byte_count"]) for entry in expected.values()),
        "verification": "complete",
    }


def _database_inventory(path: Path, expected_source: str) -> dict[str, Any]:
    with closing(
        sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    ) as connection:
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

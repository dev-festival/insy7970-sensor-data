from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.schema import SNAPSHOT_FIELDS
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.store.errors import StoreMigrationRequiredError
from insy_sensor_data.store.events import (
    initialize_event_schema,
    record_waites_event_coverage,
    upsert_waites_events,
)
from insy_sensor_data.store.schema import (
    FIXED_SNAPSHOT_TABLE,
    OPERATIONAL_SCHEMA_VERSION,
    _replace_fixed_rows,
    active_snapshot_table,
    claim_configured_source,
    initialize_operational_schema,
    snapshot_revision,
)
from insy_sensor_data.waites.asset_tree import normalize_asset_tree_records


OBSERVATION_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION
VALID_OBSERVATION_SOURCES = {"mock", "api"}
VALID_RAW_RETENTION_MODES = {"release", "compress", "keep"}


def observation_db_path(settings: AppSettings) -> Path:
    return get_storage_paths(settings.data_dir).observations_db_path


def connect_observation_store(settings: AppSettings) -> sqlite3.Connection:
    path = observation_db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_observation_store(connection)
    return connection


def initialize_observation_store(connection: sqlite3.Connection) -> None:
    """Create only the operational store used by sync, web reads, and exports."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS observation_schema (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS waites_equipment_reference (
            source TEXT NOT NULL,
            equipment_id INTEGER NOT NULL,
            asset_tree_id INTEGER,
            name TEXT,
            facility_id INTEGER,
            customer_asset_id TEXT,
            first_loaded_at TEXT NOT NULL,
            last_loaded_at TEXT NOT NULL,
            last_source_date TEXT NOT NULL,
            PRIMARY KEY (source, equipment_id)
        );

        CREATE TABLE IF NOT EXISTS waites_asset_tree_reference (
            source TEXT NOT NULL,
            asset_tree_id INTEGER NOT NULL,
            name TEXT,
            parent_asset_tree_id INTEGER,
            facility_id INTEGER,
            asset_tree_path TEXT,
            first_loaded_at TEXT NOT NULL,
            last_loaded_at TEXT NOT NULL,
            last_source_date TEXT NOT NULL,
            PRIMARY KEY (source, asset_tree_id)
        );

        CREATE TABLE IF NOT EXISTS waites_installation_point_reference (
            source TEXT NOT NULL,
            installation_point_id INTEGER NOT NULL,
            name TEXT,
            equipment_id INTEGER,
            sensor_id INTEGER,
            facility_id INTEGER,
            last_seen TEXT,
            installation_date TEXT,
            customer_asset_id TEXT,
            first_loaded_at TEXT NOT NULL,
            last_loaded_at TEXT NOT NULL,
            last_source_date TEXT NOT NULL,
            PRIMARY KEY (source, installation_point_id)
        );

        CREATE TABLE IF NOT EXISTS cluster_model_runs (
            model_run_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            feature_space TEXT NOT NULL,
            k INTEGER NOT NULL,
            algorithm TEXT NOT NULL,
            random_seed INTEGER NOT NULL,
            feature_policy_version TEXT NOT NULL,
            feature_columns_json TEXT NOT NULL,
            scaler_policy TEXT NOT NULL,
            input_snapshot_hash TEXT,
            input_snapshot_row_count INTEGER NOT NULL,
            feature_row_count INTEGER NOT NULL,
            feature_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            artifact_dir TEXT,
            metrics_json TEXT,
            warnings_json TEXT,
            UNIQUE (
                source, source_date, feature_space, k, algorithm,
                random_seed, feature_policy_version
            )
        );

        CREATE TABLE IF NOT EXISTS cluster_model_assignments (
            model_run_id TEXT NOT NULL,
            installation_point_id TEXT NOT NULL,
            sensor_id TEXT,
            equipment_id TEXT,
            equipment_name TEXT,
            customer_asset_id TEXT,
            installation_point_name TEXT,
            cluster INTEGER NOT NULL,
            distance_to_centroid REAL,
            pca_x REAL,
            pca_y REAL,
            features_json TEXT,
            PRIMARY KEY (model_run_id, installation_point_id),
            FOREIGN KEY (model_run_id) REFERENCES cluster_model_runs(model_run_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cluster_model_centroids (
            model_run_id TEXT NOT NULL,
            cluster INTEGER NOT NULL,
            sensor_count INTEGER NOT NULL,
            centroid_json TEXT NOT NULL,
            pca_x REAL,
            pca_y REAL,
            summary_json TEXT,
            PRIMARY KEY (model_run_id, cluster),
            FOREIGN KEY (model_run_id) REFERENCES cluster_model_runs(model_run_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cluster_drift_runs (
            drift_run_id TEXT PRIMARY KEY,
            from_model_run_id TEXT NOT NULL,
            to_model_run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            from_date TEXT NOT NULL,
            to_date TEXT NOT NULL,
            feature_space TEXT NOT NULL,
            k INTEGER NOT NULL,
            alignment_policy TEXT NOT NULL,
            matched_sensor_count INTEGER NOT NULL,
            raw_changed_sensor_count INTEGER NOT NULL,
            aligned_changed_sensor_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            metrics_json TEXT,
            warnings_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (from_model_run_id) REFERENCES cluster_model_runs(model_run_id),
            FOREIGN KEY (to_model_run_id) REFERENCES cluster_model_runs(model_run_id)
        );

        CREATE TABLE IF NOT EXISTS cluster_drift_assignments (
            drift_run_id TEXT NOT NULL,
            installation_point_id TEXT NOT NULL,
            from_cluster INTEGER,
            to_cluster INTEGER,
            aligned_to_cluster INTEGER,
            status TEXT NOT NULL,
            raw_changed INTEGER NOT NULL,
            aligned_changed INTEGER NOT NULL,
            distance_delta REAL,
            PRIMARY KEY (drift_run_id, installation_point_id),
            FOREIGN KEY (drift_run_id) REFERENCES cluster_drift_runs(drift_run_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cluster_centroid_alignment (
            drift_run_id TEXT NOT NULL,
            from_cluster INTEGER NOT NULL,
            to_cluster INTEGER,
            distance REAL,
            mapping_confidence TEXT,
            PRIMARY KEY (drift_run_id, from_cluster),
            FOREIGN KEY (drift_run_id) REFERENCES cluster_drift_runs(drift_run_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS waites_ingestion_ledger (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            facility_id INTEGER NOT NULL,
            fetched_at TEXT,
            validated_at TEXT,
            snapshot_built_at TEXT,
            validation_status TEXT,
            validation_error_count INTEGER,
            validation_warning_count INTEGER,
            endpoint_counts_json TEXT NOT NULL,
            endpoint_artifacts_json TEXT NOT NULL,
            manifest_sha256 TEXT,
            snapshot_row_count INTEGER NOT NULL,
            snapshot_store_status TEXT NOT NULL,
            raw_retention_mode TEXT NOT NULL,
            raw_retention_status TEXT NOT NULL,
            native_retention_status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (source, source_date, facility_id)
        );

        CREATE INDEX IF NOT EXISTS idx_waites_equipment_reference_asset
            ON waites_equipment_reference (source, customer_asset_id);
        CREATE INDEX IF NOT EXISTS idx_waites_asset_tree_reference_parent
            ON waites_asset_tree_reference (source, parent_asset_tree_id);
        CREATE INDEX IF NOT EXISTS idx_waites_installation_reference_equipment
            ON waites_installation_point_reference (source, equipment_id);
        CREATE INDEX IF NOT EXISTS idx_waites_installation_reference_asset
            ON waites_installation_point_reference (source, customer_asset_id);
        CREATE INDEX IF NOT EXISTS idx_waites_ingestion_ledger_date
            ON waites_ingestion_ledger (source, source_date);
        CREATE INDEX IF NOT EXISTS idx_cluster_model_runs_lookup
            ON cluster_model_runs (source, source_date, feature_space, k, status);
        CREATE INDEX IF NOT EXISTS idx_cluster_model_runs_feature_space
            ON cluster_model_runs (source, feature_space, k, status);
        CREATE INDEX IF NOT EXISTS idx_cluster_model_assignments_equipment
            ON cluster_model_assignments (model_run_id, equipment_id, installation_point_id);
        CREATE INDEX IF NOT EXISTS idx_cluster_drift_runs_lookup
            ON cluster_drift_runs (source, from_date, to_date, feature_space, k, status);
        """
    )
    initialize_event_schema(connection)
    initialize_operational_schema(connection)
    connection.execute(
        "INSERT OR IGNORE INTO observation_schema (version, applied_at) VALUES (?, ?)",
        (OBSERVATION_SCHEMA_VERSION, _utc_now()),
    )
    connection.commit()


def persist_validated_waites_day(
    settings: AppSettings,
    run_date: date,
    source: str,
    payloads: dict[str, list[dict[str, Any]]],
    snapshot_rows: list[dict[str, Any]],
    validation_report: dict[str, Any],
    manifest_path: Path,
    built_at: str | None = None,
    failure_point: str | None = None,
) -> dict[str, Any]:
    """Atomically publish references, events, and fixed daily facts."""
    valid_failure_points = {
        None,
        "after_validation",
        "after_references",
        "after_events",
        "after_snapshots",
    }
    if failure_point not in valid_failure_points:
        raise ValueError(f"Unsupported ingestion failure point: {failure_point}")
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    manifest = read_json(manifest_path)
    facility_id = _as_int(manifest.get("facility_id")) or settings.waites_facility_id
    stored_at = built_at or _utc_now()
    revision = snapshot_revision(source_mode, source_date, snapshot_rows)
    started_at = _utc_now()

    with connect_observation_store(settings) as connection:
        table = active_snapshot_table(connection)
        if table != FIXED_SNAPSHOT_TABLE:
            raise StoreMigrationRequiredError(
                "Direct durable ingestion requires the verified fixed snapshot table."
            )
        _set_ingestion_run_state(
            connection,
            source=source_mode,
            source_date=source_date,
            facility_id=facility_id,
            state="started",
            started_at=started_at,
        )
        connection.commit()
        _set_ingestion_run_state(
            connection,
            source=source_mode,
            source_date=source_date,
            facility_id=facility_id,
            state="validated",
            started_at=started_at,
        )
        connection.commit()
        try:
            if failure_point == "after_validation":
                raise RuntimeError("Injected ingestion failure after validation")
            connection.execute("BEGIN IMMEDIATE")
            claim_configured_source(connection, source_mode)
            reference_counts = _upsert_durable_waites_references(
                connection,
                source=source_mode,
                source_date=source_date,
                loaded_at=stored_at,
                payloads=payloads,
            )
            if failure_point == "after_references":
                raise RuntimeError("Injected ingestion failure after references")
            event_count = upsert_waites_events(
                connection,
                source=source_mode,
                source_date=source_date,
                observed_at=stored_at,
                rows=payloads.get("action-items", []),
            )
            record_waites_event_coverage(
                connection,
                source=source_mode,
                source_date=source_date,
                state="imported" if event_count else "genuinely_empty",
                input_mode="direct_raw",
                event_observation_count=event_count,
                checked_at=stored_at,
            )
            if failure_point == "after_events":
                raise RuntimeError("Injected ingestion failure after events")
            connection.execute(
                f"DELETE FROM {FIXED_SNAPSHOT_TABLE} WHERE source = ? AND source_date = ?",
                (source_mode, source_date),
            )
            _replace_fixed_rows(
                connection,
                source=source_mode,
                source_date=source_date,
                built_at=stored_at,
                revision=revision,
                rows=snapshot_rows,
            )
            if failure_point == "after_snapshots":
                raise RuntimeError("Injected ingestion failure after snapshots")
            _write_ingestion_ledger_row(
                connection,
                source=source_mode,
                source_date=source_date,
                facility_id=facility_id,
                manifest_path=manifest_path,
                manifest=manifest,
                validation_report=validation_report,
                snapshot_row_count=len(snapshot_rows),
                snapshot_revision_value=revision,
                snapshot_built_at=stored_at,
            )
            stored_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {FIXED_SNAPSHOT_TABLE} "
                    "WHERE source = ? AND source_date = ?",
                    (source_mode, source_date),
                ).fetchone()[0]
            )
            if stored_count != len(snapshot_rows):
                raise RuntimeError(
                    f"Snapshot verification failed: expected {len(snapshot_rows)}, "
                    f"stored {stored_count}."
                )
            _set_ingestion_run_state(
                connection,
                source=source_mode,
                source_date=source_date,
                facility_id=facility_id,
                state="stored",
                started_at=started_at,
                snapshot_revision_value=revision,
                snapshot_row_count=stored_count,
            )
            _set_ingestion_run_state(
                connection,
                source=source_mode,
                source_date=source_date,
                facility_id=facility_id,
                state="complete",
                started_at=started_at,
                snapshot_revision_value=revision,
                snapshot_row_count=stored_count,
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            _set_ingestion_run_state(
                connection,
                source=source_mode,
                source_date=source_date,
                facility_id=facility_id,
                state="failed",
                started_at=started_at,
                error=str(exc),
            )
            connection.commit()
            raise

    row_counts = {
        **reference_counts,
        "rms": 0,
        "impact": 0,
        "temperature": 0,
        "action_items": event_count,
    }
    return {
        "source": source_mode,
        "date": source_date,
        "facility_id": facility_id,
        "database_path": observation_db_path(settings).as_posix(),
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "ingestion_state": "complete",
        "snapshot_revision": revision,
        "snapshot_row_count": len(snapshot_rows),
        "staging_row_count": 0,
        "row_counts": row_counts,
        "snapshot_store": {
            "source": source_mode,
            "date": source_date,
            "table": FIXED_SNAPSHOT_TABLE,
            "row_count": len(snapshot_rows),
            "stored_at": stored_at,
            "snapshot_revision": revision,
            "snapshot_csv_path": None,
        },
        "ledger": {
            "source": source_mode,
            "date": source_date,
            "facility_id": facility_id,
            "table": "waites_ingestion_ledger",
            "snapshot_row_count": len(snapshot_rows),
            "snapshot_revision": revision,
            "snapshot_store_status": "stored",
            "ingestion_state": "complete",
        },
    }


def load_sensor_daily_snapshots(
    settings: AppSettings,
    run_date: date,
    source: str,
) -> list[dict[str, Any]]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    with connect_observation_store(settings) as connection:
        table = active_snapshot_table(connection)
        rows = _query_dicts(
            connection,
            f"SELECT * FROM {table} WHERE source = ? AND source_date = ? "
            "ORDER BY CAST(installation_point_id AS INTEGER), installation_point_id",
            (source_mode, source_date),
        )
    if not rows:
        raise FileNotFoundError(
            f"Missing SQLite daily snapshots for source {source_mode} date {source_date}."
        )
    return [
        {field: value for field, value in row.items() if field in SNAPSHOT_FIELDS}
        for row in rows
    ]


def verify_sensor_daily_snapshot(
    settings: AppSettings,
    run_date: date,
    source: str,
    expected_row_count: int | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    with connect_observation_store(settings) as connection:
        table = active_snapshot_table(connection)
        row_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source = ? AND source_date = ?",
                (source_mode, source_date),
            ).fetchone()[0]
        )
    issues: list[dict[str, Any]] = []
    if row_count == 0:
        issues.append({"level": "error", "code": "missing_daily_snapshot"})
    if expected_row_count is not None and row_count != expected_row_count:
        issues.append(
            {
                "level": "error",
                "code": "snapshot_row_count_mismatch",
                "expected": expected_row_count,
                "actual": row_count,
            }
        )
    return {
        "source": source_mode,
        "date": source_date,
        "database_path": observation_db_path(settings).as_posix(),
        "table": table,
        "row_count": row_count,
        "expected_row_count": expected_row_count,
        "status": "invalid" if issues else "valid",
        "error_count": len(issues),
        "issues": issues,
    }


def update_ingestion_retention(
    settings: AppSettings,
    run_date: date,
    source: str,
    raw_retention_mode: str,
    raw_retention_status: str,
    native_retention_status: str,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    retention_mode = _validate_raw_retention(raw_retention_mode)
    source_date = run_date.isoformat()
    manifest_path = get_storage_paths(settings.data_dir).raw_waites_run_dir(source_date) / "manifest.json"
    manifest = read_json(manifest_path)
    updated_at = _utc_now()
    endpoint_artifacts = _manifest_endpoint_artifacts(manifest)
    with connect_observation_store(settings) as connection:
        ledger = _get_ledger_row(connection, source_date=source_date, source=source_mode)
        if ledger is None:
            raise FileNotFoundError(
                f"Missing ingestion ledger for source {source_mode} date {source_date}."
            )
        connection.execute(
            """
            UPDATE waites_ingestion_ledger
            SET endpoint_artifacts_json = ?, manifest_sha256 = ?,
                raw_retention_mode = ?, raw_retention_status = ?,
                native_retention_status = ?, updated_at = ?
            WHERE source = ? AND source_date = ? AND facility_id = ?
            """,
            (
                json.dumps(endpoint_artifacts, sort_keys=True),
                _sha256(manifest_path),
                retention_mode,
                raw_retention_status,
                native_retention_status,
                updated_at,
                source_mode,
                source_date,
                ledger["facility_id"],
            ),
        )
        connection.commit()
    return {
        "source": source_mode,
        "date": source_date,
        "facility_id": ledger["facility_id"],
        "raw_retention_mode": retention_mode,
        "raw_retention_status": raw_retention_status,
        "native_retention_status": native_retention_status,
        "updated_at": updated_at,
    }


def load_ingestion_ledger(
    settings: AppSettings,
    run_date: date,
    source: str,
) -> dict[str, Any] | None:
    source_mode = _validate_source(source)
    with connect_observation_store(settings) as connection:
        ledger = _get_ledger_row(
            connection,
            source_date=run_date.isoformat(),
            source=source_mode,
        )
    if ledger is None:
        return None
    ledger["endpoint_counts"] = json.loads(ledger.pop("endpoint_counts_json"))
    ledger["endpoint_artifacts"] = json.loads(ledger.pop("endpoint_artifacts_json"))
    return ledger


def _upsert_durable_waites_references(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_date: str,
    loaded_at: str,
    payloads: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    asset_tree_rows = [
        _asset_tree_reference_row(source, source_date, loaded_at, row)
        for row in normalize_asset_tree_records(payloads.get("asset-tree", []))
    ]
    equipment_rows = [
        _equipment_reference_row(source, source_date, loaded_at, row)
        for row in payloads["equipment"]
    ]
    installation_rows = [
        _installation_point_reference_row(source, source_date, loaded_at, row)
        for row in payloads["installation-points"]
    ]
    connection.executemany(
        """
        INSERT INTO waites_asset_tree_reference (
            source, asset_tree_id, name, parent_asset_tree_id, facility_id,
            asset_tree_path, first_loaded_at, last_loaded_at, last_source_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, asset_tree_id) DO UPDATE SET
            name = excluded.name,
            parent_asset_tree_id = excluded.parent_asset_tree_id,
            facility_id = excluded.facility_id,
            asset_tree_path = excluded.asset_tree_path,
            last_loaded_at = excluded.last_loaded_at,
            last_source_date = excluded.last_source_date
        """,
        asset_tree_rows,
    )
    connection.executemany(
        """
        INSERT INTO waites_equipment_reference (
            source, equipment_id, asset_tree_id, name, facility_id,
            customer_asset_id, first_loaded_at, last_loaded_at, last_source_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, equipment_id) DO UPDATE SET
            asset_tree_id = excluded.asset_tree_id,
            name = excluded.name,
            facility_id = excluded.facility_id,
            customer_asset_id = excluded.customer_asset_id,
            last_loaded_at = excluded.last_loaded_at,
            last_source_date = excluded.last_source_date
        """,
        equipment_rows,
    )
    connection.executemany(
        """
        INSERT INTO waites_installation_point_reference (
            source, installation_point_id, name, equipment_id, sensor_id,
            facility_id, last_seen, installation_date, customer_asset_id,
            first_loaded_at, last_loaded_at, last_source_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, installation_point_id) DO UPDATE SET
            name = excluded.name,
            equipment_id = excluded.equipment_id,
            sensor_id = excluded.sensor_id,
            facility_id = excluded.facility_id,
            last_seen = excluded.last_seen,
            installation_date = excluded.installation_date,
            customer_asset_id = excluded.customer_asset_id,
            last_loaded_at = excluded.last_loaded_at,
            last_source_date = excluded.last_source_date
        """,
        installation_rows,
    )
    return {
        "asset_trees": len(asset_tree_rows),
        "equipment": len(equipment_rows),
        "installation_points": len(installation_rows),
    }


def _write_ingestion_ledger_row(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_date: str,
    facility_id: int,
    manifest_path: Path,
    manifest: dict[str, Any],
    validation_report: dict[str, Any],
    snapshot_row_count: int,
    snapshot_revision_value: str,
    snapshot_built_at: str,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO waites_ingestion_ledger (
            source, source_date, facility_id, fetched_at, validated_at,
            snapshot_built_at, validation_status, validation_error_count,
            validation_warning_count, endpoint_counts_json,
            endpoint_artifacts_json, manifest_sha256, snapshot_row_count,
            snapshot_store_status, raw_retention_mode, raw_retention_status,
            native_retention_status, updated_at, ingestion_state,
            snapshot_revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            source_date,
            facility_id,
            manifest.get("fetched_at"),
            validation_report.get("validated_at"),
            snapshot_built_at,
            validation_report.get("status"),
            _as_int(validation_report.get("error_count")) or 0,
            _as_int(validation_report.get("warning_count")) or 0,
            json.dumps(_manifest_endpoint_counts(manifest), sort_keys=True),
            json.dumps(_manifest_endpoint_artifacts(manifest), sort_keys=True),
            _sha256(manifest_path),
            snapshot_row_count,
            "stored",
            "keep",
            "kept",
            "not_applicable",
            _utc_now(),
            "complete",
            snapshot_revision_value,
        ),
    )


def _set_ingestion_run_state(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_date: str,
    facility_id: int,
    state: str,
    started_at: str,
    snapshot_revision_value: str | None = None,
    snapshot_row_count: int = 0,
    error: str | None = None,
) -> None:
    transitioned_at = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO ingestion_runs (
            source, source_date, facility_id, state, snapshot_revision,
            snapshot_row_count, started_at, updated_at, completed_at, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_date, facility_id) DO UPDATE SET
            state = excluded.state,
            snapshot_revision = COALESCE(
                excluded.snapshot_revision, ingestion_runs.snapshot_revision
            ),
            snapshot_row_count = excluded.snapshot_row_count,
            started_at = excluded.started_at,
            updated_at = excluded.updated_at,
            completed_at = excluded.completed_at,
            error = excluded.error
        """,
        (
            source,
            source_date,
            facility_id,
            state,
            snapshot_revision_value,
            snapshot_row_count,
            started_at,
            transitioned_at,
            transitioned_at if state == "complete" else None,
            error,
        ),
    )
    connection.execute(
        """
        INSERT INTO ingestion_run_transitions (
            source, source_date, facility_id, state, transitioned_at, detail
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source, source_date, facility_id, state, transitioned_at, error),
    )


def _manifest_endpoint_counts(manifest: dict[str, Any]) -> dict[str, int | None]:
    return {
        str(endpoint.get("name")): _as_int(endpoint.get("record_count"))
        for endpoint in manifest.get("endpoints", [])
        if isinstance(endpoint, dict) and endpoint.get("name") is not None
    }


def _manifest_endpoint_artifacts(manifest: dict[str, Any]) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for endpoint in manifest.get("endpoints", []):
        if not isinstance(endpoint, dict) or endpoint.get("name") is None:
            continue
        artifact = endpoint.get("artifact") if isinstance(endpoint.get("artifact"), dict) else {}
        artifacts[str(endpoint["name"])] = {
            "record_count": endpoint.get("record_count"),
            "path": endpoint.get("path"),
            "artifact": dict(artifact),
        }
    return artifacts


def _get_ledger_row(
    connection: sqlite3.Connection,
    source_date: str,
    source: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM waites_ingestion_ledger
        WHERE source = ? AND source_date = ?
        ORDER BY updated_at DESC LIMIT 1
        """,
        (source, source_date),
    ).fetchone()
    return dict(row) if row is not None else None


def _asset_tree_reference_row(
    source: str,
    source_date: str,
    loaded_at: str,
    row: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        source,
        _as_int(row.get("asset_tree_id")),
        _as_text(row.get("name")),
        _as_int(row.get("parent_asset_tree_id")),
        _as_int(row.get("facility_id")),
        _as_text(row.get("asset_tree_path")),
        loaded_at,
        loaded_at,
        source_date,
    )


def _equipment_reference_row(
    source: str,
    source_date: str,
    loaded_at: str,
    row: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        source,
        _as_int(row.get("equipment_id")),
        _as_int(row.get("asset_tree_id")),
        _as_text(row.get("name")),
        _as_int(row.get("facility_id")),
        _as_text(row.get("customer_asset_id")),
        loaded_at,
        loaded_at,
        source_date,
    )


def _installation_point_reference_row(
    source: str,
    source_date: str,
    loaded_at: str,
    row: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        source,
        _as_int(row.get("installation_point_id")),
        _as_text(row.get("name")),
        _as_int(row.get("equipment_id")),
        _as_int(row.get("sensor_id")),
        _as_int(row.get("facility_id")),
        _as_text(row.get("last_seen")),
        _as_text(row.get("installation_date")),
        _as_text(row.get("customer_asset_id")),
        loaded_at,
        loaded_at,
        source_date,
    )


def _query_dicts(
    connection: sqlite3.Connection,
    sql: str,
    params: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, tuple(params))]


def _validate_source(source: str) -> str:
    source_mode = source.strip().lower()
    if source_mode not in VALID_OBSERVATION_SOURCES:
        allowed = ", ".join(sorted(VALID_OBSERVATION_SOURCES))
        raise ValueError(f"source must be one of: {allowed}")
    return source_mode


def _validate_raw_retention(raw_retention: str) -> str:
    retention_mode = raw_retention.strip().lower()
    if retention_mode not in VALID_RAW_RETENTION_MODES:
        allowed = ", ".join(sorted(VALID_RAW_RETENTION_MODES))
        raise ValueError(f"raw_retention must be one of: {allowed}")
    return retention_mode


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _as_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.asset_tree import (
    asset_tree_records_from_payload,
    normalize_asset_tree_records,
)
from insy_sensor_data.waites.client import ENDPOINT_FILENAMES
from insy_sensor_data.waites.validate import ensure_waites_raw_valid


OBSERVATION_SCHEMA_VERSION = 5
VALID_OBSERVATION_SOURCES = {"mock", "api"}
VALID_RAW_RETENTION_MODES = {"release", "compress", "keep"}

DAILY_SNAPSHOT_INTERNAL_FIELDS = {
    "source",
    "source_date",
    "built_at",
    "snapshot_csv_path",
    "snapshot_json",
}

SNAPSHOT_TEXT_FIELDS = {
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "facility_id",
    "customer_asset_id",
    "installation_customer_asset_id",
    "equipment_customer_asset_id",
}

WAITES_DATE_SCOPED_RELEASE_TABLES = [
    "waites_equipment",
    "waites_installation_points",
    "waites_rms_observations",
    "waites_temperature_observations",
    "waites_impact_observations",
    "waites_action_items",
]

WAITES_NATIVE_TABLES = [
    "waites_rms_observations",
    "waites_temperature_observations",
    "waites_impact_observations",
    "waites_action_items",
]

WAITES_TABLES = [
    "waites_loads",
    "waites_equipment",
    "waites_installation_points",
    "waites_rms_observations",
    "waites_temperature_observations",
    "waites_impact_observations",
    "waites_action_items",
    "waites_daily_metric_rollups",
]


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
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS observation_schema (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS waites_loads (
            source_date TEXT NOT NULL,
            source TEXT NOT NULL,
            facility_id INTEGER NOT NULL,
            manifest_path TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            loaded_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            equipment_count INTEGER NOT NULL,
            installation_point_count INTEGER NOT NULL,
            rms_count INTEGER NOT NULL,
            impact_count INTEGER NOT NULL,
            temperature_count INTEGER NOT NULL,
            action_item_count INTEGER NOT NULL,
            rollup_count INTEGER NOT NULL,
            PRIMARY KEY (source, source_date, facility_id)
        );

        CREATE TABLE IF NOT EXISTS waites_equipment (
            source_date TEXT NOT NULL,
            equipment_id INTEGER NOT NULL,
            asset_tree_id INTEGER,
            name TEXT,
            facility_id INTEGER,
            customer_asset_id TEXT,
            PRIMARY KEY (source_date, equipment_id)
        );

        CREATE TABLE IF NOT EXISTS waites_installation_points (
            source_date TEXT NOT NULL,
            installation_point_id INTEGER NOT NULL,
            name TEXT,
            equipment_id INTEGER,
            sensor_id INTEGER,
            facility_id INTEGER,
            last_seen TEXT,
            installation_date TEXT,
            customer_asset_id TEXT,
            PRIMARY KEY (source_date, installation_point_id)
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

        CREATE TABLE IF NOT EXISTS waites_rms_observations (
            source_date TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            installation_point_id INTEGER NOT NULL,
            axis TEXT NOT NULL,
            facility_id INTEGER,
            acceleration REAL,
            velocity REAL,
            pk_pk REAL,
            cf REAL,
            PRIMARY KEY (source_date, source_row_number)
        );

        CREATE TABLE IF NOT EXISTS waites_temperature_observations (
            source_date TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            installation_point_id INTEGER NOT NULL,
            facility_id INTEGER,
            value REAL,
            ambient REAL,
            PRIMARY KEY (source_date, source_row_number)
        );

        CREATE TABLE IF NOT EXISTS waites_impact_observations (
            source_date TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            installation_point_id INTEGER NOT NULL,
            axis TEXT NOT NULL,
            facility_id INTEGER,
            impact_vue_acceleration REAL,
            impact_vue_pk_pk REAL,
            PRIMARY KEY (source_date, source_row_number)
        );

        CREATE TABLE IF NOT EXISTS waites_action_items (
            source_date TEXT NOT NULL,
            action_item_id TEXT NOT NULL,
            wo_number TEXT,
            wo_status TEXT,
            sensor_id INTEGER,
            type TEXT,
            status TEXT,
            installation_point_id INTEGER,
            equipment_id INTEGER,
            title TEXT,
            description TEXT,
            urgency TEXT,
            closed_at TEXT,
            facility_id INTEGER,
            raw_json TEXT,
            PRIMARY KEY (source_date, action_item_id)
        );

        CREATE TABLE IF NOT EXISTS waites_daily_metric_rollups (
            source_date TEXT NOT NULL,
            installation_point_id INTEGER NOT NULL,
            equipment_id INTEGER,
            axis TEXT NOT NULL,
            metric TEXT NOT NULL,
            source_table TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            min_value REAL,
            max_value REAL,
            mean_value REAL,
            first_timestamp TEXT,
            last_timestamp TEXT,
            PRIMARY KEY (source_date, installation_point_id, axis, metric)
        );

        CREATE TABLE IF NOT EXISTS sensor_daily_snapshots (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            installation_point_id TEXT NOT NULL,
            built_at TEXT NOT NULL,
            snapshot_csv_path TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            PRIMARY KEY (source, source_date, installation_point_id)
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
            UNIQUE(source, source_date, feature_space, k, algorithm, random_seed, feature_policy_version)
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
            PRIMARY KEY(model_run_id, installation_point_id),
            FOREIGN KEY(model_run_id) REFERENCES cluster_model_runs(model_run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cluster_model_centroids (
            model_run_id TEXT NOT NULL,
            cluster INTEGER NOT NULL,
            sensor_count INTEGER NOT NULL,
            centroid_json TEXT NOT NULL,
            pca_x REAL,
            pca_y REAL,
            summary_json TEXT,
            PRIMARY KEY(model_run_id, cluster),
            FOREIGN KEY(model_run_id) REFERENCES cluster_model_runs(model_run_id) ON DELETE CASCADE
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
            FOREIGN KEY(from_model_run_id) REFERENCES cluster_model_runs(model_run_id),
            FOREIGN KEY(to_model_run_id) REFERENCES cluster_model_runs(model_run_id)
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
            PRIMARY KEY(drift_run_id, installation_point_id),
            FOREIGN KEY(drift_run_id) REFERENCES cluster_drift_runs(drift_run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cluster_centroid_alignment (
            drift_run_id TEXT NOT NULL,
            from_cluster INTEGER NOT NULL,
            to_cluster INTEGER,
            distance REAL,
            mapping_confidence TEXT,
            PRIMARY KEY(drift_run_id, from_cluster),
            FOREIGN KEY(drift_run_id) REFERENCES cluster_drift_runs(drift_run_id) ON DELETE CASCADE
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

        CREATE INDEX IF NOT EXISTS idx_waites_loads_source_date
            ON waites_loads (source, source_date);

        CREATE INDEX IF NOT EXISTS idx_waites_equipment_asset
            ON waites_equipment (source_date, customer_asset_id);

        CREATE INDEX IF NOT EXISTS idx_waites_installation_equipment
            ON waites_installation_points (source_date, equipment_id);

        CREATE INDEX IF NOT EXISTS idx_waites_installation_asset
            ON waites_installation_points (source_date, customer_asset_id);

        CREATE INDEX IF NOT EXISTS idx_waites_equipment_reference_asset
            ON waites_equipment_reference (source, customer_asset_id);

        CREATE INDEX IF NOT EXISTS idx_waites_asset_tree_reference_parent
            ON waites_asset_tree_reference (source, parent_asset_tree_id);

        CREATE INDEX IF NOT EXISTS idx_waites_installation_reference_equipment
            ON waites_installation_point_reference (source, equipment_id);

        CREATE INDEX IF NOT EXISTS idx_waites_installation_reference_asset
            ON waites_installation_point_reference (source, customer_asset_id);

        CREATE INDEX IF NOT EXISTS idx_waites_rms_sensor_time
            ON waites_rms_observations (installation_point_id, axis, timestamp);

        CREATE INDEX IF NOT EXISTS idx_waites_rms_date_axis
            ON waites_rms_observations (source_date, axis, timestamp);

        CREATE INDEX IF NOT EXISTS idx_waites_temperature_sensor_time
            ON waites_temperature_observations (installation_point_id, timestamp);

        CREATE INDEX IF NOT EXISTS idx_waites_temperature_date_time
            ON waites_temperature_observations (source_date, timestamp);

        CREATE INDEX IF NOT EXISTS idx_waites_impact_sensor_time
            ON waites_impact_observations (installation_point_id, axis, timestamp);

        CREATE INDEX IF NOT EXISTS idx_waites_impact_date_axis
            ON waites_impact_observations (source_date, axis, timestamp);

        CREATE INDEX IF NOT EXISTS idx_waites_action_items_sensor
            ON waites_action_items (source_date, installation_point_id, equipment_id);

        CREATE INDEX IF NOT EXISTS idx_waites_daily_metric_lookup
            ON waites_daily_metric_rollups (source_date, metric, equipment_id, installation_point_id);

        CREATE INDEX IF NOT EXISTS idx_sensor_daily_snapshots_source_date
            ON sensor_daily_snapshots (source, source_date);

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
    connection.execute(
        """
        INSERT OR IGNORE INTO observation_schema (version, applied_at)
        VALUES (?, ?)
        """,
        (OBSERVATION_SCHEMA_VERSION, _utc_now()),
    )
    connection.commit()


def load_waites_observations(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
    replace: bool = True,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    storage = get_storage_paths(settings.data_dir)
    raw_dir = storage.raw_waites_run_dir(run_date.isoformat())
    if not raw_dir.exists():
        raise FileNotFoundError(f"Missing raw Waites run directory: {raw_dir}")

    validation_report = ensure_waites_raw_valid(settings=settings, run_date=run_date, source=source_mode)
    manifest_path = raw_dir / "manifest.json"
    manifest = read_json(manifest_path)
    facility_id = _as_int(manifest.get("facility_id")) or settings.waites_facility_id

    payloads = {
        "asset-tree": asset_tree_records_from_payload(read_json(raw_dir / "asset-tree.json")),
        "equipment": read_json(raw_dir / "equipment.json")["list"],
        "installation-points": read_json(raw_dir / "installation-points.json")["list"],
        "readings-rms": read_json(raw_dir / "readings-rms.json")["list"],
        "readings-impact-vue": read_json(raw_dir / "readings-impact-vue.json")["list"],
        "readings-temperature": read_json(raw_dir / "readings-temperature.json")["list"],
        "action-items": read_json(raw_dir / "action-items.json")["list"],
    }
    loaded_at = _utc_now()
    source_date = run_date.isoformat()

    with connect_observation_store(settings) as connection:
        existing_loads = _get_loads_for_date(connection, source_date=source_date)
        if existing_loads and not replace:
            raise ValueError(
                f"Waites observations already loaded for date {source_date}; use replace."
            )

        _delete_waites_date(connection, source_date)
        row_counts = _insert_waites_payloads(
            connection,
            source=source_mode,
            source_date=source_date,
            loaded_at=loaded_at,
            payloads=payloads,
        )
        rollup_count = _rebuild_daily_rollups(connection, source_date)
        connection.execute(
            """
            INSERT INTO waites_loads (
                source_date,
                source,
                facility_id,
                manifest_path,
                manifest_sha256,
                loaded_at,
                schema_version,
                equipment_count,
                installation_point_count,
                rms_count,
                impact_count,
                temperature_count,
                action_item_count,
                rollup_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_date,
                source_mode,
                facility_id,
                manifest_path.as_posix(),
                _sha256(manifest_path),
                loaded_at,
                OBSERVATION_SCHEMA_VERSION,
                row_counts["equipment"],
                row_counts["installation_points"],
                row_counts["rms"],
                row_counts["impact"],
                row_counts["temperature"],
                row_counts["action_items"],
                rollup_count,
            ),
        )
        connection.commit()

    return {
        "source": source_mode,
        "date": source_date,
        "facility_id": facility_id,
        "database_path": observation_db_path(settings).as_posix(),
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "loaded_at": loaded_at,
        "manifest_path": manifest_path.as_posix(),
        "manifest_sha256": _sha256(manifest_path),
        "validation_status": validation_report["status"],
        "replaced_existing": bool(existing_loads),
        "row_counts": row_counts,
        "rollup_count": rollup_count,
    }


def load_waites_snapshot_inputs(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    with connect_observation_store(settings) as connection:
        load_metadata = _require_load_metadata(connection, source_date=source_date, source=source_mode)
        return {
            "load": load_metadata,
            "equipment": _query_dicts(
                connection,
                """
                SELECT equipment_id, asset_tree_id, name, facility_id, customer_asset_id
                FROM waites_equipment
                WHERE source_date = ?
                ORDER BY equipment_id
                """,
                (source_date,),
            ),
            "installation_points": _query_dicts(
                connection,
                """
                SELECT
                    installation_point_id,
                    name,
                    equipment_id,
                    sensor_id,
                    facility_id,
                    last_seen,
                    installation_date,
                    customer_asset_id
                FROM waites_installation_points
                WHERE source_date = ?
                ORDER BY installation_point_id
                """,
                (source_date,),
            ),
            "rms": _query_rms_rows(connection, source_date),
            "impact": _query_impact_rows(connection, source_date),
            "temperature": _query_dicts(
                connection,
                """
                SELECT timestamp, installation_point_id, facility_id, value, ambient
                FROM waites_temperature_observations
                WHERE source_date = ?
                ORDER BY installation_point_id, timestamp, source_row_number
                """,
                (source_date,),
            ),
        }


def query_daily_metric_rollups(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
    metric: str | None = None,
    installation_point_id: int | None = None,
) -> list[dict[str, Any]]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    clauses = ["source_date = ?"]
    params: list[Any] = [source_date]
    if metric is not None:
        clauses.append("metric = ?")
        params.append(metric)
    if installation_point_id is not None:
        clauses.append("installation_point_id = ?")
        params.append(installation_point_id)

    with connect_observation_store(settings) as connection:
        _require_load_metadata(connection, source_date=source_date, source=source_mode)
        return _query_dicts(
            connection,
            f"""
            SELECT
                source_date,
                installation_point_id,
                equipment_id,
                axis,
                metric,
                source_table,
                sample_count,
                min_value,
                max_value,
                mean_value,
                first_timestamp,
                last_timestamp
            FROM waites_daily_metric_rollups
            WHERE {" AND ".join(clauses)}
            ORDER BY installation_point_id, metric, axis
            """,
            tuple(params),
        )


def store_sensor_daily_snapshots(
    settings: AppSettings,
    run_date: date,
    source: str,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    snapshot_csv_path: Path,
    built_at: str | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    stored_at = built_at or _utc_now()

    if "installation_point_id" not in fieldnames:
        raise ValueError("daily snapshot rows must include installation_point_id")

    with connect_observation_store(settings) as connection:
        _ensure_daily_snapshot_columns(connection, fieldnames)
        snapshot_columns = [
            field
            for field in fieldnames
            if field not in DAILY_SNAPSHOT_INTERNAL_FIELDS
        ]
        insert_columns = [
            "source",
            "source_date",
            "installation_point_id",
            "built_at",
            "snapshot_csv_path",
            "snapshot_json",
            *snapshot_columns,
        ]
        placeholders = ", ".join("?" for _field in insert_columns)
        quoted_columns = ", ".join(_quote_identifier(field) for field in insert_columns)

        connection.execute(
            """
            DELETE FROM sensor_daily_snapshots
            WHERE source = ? AND source_date = ?
            """,
            (source_mode, source_date),
        )
        for row in rows:
            installation_point_id = row.get("installation_point_id")
            if installation_point_id in (None, ""):
                raise ValueError("daily snapshot row is missing installation_point_id")
            values = [
                source_mode,
                source_date,
                str(installation_point_id),
                stored_at,
                snapshot_csv_path.as_posix(),
                json.dumps({field: row.get(field) for field in fieldnames}, sort_keys=True),
                *[_snapshot_column_value(field, row.get(field)) for field in snapshot_columns],
            ]
            connection.execute(
                f"""
                INSERT OR REPLACE INTO sensor_daily_snapshots ({quoted_columns})
                VALUES ({placeholders})
                """,
                values,
            )
        connection.commit()

    return {
        "source": source_mode,
        "date": source_date,
        "database_path": observation_db_path(settings).as_posix(),
        "table": "sensor_daily_snapshots",
        "row_count": len(rows),
        "stored_at": stored_at,
        "snapshot_csv_path": snapshot_csv_path.as_posix(),
    }


def load_sensor_daily_snapshots(
    settings: AppSettings,
    run_date: date,
    source: str = "mock",
) -> list[dict[str, Any]]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    with connect_observation_store(settings) as connection:
        rows = _query_dicts(
            connection,
            """
            SELECT *
            FROM sensor_daily_snapshots
            WHERE source = ? AND source_date = ?
            ORDER BY CAST(installation_point_id AS INTEGER), installation_point_id
            """,
            (source_mode, source_date),
        )

    if not rows:
        raise FileNotFoundError(
            f"Missing SQLite daily snapshots for source {source_mode} date {source_date}."
        )

    loaded: list[dict[str, Any]] = []
    for row in rows:
        snapshot_json = row.get("snapshot_json")
        if isinstance(snapshot_json, str) and snapshot_json:
            loaded.append(json.loads(snapshot_json))
            continue
        loaded.append(
            {
                field: value
                for field, value in row.items()
                if field not in DAILY_SNAPSHOT_INTERNAL_FIELDS
            }
        )
    return loaded


def verify_sensor_daily_snapshot(
    settings: AppSettings,
    run_date: date,
    source: str,
    expected_row_count: int | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    source_date = run_date.isoformat()
    with connect_observation_store(settings) as connection:
        row_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM sensor_daily_snapshots
                WHERE source = ? AND source_date = ?
                """,
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
    error_count = len(issues)
    return {
        "source": source_mode,
        "date": source_date,
        "database_path": observation_db_path(settings).as_posix(),
        "table": "sensor_daily_snapshots",
        "row_count": row_count,
        "expected_row_count": expected_row_count,
        "status": "invalid" if error_count else "valid",
        "error_count": error_count,
        "issues": issues,
    }


def record_waites_ingestion_ledger(
    settings: AppSettings,
    run_date: date,
    source: str,
    facility_id: int,
    manifest_path: Path,
    validation_report: dict[str, Any],
    snapshot_row_count: int,
    snapshot_store_status: str,
    raw_retention_mode: str = "keep",
    raw_retention_status: str = "kept",
    native_retention_status: str = "kept",
    snapshot_built_at: str | None = None,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    retention_mode = _validate_raw_retention(raw_retention_mode)
    source_date = run_date.isoformat()
    manifest = read_json(manifest_path)
    updated_at = _utc_now()
    endpoint_counts = _manifest_endpoint_counts(manifest)
    endpoint_artifacts = _manifest_endpoint_artifacts(manifest)

    with connect_observation_store(settings) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO waites_ingestion_ledger (
                source,
                source_date,
                facility_id,
                fetched_at,
                validated_at,
                snapshot_built_at,
                validation_status,
                validation_error_count,
                validation_warning_count,
                endpoint_counts_json,
                endpoint_artifacts_json,
                manifest_sha256,
                snapshot_row_count,
                snapshot_store_status,
                raw_retention_mode,
                raw_retention_status,
                native_retention_status,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_mode,
                source_date,
                facility_id,
                manifest.get("fetched_at"),
                validation_report.get("validated_at"),
                snapshot_built_at,
                validation_report.get("status"),
                _as_int(validation_report.get("error_count")) or 0,
                _as_int(validation_report.get("warning_count")) or 0,
                json.dumps(endpoint_counts, sort_keys=True),
                json.dumps(endpoint_artifacts, sort_keys=True),
                _sha256(manifest_path),
                snapshot_row_count,
                snapshot_store_status,
                retention_mode,
                raw_retention_status,
                native_retention_status,
                updated_at,
            ),
        )
        connection.commit()

    return {
        "source": source_mode,
        "date": source_date,
        "facility_id": facility_id,
        "database_path": observation_db_path(settings).as_posix(),
        "table": "waites_ingestion_ledger",
        "endpoint_counts": endpoint_counts,
        "manifest_sha256": _sha256(manifest_path),
        "snapshot_row_count": snapshot_row_count,
        "snapshot_store_status": snapshot_store_status,
        "raw_retention_mode": retention_mode,
        "raw_retention_status": raw_retention_status,
        "native_retention_status": native_retention_status,
        "updated_at": updated_at,
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
    storage = get_storage_paths(settings.data_dir)
    manifest_path = storage.raw_waites_run_dir(source_date) / "manifest.json"
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
            SET
                endpoint_artifacts_json = ?,
                manifest_sha256 = ?,
                raw_retention_mode = ?,
                raw_retention_status = ?,
                native_retention_status = ?,
                updated_at = ?
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
    source: str = "mock",
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


def purge_waites_native_observations(
    settings: AppSettings,
    source: str,
    run_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    dry_run: bool = True,
    confirm_delete: bool = False,
) -> dict[str, Any]:
    source_mode = _validate_source(source)
    dates = _selected_dates(run_date=run_date, start_date=start_date, end_date=end_date)
    if not dry_run and not confirm_delete:
        raise ValueError("native purge requires --confirm-delete when --delete is used")

    purged_dates: list[str] = []
    candidates: list[dict[str, Any]] = []
    total_rows_deleted = 0
    with connect_observation_store(settings) as connection:
        for selected_date in dates:
            source_date = selected_date.isoformat()
            candidate = _native_purge_candidate(connection, source_date=source_date, source=source_mode)
            candidates.append(candidate)
            if dry_run or not candidate["delete_ready"]:
                continue

            for table in WAITES_DATE_SCOPED_RELEASE_TABLES:
                deleted = connection.execute(
                    f"DELETE FROM {table} WHERE source_date = ?",
                    (source_date,),
                ).rowcount
                total_rows_deleted += int(deleted if deleted != -1 else 0)
            connection.execute(
                """
                DELETE FROM waites_daily_metric_rollups
                WHERE source_date = ?
                """,
                (source_date,),
            )
            connection.execute(
                """
                UPDATE waites_ingestion_ledger
                SET native_retention_status = ?, updated_at = ?
                WHERE source = ? AND source_date = ?
                """,
                ("purged", _utc_now(), source_mode, source_date),
            )
            purged_dates.append(source_date)
        if not dry_run:
            connection.commit()

    return {
        "source": source_mode,
        "dry_run": dry_run,
        "date_count": len(dates),
        "candidate_count": len(candidates),
        "rows_deleted": total_rows_deleted,
        "purged_dates": purged_dates,
        "candidates": candidates,
    }


def _insert_waites_payloads(
    connection: sqlite3.Connection,
    source: str,
    source_date: str,
    loaded_at: str,
    payloads: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    asset_tree_rows = [
        _asset_tree_reference_row(source, source_date, loaded_at, row)
        for row in normalize_asset_tree_records(payloads.get("asset-tree", []))
    ]
    equipment_rows = [_equipment_row(source_date, row) for row in payloads["equipment"]]
    equipment_reference_rows = [
        _equipment_reference_row(source, source_date, loaded_at, row)
        for row in payloads["equipment"]
    ]
    installation_rows = [
        _installation_point_row(source_date, row) for row in payloads["installation-points"]
    ]
    installation_reference_rows = [
        _installation_point_reference_row(source, source_date, loaded_at, row)
        for row in payloads["installation-points"]
    ]
    rms_rows = [
        _rms_row(source_date, row, index) for index, row in enumerate(payloads["readings-rms"])
    ]
    impact_rows = [
        _impact_row(source_date, row, index)
        for index, row in enumerate(payloads["readings-impact-vue"])
    ]
    temperature_rows = [
        _temperature_row(source_date, row, index)
        for index, row in enumerate(payloads["readings-temperature"])
    ]
    action_rows = [
        _action_item_row(source_date, row, index) for index, row in enumerate(payloads["action-items"])
    ]

    connection.executemany(
        """
        INSERT INTO waites_asset_tree_reference (
            source,
            asset_tree_id,
            name,
            parent_asset_tree_id,
            facility_id,
            asset_tree_path,
            first_loaded_at,
            last_loaded_at,
            last_source_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        INSERT OR REPLACE INTO waites_equipment (
            source_date, equipment_id, asset_tree_id, name, facility_id, customer_asset_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        equipment_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO waites_installation_points (
            source_date,
            installation_point_id,
            name,
            equipment_id,
            sensor_id,
            facility_id,
            last_seen,
            installation_date,
            customer_asset_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        installation_rows,
    )
    connection.executemany(
        """
        INSERT INTO waites_equipment_reference (
            source,
            equipment_id,
            asset_tree_id,
            name,
            facility_id,
            customer_asset_id,
            first_loaded_at,
            last_loaded_at,
            last_source_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, equipment_id) DO UPDATE SET
            asset_tree_id = excluded.asset_tree_id,
            name = excluded.name,
            facility_id = excluded.facility_id,
            customer_asset_id = excluded.customer_asset_id,
            last_loaded_at = excluded.last_loaded_at,
            last_source_date = excluded.last_source_date
        """,
        equipment_reference_rows,
    )
    connection.executemany(
        """
        INSERT INTO waites_installation_point_reference (
            source,
            installation_point_id,
            name,
            equipment_id,
            sensor_id,
            facility_id,
            last_seen,
            installation_date,
            customer_asset_id,
            first_loaded_at,
            last_loaded_at,
            last_source_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        installation_reference_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO waites_rms_observations (
            source_date,
            source_row_number,
            timestamp,
            installation_point_id,
            axis,
            facility_id,
            acceleration,
            velocity,
            pk_pk,
            cf
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rms_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO waites_impact_observations (
            source_date,
            source_row_number,
            timestamp,
            installation_point_id,
            axis,
            facility_id,
            impact_vue_acceleration,
            impact_vue_pk_pk
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        impact_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO waites_temperature_observations (
            source_date,
            source_row_number,
            timestamp,
            installation_point_id,
            facility_id,
            value,
            ambient
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        temperature_rows,
    )
    connection.executemany(
        """
        INSERT OR REPLACE INTO waites_action_items (
            source_date,
            action_item_id,
            wo_number,
            wo_status,
            sensor_id,
            type,
            status,
            installation_point_id,
            equipment_id,
            title,
            description,
            urgency,
            closed_at,
            facility_id,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        action_rows,
    )

    return {
        "asset_trees": len(asset_tree_rows),
        "equipment": len(equipment_rows),
        "installation_points": len(installation_rows),
        "rms": len(rms_rows),
        "impact": len(impact_rows),
        "temperature": len(temperature_rows),
        "action_items": len(action_rows),
    }


def _rebuild_daily_rollups(connection: sqlite3.Connection, source_date: str) -> int:
    connection.execute("DELETE FROM waites_daily_metric_rollups WHERE source_date = ?", (source_date,))
    equipment_by_point = {
        row["installation_point_id"]: row["equipment_id"]
        for row in connection.execute(
            """
            SELECT installation_point_id, equipment_id
            FROM waites_installation_points
            WHERE source_date = ?
            """,
            (source_date,),
        )
    }

    groups: dict[tuple[int, int | None, str, str, str], list[tuple[str, float]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT timestamp, installation_point_id, axis, acceleration, velocity, pk_pk, cf
        FROM waites_rms_observations
        WHERE source_date = ?
        """,
        (source_date,),
    ):
        _add_rollup_values(
            groups,
            row,
            equipment_by_point,
            "waites_rms_observations",
            {
                "rms.acceleration": row["acceleration"],
                "rms.velocity": row["velocity"],
                "rms.pk_pk": row["pk_pk"],
                "rms.cf": row["cf"],
            },
            axis=row["axis"],
        )

    for row in connection.execute(
        """
        SELECT timestamp, installation_point_id, value, ambient
        FROM waites_temperature_observations
        WHERE source_date = ?
        """,
        (source_date,),
    ):
        _add_rollup_values(
            groups,
            row,
            equipment_by_point,
            "waites_temperature_observations",
            {
                "temperature.value": row["value"],
                "temperature.ambient": row["ambient"],
            },
        )

    for row in connection.execute(
        """
        SELECT timestamp, installation_point_id, axis, impact_vue_acceleration, impact_vue_pk_pk
        FROM waites_impact_observations
        WHERE source_date = ?
        """,
        (source_date,),
    ):
        _add_rollup_values(
            groups,
            row,
            equipment_by_point,
            "waites_impact_observations",
            {
                "impact.impact_vue_acceleration": row["impact_vue_acceleration"],
                "impact.impact_vue_pk_pk": row["impact_vue_pk_pk"],
            },
            axis=row["axis"],
        )

    rollups = [_rollup_row(source_date, key, values) for key, values in groups.items()]
    connection.executemany(
        """
        INSERT OR REPLACE INTO waites_daily_metric_rollups (
            source_date,
            installation_point_id,
            equipment_id,
            axis,
            metric,
            source_table,
            sample_count,
            min_value,
            max_value,
            mean_value,
            first_timestamp,
            last_timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rollups,
    )
    return len(rollups)


def _add_rollup_values(
    groups: dict[tuple[int, int | None, str, str, str], list[tuple[str, float]]],
    row: sqlite3.Row,
    equipment_by_point: dict[int, int | None],
    source_table: str,
    values: dict[str, Any],
    axis: str | None = "",
) -> None:
    installation_point_id = row["installation_point_id"]
    for metric, raw_value in values.items():
        value = _as_float(raw_value)
        if value is None:
            continue
        key = (
            installation_point_id,
            equipment_by_point.get(installation_point_id),
            axis or "",
            metric,
            source_table,
        )
        groups[key].append((row["timestamp"], value))


def _rollup_row(
    source_date: str,
    key: tuple[int, int | None, str, str, str],
    values: list[tuple[str, float]],
) -> tuple[Any, ...]:
    installation_point_id, equipment_id, axis, metric, source_table = key
    numeric_values = [value for _timestamp, value in values]
    timestamps = [timestamp for timestamp, _value in values]
    return (
        source_date,
        installation_point_id,
        equipment_id,
        axis,
        metric,
        source_table,
        len(numeric_values),
        min(numeric_values),
        max(numeric_values),
        sum(numeric_values) / len(numeric_values),
        min(timestamps),
        max(timestamps),
    )


def _delete_waites_date(connection: sqlite3.Connection, source_date: str) -> None:
    for table in WAITES_TABLES:
        connection.execute(f"DELETE FROM {table} WHERE source_date = ?", (source_date,))


def _get_load_metadata(
    connection: sqlite3.Connection,
    source_date: str,
    source: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT *
        FROM waites_loads
        WHERE source_date = ? AND source = ?
        ORDER BY loaded_at DESC
        LIMIT 1
        """,
        (source_date, source),
    ).fetchone()
    return dict(row) if row is not None else None


def _get_loads_for_date(connection: sqlite3.Connection, source_date: str) -> list[dict[str, Any]]:
    return _query_dicts(
        connection,
        """
        SELECT *
        FROM waites_loads
        WHERE source_date = ?
        ORDER BY loaded_at DESC
        """,
        (source_date,),
    )


def _require_load_metadata(
    connection: sqlite3.Connection,
    source_date: str,
    source: str,
) -> dict[str, Any]:
    metadata = _get_load_metadata(connection, source_date=source_date, source=source)
    if metadata is None:
        raise FileNotFoundError(
            f"Missing Waites observation load for source {source} date {source_date}."
        )
    return metadata


def _query_rms_rows(connection: sqlite3.Connection, source_date: str) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": row["timestamp"],
            "installation_point_id": row["installation_point_id"],
            "axis": row["axis"],
            "facility_id": row["facility_id"],
            "acceleration": row["acceleration"],
            "velocity": row["velocity"],
            "pk-pk": row["pk_pk"],
            "cf": row["cf"],
        }
        for row in connection.execute(
            """
            SELECT timestamp, installation_point_id, axis, facility_id, acceleration, velocity, pk_pk, cf
            FROM waites_rms_observations
            WHERE source_date = ?
            ORDER BY installation_point_id, axis, timestamp, source_row_number
            """,
            (source_date,),
        )
    ]


def _query_impact_rows(connection: sqlite3.Connection, source_date: str) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": row["timestamp"],
            "installation_point_id": row["installation_point_id"],
            "axis": row["axis"],
            "facility_id": row["facility_id"],
            "impact_vue_acceleration": row["impact_vue_acceleration"],
            "impact_vue_pk_pk": row["impact_vue_pk_pk"],
        }
        for row in connection.execute(
            """
            SELECT
                timestamp,
                installation_point_id,
                axis,
                facility_id,
                impact_vue_acceleration,
                impact_vue_pk_pk
            FROM waites_impact_observations
            WHERE source_date = ?
            ORDER BY installation_point_id, axis, timestamp, source_row_number
            """,
            (source_date,),
        )
    ]


def _query_dicts(
    connection: sqlite3.Connection,
    sql: str,
    params: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, tuple(params))]


def _ensure_daily_snapshot_columns(connection: sqlite3.Connection, fieldnames: list[str]) -> None:
    existing = _daily_snapshot_columns(connection)
    for field in fieldnames:
        if field in DAILY_SNAPSHOT_INTERNAL_FIELDS or field in existing:
            continue
        connection.execute(
            f"""
            ALTER TABLE sensor_daily_snapshots
            ADD COLUMN {_quote_identifier(field)} {_snapshot_column_type(field)}
            """
        )
        existing.add(field)

    if "equipment_id" in existing:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_daily_snapshots_equipment
                ON sensor_daily_snapshots (source, source_date, equipment_id)
            """
        )
    if "customer_asset_id" in existing:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_daily_snapshots_asset
                ON sensor_daily_snapshots (source, source_date, customer_asset_id)
            """
        )


def _daily_snapshot_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(sensor_daily_snapshots)")
    }


def _snapshot_column_type(field: str) -> str:
    if field in SNAPSHOT_TEXT_FIELDS:
        return "TEXT"
    return "REAL"


def _snapshot_column_value(field: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if field in SNAPSHOT_TEXT_FIELDS:
        return str(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


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
        name = str(endpoint["name"])
        artifact = endpoint.get("artifact") if isinstance(endpoint.get("artifact"), dict) else {}
        artifacts[name] = {
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
        SELECT *
        FROM waites_ingestion_ledger
        WHERE source = ? AND source_date = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (source, source_date),
    ).fetchone()
    return dict(row) if row is not None else None


def _selected_dates(
    run_date: date | None,
    start_date: date | None,
    end_date: date | None,
) -> list[date]:
    if run_date is not None and (start_date is not None or end_date is not None):
        raise ValueError("use either --date or --start-date/--end-date, not both")
    if run_date is not None:
        return [run_date]
    if start_date is None or end_date is None:
        raise ValueError("native purge requires --date or both --start-date and --end-date")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _native_purge_candidate(
    connection: sqlite3.Connection,
    source_date: str,
    source: str,
) -> dict[str, Any]:
    ledger = _get_ledger_row(connection, source_date=source_date, source=source)
    snapshot_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM sensor_daily_snapshots
            WHERE source = ? AND source_date = ?
            """,
            (source, source_date),
        ).fetchone()[0]
    )
    date_scoped_counts = {
        table: int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source_date = ?",
                (source_date,),
            ).fetchone()[0]
        )
        for table in WAITES_DATE_SCOPED_RELEASE_TABLES
    }
    native_counts = {
        table: date_scoped_counts[table]
        for table in WAITES_NATIVE_TABLES
    }

    issues: list[dict[str, Any]] = []
    if ledger is None:
        issues.append({"code": "missing_ingestion_ledger"})
    else:
        if int(ledger.get("validation_error_count") or 0) > 0 or ledger.get("validation_status") == "invalid":
            issues.append({"code": "validation_failed"})
        if snapshot_count != int(ledger.get("snapshot_row_count") or 0):
            issues.append(
                {
                    "code": "snapshot_row_count_mismatch",
                    "ledger_row_count": ledger.get("snapshot_row_count"),
                    "snapshot_row_count": snapshot_count,
                }
            )
        if ledger.get("snapshot_store_status") != "stored":
            issues.append({"code": "snapshot_store_not_confirmed"})
    if snapshot_count == 0:
        issues.append({"code": "missing_daily_snapshot"})

    return {
        "source": source,
        "date": source_date,
        "delete_ready": not issues,
        "issues": issues,
        "snapshot_row_count": snapshot_count,
        "date_scoped_row_counts": date_scoped_counts,
        "date_scoped_row_count": sum(date_scoped_counts.values()),
        "native_row_counts": native_counts,
        "native_row_count": sum(date_scoped_counts.values()),
        "timestamp_native_row_count": sum(native_counts.values()),
        "ledger": {
            "facility_id": ledger.get("facility_id"),
            "validation_status": ledger.get("validation_status"),
            "snapshot_row_count": ledger.get("snapshot_row_count"),
            "raw_retention_mode": ledger.get("raw_retention_mode"),
            "raw_retention_status": ledger.get("raw_retention_status"),
            "native_retention_status": ledger.get("native_retention_status"),
        }
        if ledger is not None
        else None,
    }


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


def _equipment_row(source_date: str, row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        source_date,
        _as_int(row.get("equipment_id")),
        _as_int(row.get("asset_tree_id")),
        _as_text(row.get("name")),
        _as_int(row.get("facility_id")),
        _as_text(row.get("customer_asset_id")),
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


def _installation_point_row(source_date: str, row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        source_date,
        _as_int(row.get("installation_point_id")),
        _as_text(row.get("name")),
        _as_int(row.get("equipment_id")),
        _as_int(row.get("sensor_id")),
        _as_int(row.get("facility_id")),
        _as_text(row.get("last_seen")),
        _as_text(row.get("installation_date")),
        _as_text(row.get("customer_asset_id")),
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


def _rms_row(source_date: str, row: dict[str, Any], index: int) -> tuple[Any, ...]:
    return (
        source_date,
        index,
        _as_text(row.get("timestamp")),
        _as_int(row.get("installation_point_id")),
        _axis(row.get("axis")),
        _as_int(row.get("facility_id")),
        _as_float(row.get("acceleration")),
        _as_float(row.get("velocity")),
        _as_float(row.get("pk-pk")),
        _as_float(row.get("cf")),
    )


def _temperature_row(source_date: str, row: dict[str, Any], index: int) -> tuple[Any, ...]:
    return (
        source_date,
        index,
        _as_text(row.get("timestamp")),
        _as_int(row.get("installation_point_id")),
        _as_int(row.get("facility_id")),
        _as_float(row.get("value")),
        _as_float(row.get("ambient")),
    )


def _impact_row(source_date: str, row: dict[str, Any], index: int) -> tuple[Any, ...]:
    return (
        source_date,
        index,
        _as_text(row.get("timestamp")),
        _as_int(row.get("installation_point_id")),
        _axis(row.get("axis")),
        _as_int(row.get("facility_id")),
        _as_float(row.get("impact_vue_acceleration")),
        _as_float(row.get("impact_vue_pk_pk")),
    )


def _action_item_row(source_date: str, row: dict[str, Any], index: int) -> tuple[Any, ...]:
    installation_point = row.get("installation_point") if isinstance(row.get("installation_point"), dict) else {}
    equipment = row.get("equipment") if isinstance(row.get("equipment"), dict) else {}
    action_item_id = row.get("action_item_id") or f"row-{index}"
    return (
        source_date,
        str(action_item_id),
        _as_text(row.get("wo_number")),
        _as_text(row.get("wo_status")),
        _as_int(row.get("sensor_id")),
        _as_text(row.get("type") or row.get("action_item_type")),
        _as_text(row.get("status") or row.get("action_item_status")),
        _as_int(installation_point.get("installation_point_id")),
        _as_int(equipment.get("equipment_id")),
        _as_text(row.get("title")),
        _as_text(row.get("description")),
        _as_text(row.get("urgency")),
        _as_text(row.get("closed_at")),
        _as_int(row.get("facility_id")),
        json.dumps(row, sort_keys=True),
    )


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


def _axis(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).lower()


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


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

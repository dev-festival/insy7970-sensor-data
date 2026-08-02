from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.schema import (
    SNAPSHOT_FIELDS,
    snapshot_column_type,
    snapshot_column_value,
)
from insy_sensor_data.store.errors import StoreMigrationRequiredError


OPERATIONAL_SCHEMA_VERSION = 10
SNAPSHOT_MIGRATION_VERSION = OPERATIONAL_SCHEMA_VERSION
LEGACY_SNAPSHOT_TABLE = "sensor_daily_snapshots"
FIXED_SNAPSHOT_TABLE = "sensor_daily_facts"


def resolve_configured_source(settings: AppSettings, requested: str | None = None) -> str:
    selected = (requested or settings.source_mode).strip().lower()
    if selected != settings.source_mode:
        raise ValueError(
            f"This service instance is configured for source {settings.source_mode!r}, "
            f"not {selected!r}."
        )
    return selected


def initialize_operational_schema(connection: sqlite3.Connection) -> None:
    field_definitions = ",\n            ".join(
        f'"{field}" {snapshot_column_type(field)}'
        + (" NOT NULL" if field == "installation_point_id" else "")
        for field in SNAPSHOT_FIELDS
    )
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {FIXED_SNAPSHOT_TABLE} (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            built_at TEXT NOT NULL,
            snapshot_revision TEXT NOT NULL,
            {field_definitions},
            PRIMARY KEY (source, source_date, installation_point_id)
        );

        CREATE INDEX IF NOT EXISTS idx_sensor_daily_facts_source_date
            ON {FIXED_SNAPSHOT_TABLE} (source, source_date);
        CREATE INDEX IF NOT EXISTS idx_sensor_daily_facts_equipment_scope
            ON {FIXED_SNAPSHOT_TABLE} (source, equipment_id, source_date);
        CREATE INDEX IF NOT EXISTS idx_sensor_daily_facts_asset_scope
            ON {FIXED_SNAPSHOT_TABLE} (source, customer_asset_id, source_date);
        CREATE INDEX IF NOT EXISTS idx_sensor_daily_facts_installation_scope
            ON {FIXED_SNAPSHOT_TABLE} (source, installation_point_id, source_date);

        CREATE TABLE IF NOT EXISTS operational_store_state (
            state_id INTEGER PRIMARY KEY CHECK (state_id = 1),
            configured_source TEXT,
            snapshot_authority TEXT NOT NULL,
            migration_status TEXT NOT NULL,
            migration_version INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshot_migration_audit (
            source TEXT NOT NULL,
            migration_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            legacy_row_count INTEGER,
            fixed_row_count INTEGER,
            legacy_hash TEXT,
            fixed_hash TEXT,
            null_value_count INTEGER,
            zero_value_count INTEGER,
            database_bytes_before INTEGER,
            database_bytes_side_by_side INTEGER,
            error TEXT,
            PRIMARY KEY (source, migration_version)
        );

        CREATE TABLE IF NOT EXISTS snapshot_revisions (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            snapshot_revision TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            built_at TEXT NOT NULL,
            PRIMARY KEY (source, source_date)
        );

        CREATE TABLE IF NOT EXISTS ingestion_runs (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            facility_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            snapshot_revision TEXT,
            snapshot_row_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT,
            PRIMARY KEY (source, source_date, facility_id)
        );

        CREATE TABLE IF NOT EXISTS ingestion_run_transitions (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            facility_id INTEGER NOT NULL,
            state TEXT NOT NULL,
            transitioned_at TEXT NOT NULL,
            detail TEXT,
            PRIMARY KEY (source, source_date, facility_id, state, transitioned_at)
        );

        CREATE TABLE IF NOT EXISTS sync_control (
            source TEXT PRIMARY KEY,
            start_date TEXT NOT NULL,
            current_through TEXT,
            source_timezone TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_date_runs (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            facility_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            data_status TEXT NOT NULL,
            model_status TEXT NOT NULL,
            retention_status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT,
            PRIMARY KEY (source, source_date)
        );

        CREATE TABLE IF NOT EXISTS admin_writer_lease (
            lease_id INTEGER PRIMARY KEY CHECK (lease_id = 1),
            owner_token TEXT NOT NULL,
            operation TEXT NOT NULL,
            process_id INTEGER NOT NULL,
            host_name TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS admin_action_audit (
            action_id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            source TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            component TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            summary_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sync_date_runs_status
            ON sync_date_runs (source, status, source_date);
        CREATE INDEX IF NOT EXISTS idx_admin_action_audit_source
            ON admin_action_audit (source, started_at);
        """
    )
    _ensure_column(connection, "waites_ingestion_ledger", "ingestion_state", "TEXT")
    _ensure_column(connection, "waites_ingestion_ledger", "snapshot_revision", "TEXT")
    _ensure_column(connection, "cluster_model_runs", "model_policy_version", "TEXT")
    _ensure_column(connection, "cluster_model_runs", "input_snapshot_revision", "TEXT")
    _ensure_column(connection, "cluster_model_runs", "max_iterations", "INTEGER")
    _ensure_column(connection, "cluster_model_runs", "tolerance", "REAL")
    _ensure_column(connection, "cluster_model_runs", "pca_iterations", "INTEGER")
    _ensure_column(connection, "cluster_drift_runs", "model_policy_version", "TEXT")
    _ensure_column(connection, "cluster_drift_runs", "from_snapshot_revision", "TEXT")
    _ensure_column(connection, "cluster_drift_runs", "to_snapshot_revision", "TEXT")
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_cluster_model_runs_active
            ON cluster_model_runs (
                source, source_date, feature_space, model_policy_version, status
            );
        CREATE INDEX IF NOT EXISTS idx_cluster_drift_runs_active
            ON cluster_drift_runs (
                source, from_date, to_date, feature_space,
                model_policy_version, status
            );
        """
    )

    state = connection.execute(
        "SELECT 1 FROM operational_store_state WHERE state_id = 1"
    ).fetchone()
    if state is None:
        legacy_count = (
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {LEGACY_SNAPSHOT_TABLE}"
                ).fetchone()[0]
            )
            if _table_exists(connection, LEGACY_SNAPSHOT_TABLE)
            else 0
        )
        authority = LEGACY_SNAPSHOT_TABLE if legacy_count else FIXED_SNAPSHOT_TABLE
        status = "legacy" if legacy_count else "ready"
        connection.execute(
            """
            INSERT INTO operational_store_state (
                state_id, configured_source, snapshot_authority,
                migration_status, migration_version, updated_at
            )
            VALUES (1, NULL, ?, ?, ?, ?)
            """,
            (authority, status, SNAPSHOT_MIGRATION_VERSION, _utc_now()),
        )


def active_snapshot_table(connection: sqlite3.Connection) -> str:
    if not _table_exists(connection, "operational_store_state"):
        return FIXED_SNAPSHOT_TABLE
    row = connection.execute(
        """
        SELECT snapshot_authority
        FROM operational_store_state
        WHERE state_id = 1
        """
    ).fetchone()
    if row is None:
        return FIXED_SNAPSHOT_TABLE
    authority = str(row[0])
    if authority != FIXED_SNAPSHOT_TABLE:
        raise StoreMigrationRequiredError(
            "The operational store still selects the retired snapshot representation. "
            "Run the 0.6.6 retirement dry run and migration rehearsal before serving it."
        )
    return FIXED_SNAPSHOT_TABLE


def configured_source(connection: sqlite3.Connection) -> str | None:
    if not _table_exists(connection, "operational_store_state"):
        return None
    row = connection.execute(
        "SELECT configured_source FROM operational_store_state WHERE state_id = 1"
    ).fetchone()
    return str(row[0]) if row is not None and row[0] not in (None, "") else None


def claim_configured_source(connection: sqlite3.Connection, source: str) -> None:
    current = configured_source(connection)
    if current is not None and current != source:
        raise StoreMigrationRequiredError(
            f"This data directory is configured for source {current!r}, not {source!r}. "
            "Use a separate data directory for each service source."
        )
    if current is None:
        connection.execute(
            """
            UPDATE operational_store_state
            SET configured_source = ?, updated_at = ?
            WHERE state_id = 1
            """,
            (source, _utc_now()),
        )


def validate_service_source(connection: sqlite3.Connection, source: str) -> None:
    if not _table_exists(connection, "operational_store_state"):
        return
    row = connection.execute(
        """
        SELECT configured_source, migration_status
        FROM operational_store_state
        WHERE state_id = 1
        """
    ).fetchone()
    if row is None:
        return
    migration_status = str(row[1])
    if migration_status in {"started", "failed"}:
        raise StoreMigrationRequiredError(
            f"Operational store migration is {migration_status}; service startup is blocked."
        )
    current = row[0]
    if current not in (None, "", source):
        raise StoreMigrationRequiredError(
            f"This data directory is configured for source {current!r}, but the service "
            f"requested {source!r}. Use a separate data directory."
        )


def audit_legacy_snapshot_parity(
    connection: sqlite3.Connection,
    source: str,
) -> dict[str, Any]:
    """Compare the retired JSON rows with fixed facts without changing authority."""
    if not _table_exists(connection, LEGACY_SNAPSHOT_TABLE):
        fixed_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {FIXED_SNAPSHOT_TABLE} WHERE source = ?",
                (source,),
            ).fetchone()[0]
        )
        return {
            "status": "absent",
            "source": source,
            "legacy_row_count": 0,
            "fixed_row_count": fixed_count,
            "legacy_hash": None,
            "fixed_hash": None,
            "null_value_count": None,
            "zero_value_count": None,
        }

    dates = [
        str(row[0])
        for row in connection.execute(
            f"SELECT DISTINCT source_date FROM {LEGACY_SNAPSHOT_TABLE} "
            "WHERE source = ? ORDER BY source_date",
            (source,),
        ).fetchall()
    ]
    legacy_digest = hashlib.sha256()
    fixed_digest = hashlib.sha256()
    legacy_count = 0
    fixed_count = 0
    legacy_nulls = 0
    legacy_zeros = 0
    fixed_nulls = 0
    fixed_zeros = 0
    for source_date in dates:
        legacy_rows = _load_legacy_rows(connection, source, source_date)
        fixed_rows = _load_fixed_rows(connection, source, source_date)
        nulls, zeros = _update_snapshot_digest(
            legacy_digest,
            legacy_rows,
            include_built_at=False,
        )
        legacy_count += len(legacy_rows)
        legacy_nulls += nulls
        legacy_zeros += zeros
        nulls, zeros = _update_snapshot_digest(
            fixed_digest,
            fixed_rows,
            include_built_at=False,
        )
        fixed_count += len(fixed_rows)
        fixed_nulls += nulls
        fixed_zeros += zeros
    legacy_hash = legacy_digest.hexdigest()
    fixed_hash = fixed_digest.hexdigest()
    ready = (
        legacy_count == fixed_count
        and legacy_hash == fixed_hash
        and (legacy_nulls, legacy_zeros) == (fixed_nulls, fixed_zeros)
    )
    return {
        "status": "ready" if ready else "mismatch",
        "source": source,
        "date_count": len(dates),
        "legacy_row_count": legacy_count,
        "fixed_row_count": fixed_count,
        "legacy_hash": legacy_hash,
        "fixed_hash": fixed_hash,
        "null_value_count": legacy_nulls,
        "zero_value_count": legacy_zeros,
    }


def snapshot_revision(source: str, source_date: str, rows: Iterable[dict[str, Any]]) -> str:
    digest, _nulls, _zeros = snapshot_rows_hash(
        [{"source_date": source_date, "built_at": "", **row} for row in rows],
        include_built_at=False,
    )
    return f"{source}:{source_date}:{digest[:20]}"


def snapshot_rows_hash(
    rows: Iterable[dict[str, Any]],
    *,
    include_built_at: bool = True,
) -> tuple[str, int, int]:
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("source_date", "")),
            str(row.get("installation_point_id", "")),
        ),
    )
    digest = hashlib.sha256()
    null_count, zero_count = _update_snapshot_digest(
        digest,
        ordered,
        include_built_at=include_built_at,
    )
    return digest.hexdigest(), null_count, zero_count


def _update_snapshot_digest(
    digest: Any,
    rows: Iterable[dict[str, Any]],
    *,
    include_built_at: bool,
) -> tuple[int, int]:
    null_count = 0
    zero_count = 0
    for original in rows:
        item: dict[str, Any] = {"source_date": str(original.get("source_date", ""))}
        if include_built_at:
            item["built_at"] = str(original.get("built_at", ""))
        for field in SNAPSHOT_FIELDS:
            value = snapshot_column_value(field, original.get(field))
            item[field] = value
            null_count += int(value is None)
            zero_count += int(isinstance(value, (int, float)) and value == 0)
        payload = json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return null_count, zero_count


def _replace_fixed_rows(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_date: str,
    built_at: str,
    revision: str,
    rows: list[dict[str, Any]],
) -> None:
    columns = ["source", "source_date", "built_at", "snapshot_revision", *SNAPSHOT_FIELDS]
    quoted = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _column in columns)
    connection.executemany(
        f"INSERT INTO {FIXED_SNAPSHOT_TABLE} ({quoted}) VALUES ({placeholders})",
        (
            (
                source,
                source_date,
                built_at,
                revision,
                *[snapshot_column_value(field, row.get(field)) for field in SNAPSHOT_FIELDS],
            )
            for row in rows
        ),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO snapshot_revisions (
            source, source_date, snapshot_revision, row_count, built_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (source, source_date, revision, len(rows), built_at),
    )


def _load_legacy_rows(
    connection: sqlite3.Connection,
    source: str,
    source_date: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["source = ?"]
    params: list[Any] = [source]
    if source_date is not None:
        clauses.append("source_date = ?")
        params.append(source_date)
    rows = connection.execute(
        f"""
        SELECT * FROM {LEGACY_SNAPSHOT_TABLE}
        WHERE {' AND '.join(clauses)}
        ORDER BY source_date, installation_point_id
        """,
        tuple(params),
    ).fetchall()
    loaded: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        payload = json.loads(row["snapshot_json"]) if row.get("snapshot_json") else row
        loaded.append(
            {
                "source_date": str(row["source_date"]),
                "built_at": str(row["built_at"]),
                **{field: snapshot_column_value(field, payload.get(field)) for field in SNAPSHOT_FIELDS},
            }
        )
    return loaded


def _load_fixed_rows(
    connection: sqlite3.Connection,
    source: str,
    source_date: str | None = None,
) -> list[dict[str, Any]]:
    projection = ", ".join(f'"{field}"' for field in SNAPSHOT_FIELDS)
    clauses = ["source = ?"]
    params: list[Any] = [source]
    if source_date is not None:
        clauses.append("source_date = ?")
        params.append(source_date)
    return [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT source_date, built_at, {projection}
            FROM {FIXED_SNAPSHOT_TABLE}
            WHERE {' AND '.join(clauses)}
            ORDER BY source_date, installation_point_id
            """,
            tuple(params),
        ).fetchall()
    ]


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if not _table_exists(connection, table):
        return
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN "{column}" {definition}')


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

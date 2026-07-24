from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.waites.validate import ensure_waites_raw_valid


OBSERVATION_SCHEMA_VERSION = 1
VALID_OBSERVATION_SOURCES = {"mock", "api"}

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

        CREATE INDEX IF NOT EXISTS idx_waites_loads_source_date
            ON waites_loads (source, source_date);

        CREATE INDEX IF NOT EXISTS idx_waites_equipment_asset
            ON waites_equipment (source_date, customer_asset_id);

        CREATE INDEX IF NOT EXISTS idx_waites_installation_equipment
            ON waites_installation_points (source_date, equipment_id);

        CREATE INDEX IF NOT EXISTS idx_waites_installation_asset
            ON waites_installation_points (source_date, customer_asset_id);

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
        row_counts = _insert_waites_payloads(connection, source_date, payloads)
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


def _insert_waites_payloads(
    connection: sqlite3.Connection,
    source_date: str,
    payloads: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    equipment_rows = [_equipment_row(source_date, row) for row in payloads["equipment"]]
    installation_rows = [
        _installation_point_row(source_date, row) for row in payloads["installation-points"]
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


def _equipment_row(source_date: str, row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        source_date,
        _as_int(row.get("equipment_id")),
        _as_int(row.get("asset_tree_id")),
        _as_text(row.get("name")),
        _as_int(row.get("facility_id")),
        _as_text(row.get("customer_asset_id")),
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

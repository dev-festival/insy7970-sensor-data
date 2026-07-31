from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import sqlite3

from insy_sensor_data.config import AppSettings
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.store.errors import (
    StoreCorruptError,
    StoreMigrationRequiredError,
    StoreNotFoundError,
    StoreUnavailableError,
)
from insy_sensor_data.store.schema import validate_service_source


def store_path(settings: AppSettings) -> Path:
    """Return the configured operational SQLite path without creating it."""
    return get_storage_paths(settings.data_dir).observations_db_path


@contextmanager
def read_store(
    settings: AppSettings,
    *,
    required_tables: tuple[str, ...] = (),
) -> Iterator[sqlite3.Connection]:
    """Open the operational store read-only and translate SQLite failures."""
    path = store_path(settings)
    if not path.is_file():
        raise StoreNotFoundError(f"Operational SQLite store is missing: {path.as_posix()}")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        validate_service_source(connection, settings.source_mode)
        if required_tables:
            _require_tables(connection, required_tables)
        yield connection
    except StoreMigrationRequiredError:
        raise
    except sqlite3.DatabaseError as exc:
        message = str(exc)
        if "malformed" in message.lower() or "not a database" in message.lower():
            raise StoreCorruptError(
                f"Operational SQLite store is corrupt: {message}"
            ) from exc
        raise StoreUnavailableError(
            f"Operational SQLite read failed: {message}"
        ) from exc
    except sqlite3.Error as exc:
        raise StoreUnavailableError(
            f"Operational SQLite read failed: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'observation_schema'
        """
    ).fetchone()
    if table is None:
        raise StoreMigrationRequiredError(
            "Operational SQLite migration required; missing table: "
            "observation_schema"
        )
    row = connection.execute(
        "SELECT MAX(version) AS version FROM observation_schema"
    ).fetchone()
    if row is None or row["version"] is None:
        raise StoreMigrationRequiredError(
            "Operational SQLite migration required; observation_schema "
            "has no applied version."
        )
    return int(row["version"])


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    _validate_identifier(table)
    return [
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]


def _require_tables(
    connection: sqlite3.Connection,
    tables: tuple[str, ...],
) -> None:
    placeholders = ", ".join("?" for _table in tables)
    existing = {
        str(row["name"])
        for row in connection.execute(
            f"""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name IN ({placeholders})
            """,
            tables,
        ).fetchall()
    }
    missing = sorted(set(tables) - existing)
    if missing:
        raise StoreMigrationRequiredError(
            "Operational SQLite migration required; missing tables: "
            + ", ".join(missing)
        )


def _validate_identifier(identifier: str) -> None:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"Unsupported SQLite identifier: {identifier}")

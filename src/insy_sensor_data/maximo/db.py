from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from insy_sensor_data.config import AppSettings
from insy_sensor_data.maximo.queries import render_workorder_query


class MaximoDatabaseError(RuntimeError):
    """Raised when the live DB2/ODBC provider cannot return a query result."""


def build_connection_string(settings: AppSettings) -> str:
    return f"DSN={settings.maximo_dsn}"


def fetch_asset_workorders(
    settings: AppSettings,
    assetnums: Sequence[str],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch a bounded set of read-only asset histories through the server DSN."""
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - exercised only on a live host
        raise MaximoDatabaseError("pyodbc is required for live Maximo history") from exc

    assets = tuple(assetnums)
    if not assets:
        return []
    sql = render_workorder_query(settings.maximo_schema, len(assets))
    end_exclusive = end_date + timedelta(days=1)
    params = (
        settings.maximo_site_id,
        *assets,
        start_date,
        end_exclusive,
        settings.maximo_site_id,
        *assets,
        start_date,
        end_exclusive,
    )
    connection = None
    try:
        connection = pyodbc.connect(
            build_connection_string(settings),
        )
        cursor = connection.cursor()
        _set_statement_timeout(cursor, settings.maximo_query_timeout_seconds, pyodbc.Error)
        cursor.execute(sql, params)
        columns = [column[0].lower() for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except pyodbc.Error as exc:  # pragma: no cover - requires a DB2/ODBC installation
        raise MaximoDatabaseError(f"Maximo DB2 query failed: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _set_statement_timeout(cursor: Any, timeout_seconds: int, driver_error: type[Exception]) -> None:
    """Apply a best-effort statement timeout without rejecting limited ODBC drivers.

    Some IBM CLI configurations reject connection-level timeout attributes with
    ``CLI0150E``. A cursor timeout, when supported, scopes the setting to this query;
    unsupported drivers still execute the bounded read-only query.
    """
    try:
        cursor.timeout = timeout_seconds
    except (AttributeError, driver_error):
        return

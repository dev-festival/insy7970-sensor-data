from __future__ import annotations

from datetime import date
from typing import Any
import sqlite3

from insy_sensor_data.store.connection import schema_version


def data_revision(
    connection: sqlite3.Connection,
    source: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Describe the durable SQLite state used by an operational response."""
    clauses = ["source = ?"]
    params: list[Any] = [source]
    if start_date is not None:
        clauses.append("source_date >= ?")
        params.append(start_date.isoformat())
    if end_date is not None:
        clauses.append("source_date <= ?")
        params.append(end_date.isoformat())
    where = " AND ".join(clauses)
    ledger = connection.execute(
        f"""
        SELECT
            COALESCE(SUM(snapshot_row_count), 0) AS row_count,
            COUNT(*) AS date_count,
            MIN(source_date) AS first_date,
            MAX(source_date) AS last_date,
            MAX(snapshot_built_at) AS snapshot_built_at,
            MAX(updated_at) AS ingestion_completed_at
        FROM waites_ingestion_ledger
        WHERE {where}
        """,
        tuple(params),
    ).fetchone()
    return {
        "store": "sqlite",
        "schema_version": schema_version(connection),
        "source": source,
        "row_count": int(ledger["row_count"] or 0),
        "date_count": int(ledger["date_count"] or 0),
        "first_date": ledger["first_date"],
        "last_date": ledger["last_date"],
        "snapshot_built_at": ledger["snapshot_built_at"],
        "ingestion_completed_at": ledger["ingestion_completed_at"],
    }

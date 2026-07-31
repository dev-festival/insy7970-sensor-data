from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable
import gzip
import hashlib
import json
import sqlite3

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.revision import data_revision
from insy_sensor_data.store.schema import resolve_configured_source


EVENT_PROVIDER = "waites"
EVENT_COVERAGE_STATES = {"imported", "genuinely_empty", "refetch_required"}


def initialize_event_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS waites_events (
            source TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_event_id TEXT NOT NULL,
            provider_created_at TEXT,
            provider_updated_at TEXT,
            first_seen_date TEXT NOT NULL,
            last_seen_date TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            wo_number TEXT,
            wo_status TEXT,
            sensor_id TEXT,
            event_type TEXT,
            status TEXT,
            installation_point_id TEXT,
            equipment_id TEXT,
            customer_asset_id TEXT,
            title TEXT,
            description TEXT,
            urgency TEXT,
            closed_at TEXT,
            facility_id TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (source, provider, provider_event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_waites_events_equipment_scope
            ON waites_events (
                source,
                equipment_id,
                first_seen_date,
                last_seen_date
            );

        CREATE INDEX IF NOT EXISTS idx_waites_events_installation_scope
            ON waites_events (
                source,
                installation_point_id,
                first_seen_date,
                last_seen_date
            );

        CREATE INDEX IF NOT EXISTS idx_waites_events_asset_scope
            ON waites_events (
                source,
                customer_asset_id,
                first_seen_date,
                last_seen_date
            );

        CREATE INDEX IF NOT EXISTS idx_waites_events_status
            ON waites_events (source, status, last_seen_date);

        CREATE TABLE IF NOT EXISTS waites_event_coverage (
            source TEXT NOT NULL,
            source_date TEXT NOT NULL,
            state TEXT NOT NULL,
            input_mode TEXT,
            event_observation_count INTEGER NOT NULL,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (source, source_date)
        );

        CREATE INDEX IF NOT EXISTS idx_waites_event_coverage_state
            ON waites_event_coverage (source, state, source_date);
        """
    )


def upsert_waites_events(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_date: str,
    observed_at: str,
    rows: Iterable[dict[str, Any]],
) -> int:
    selected_source = _validate_source(source)
    records = [
        _event_record(
            connection,
            selected_source,
            source_date,
            observed_at,
            row,
        )
        for row in rows
    ]
    connection.executemany(
        """
        INSERT INTO waites_events (
            source,
            provider,
            provider_event_id,
            provider_created_at,
            provider_updated_at,
            first_seen_date,
            last_seen_date,
            first_seen_at,
            last_seen_at,
            wo_number,
            wo_status,
            sensor_id,
            event_type,
            status,
            installation_point_id,
            equipment_id,
            customer_asset_id,
            title,
            description,
            urgency,
            closed_at,
            facility_id,
            raw_json
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(source, provider, provider_event_id) DO UPDATE SET
            provider_created_at = COALESCE(
                waites_events.provider_created_at,
                excluded.provider_created_at
            ),
            provider_updated_at = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(
                    excluded.provider_updated_at,
                    waites_events.provider_updated_at
                )
                ELSE waites_events.provider_updated_at
            END,
            first_seen_date = MIN(
                waites_events.first_seen_date,
                excluded.first_seen_date
            ),
            last_seen_date = MAX(waites_events.last_seen_date, excluded.last_seen_date),
            last_seen_at = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN excluded.last_seen_at
                ELSE waites_events.last_seen_at
            END,
            wo_number = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.wo_number, waites_events.wo_number)
                ELSE waites_events.wo_number
            END,
            wo_status = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.wo_status, waites_events.wo_status)
                ELSE waites_events.wo_status
            END,
            sensor_id = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.sensor_id, waites_events.sensor_id)
                ELSE waites_events.sensor_id
            END,
            event_type = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.event_type, waites_events.event_type)
                ELSE waites_events.event_type
            END,
            status = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.status, waites_events.status)
                ELSE waites_events.status
            END,
            installation_point_id = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(
                    excluded.installation_point_id,
                    waites_events.installation_point_id
                )
                ELSE waites_events.installation_point_id
            END,
            equipment_id = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.equipment_id, waites_events.equipment_id)
                ELSE waites_events.equipment_id
            END,
            customer_asset_id = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(
                    excluded.customer_asset_id,
                    waites_events.customer_asset_id
                )
                ELSE waites_events.customer_asset_id
            END,
            title = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.title, waites_events.title)
                ELSE waites_events.title
            END,
            description = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.description, waites_events.description)
                ELSE waites_events.description
            END,
            urgency = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.urgency, waites_events.urgency)
                ELSE waites_events.urgency
            END,
            closed_at = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.closed_at, waites_events.closed_at)
                ELSE waites_events.closed_at
            END,
            facility_id = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN COALESCE(excluded.facility_id, waites_events.facility_id)
                ELSE waites_events.facility_id
            END,
            raw_json = CASE
                WHEN excluded.last_seen_date >= waites_events.last_seen_date
                THEN excluded.raw_json
                ELSE waites_events.raw_json
            END
        """,
        records,
    )
    return len(records)


def record_waites_event_coverage(
    connection: sqlite3.Connection,
    *,
    source: str,
    source_date: str,
    state: str,
    input_mode: str | None,
    event_observation_count: int,
    checked_at: str,
) -> None:
    selected_source = _validate_source(source)
    if state not in EVENT_COVERAGE_STATES:
        allowed = ", ".join(sorted(EVENT_COVERAGE_STATES))
        raise ValueError(f"event coverage state must be one of: {allowed}")
    if event_observation_count < 0:
        raise ValueError("event_observation_count must not be negative")
    connection.execute(
        """
        INSERT INTO waites_event_coverage (
            source,
            source_date,
            state,
            input_mode,
            event_observation_count,
            checked_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_date) DO UPDATE SET
            state = excluded.state,
            input_mode = excluded.input_mode,
            event_observation_count = excluded.event_observation_count,
            checked_at = excluded.checked_at
        """,
        (
            selected_source,
            source_date,
            state,
            input_mode,
            event_observation_count,
            checked_at,
        ),
    )


def query_waites_events(
    settings: AppSettings,
    *,
    source: str,
    start_date: date,
    end_date: date,
    equipment_ids: set[str] | None = None,
    installation_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    selected_source = resolve_configured_source(settings, source)
    clauses = [
        "source = ?",
        "first_seen_date <= ?",
        "last_seen_date >= ?",
    ]
    params: list[Any] = [
        selected_source,
        end_date.isoformat(),
        start_date.isoformat(),
    ]
    scope_clauses: list[str] = []
    if equipment_ids is not None:
        _append_set_filter(
            scope_clauses,
            params,
            "equipment_id",
            equipment_ids,
        )
    if installation_point_ids is not None:
        _append_set_filter(
            scope_clauses,
            params,
            "installation_point_id",
            installation_point_ids,
        )
    if scope_clauses:
        clauses.append(f"({' OR '.join(scope_clauses)})")
    with read_store(
        settings,
        required_tables=(
            "waites_events",
            "waites_event_coverage",
            "waites_ingestion_ledger",
        ),
    ) as connection:
        rows = connection.execute(
            f"""
            SELECT
                provider_event_id,
                provider_created_at,
                provider_updated_at,
                first_seen_date,
                last_seen_date,
                wo_number,
                wo_status,
                sensor_id,
                event_type,
                status,
                installation_point_id,
                equipment_id,
                customer_asset_id,
                title,
                description,
                urgency,
                closed_at
            FROM waites_events
            WHERE {" AND ".join(clauses)}
            ORDER BY last_seen_date DESC, provider_event_id
            """,
            tuple(params),
        ).fetchall()
        coverage_rows = connection.execute(
            """
            SELECT
                ledger.source_date,
                COALESCE(coverage.state, 'migration_required') AS state,
                coverage.input_mode,
                COALESCE(coverage.event_observation_count, 0)
                    AS event_observation_count,
                coverage.checked_at
            FROM waites_ingestion_ledger AS ledger
            LEFT JOIN waites_event_coverage AS coverage
              ON coverage.source = ledger.source
             AND coverage.source_date = ledger.source_date
            WHERE ledger.source = ?
              AND ledger.source_date >= ?
              AND ledger.source_date <= ?
            ORDER BY ledger.source_date
            """,
            (
                selected_source,
                start_date.isoformat(),
                end_date.isoformat(),
            ),
        ).fetchall()
        revision = data_revision(
            connection,
            selected_source,
            start_date=start_date,
            end_date=end_date,
        )
    output = [
        {
            "date": row["last_seen_date"],
            "source": EVENT_PROVIDER,
            "provider": EVENT_PROVIDER,
            "event_id": row["provider_event_id"],
            "provider_created_at": row["provider_created_at"],
            "provider_updated_at": row["provider_updated_at"],
            "first_seen_date": row["first_seen_date"],
            "last_seen_date": row["last_seen_date"],
            "work_order": row["wo_number"] or "",
            "work_order_status": row["wo_status"] or "",
            "sensor_id": row["sensor_id"] or "",
            "type": row["event_type"] or "",
            "status": row["status"] or "",
            "installation_point_id": row["installation_point_id"] or "",
            "equipment_id": row["equipment_id"] or "",
            "asset_number": row["customer_asset_id"] or "",
            "title": row["title"] or f"Action item {row['provider_event_id']}",
            "description": row["description"] or "",
            "urgency": row["urgency"] or "",
            "closed_at": row["closed_at"] or "",
        }
        for row in rows
    ]
    coverage_dates = [dict(row) for row in coverage_rows]
    incomplete_dates = [
        str(row["source_date"])
        for row in coverage_rows
        if row["state"] not in {"imported", "genuinely_empty"}
    ]
    return {
        "source": selected_source,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "status": "partial" if incomplete_dates else "available",
        "row_count": len(output),
        "rows": output,
        "coverage": {
            "status": "partial" if incomplete_dates else "complete",
            "date_count": len(coverage_dates),
            "incomplete_dates": incomplete_dates,
            "dates": coverage_dates,
        },
        "data_revision": revision,
    }


def backfill_waites_events(
    settings: AppSettings,
    *,
    source: str,
) -> dict[str, Any]:
    """Import retained action items and report dates needing a narrow re-fetch."""
    selected_source = _validate_source(source)
    from insy_sensor_data.observations import connect_observation_store

    storage = get_storage_paths(settings.data_dir)
    results: list[dict[str, Any]] = []
    imported_observation_count = 0
    with connect_observation_store(settings) as connection:
        ledger_rows = connection.execute(
            """
            SELECT source_date, updated_at, endpoint_counts_json
            FROM waites_ingestion_ledger
            WHERE source = ?
            ORDER BY source_date
            """,
            (selected_source,),
        ).fetchall()
        for ledger in ledger_rows:
            source_date = str(ledger["source_date"])
            observed_at = str(ledger["updated_at"])
            endpoint_counts = _json_object(ledger["endpoint_counts_json"])
            raw_path = _retained_action_path(storage.raw_waites_run_dir(source_date))
            if raw_path is not None:
                rows = _read_action_rows(raw_path)
                upsert_waites_events(
                    connection,
                    source=selected_source,
                    source_date=source_date,
                    observed_at=observed_at,
                    rows=rows,
                )
                state = "imported" if rows else "genuinely_empty"
                input_mode = "retained_raw"
            else:
                legacy_rows = _legacy_action_rows(
                    connection,
                    selected_source,
                    source_date,
                )
                if legacy_rows:
                    upsert_waites_events(
                        connection,
                        source=selected_source,
                        source_date=source_date,
                        observed_at=observed_at,
                        rows=legacy_rows,
                    )
                    state = "imported"
                    input_mode = "retained_sqlite"
                    rows = legacy_rows
                elif int(endpoint_counts.get("action-items") or 0) == 0:
                    state = "genuinely_empty"
                    input_mode = None
                    rows = []
                else:
                    state = "refetch_required"
                    input_mode = None
                    rows = []
            imported_observation_count += len(rows)
            record_waites_event_coverage(
                connection,
                source=selected_source,
                source_date=source_date,
                state=state,
                input_mode=input_mode,
                event_observation_count=len(rows),
                checked_at=observed_at,
            )
            results.append(
                {
                    "date": source_date,
                    "state": state,
                    "input": input_mode,
                    "event_count": len(rows),
                }
            )
        durable_event_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM waites_events
                WHERE source = ? AND provider = ?
                """,
                (selected_source, EVENT_PROVIDER),
            ).fetchone()[0]
        )
        connection.commit()
    return {
        "source": selected_source,
        "date_count": len(results),
        "imported_event_count": durable_event_count,
        "imported_observation_count": imported_observation_count,
        "imported_dates": [
            row["date"] for row in results if row["state"] == "imported"
        ],
        "refetch_required_dates": [
            row["date"] for row in results if row["state"] == "refetch_required"
        ],
        "genuinely_empty_dates": [
            row["date"] for row in results if row["state"] == "genuinely_empty"
        ],
        "dates": results,
    }


def _event_record(
    connection: sqlite3.Connection,
    source: str,
    source_date: str,
    observed_at: str,
    row: dict[str, Any],
) -> tuple[Any, ...]:
    installation = (
        row.get("installation_point")
        if isinstance(row.get("installation_point"), dict)
        else {}
    )
    equipment = row.get("equipment") if isinstance(row.get("equipment"), dict) else {}
    installation_id = _optional_text(
        row.get("installation_point_id")
        or installation.get("installation_point_id")
    )
    equipment_id = _optional_text(
        row.get("equipment_id") or equipment.get("equipment_id")
    )
    sensor_id = _optional_text(row.get("sensor_id") or installation.get("sensor_id"))
    customer_asset_id = _optional_text(
        row.get("customer_asset_id")
        or equipment.get("customer_asset_id")
        or installation.get("customer_asset_id")
    )
    if installation_id and (not equipment_id or not customer_asset_id):
        reference = connection.execute(
            """
            SELECT equipment_id, customer_asset_id
            FROM waites_installation_point_reference
            WHERE source = ? AND CAST(installation_point_id AS TEXT) = ?
            """,
            (source, installation_id),
        ).fetchone()
        if reference is not None:
            equipment_id = equipment_id or _optional_text(reference["equipment_id"])
            customer_asset_id = customer_asset_id or _optional_text(
                reference["customer_asset_id"]
            )
    if equipment_id and not customer_asset_id:
        reference = connection.execute(
            """
            SELECT customer_asset_id
            FROM waites_equipment_reference
            WHERE source = ? AND CAST(equipment_id AS TEXT) = ?
            """,
            (source, equipment_id),
        ).fetchone()
        if reference is not None:
            customer_asset_id = _optional_text(reference["customer_asset_id"])
    raw_json = json.dumps(row, sort_keys=True)
    event_id = _provider_event_id(row)
    return (
        source,
        EVENT_PROVIDER,
        event_id,
        _optional_text(row.get("created_at") or row.get("opened_at")),
        _optional_text(row.get("updated_at")),
        source_date,
        source_date,
        observed_at,
        observed_at,
        _optional_text(row.get("wo_number")),
        _optional_text(row.get("wo_status")),
        sensor_id,
        _optional_text(row.get("type") or row.get("action_item_type")),
        _optional_text(row.get("status") or row.get("action_item_status")),
        installation_id,
        equipment_id,
        customer_asset_id,
        _optional_text(row.get("title")),
        _optional_text(row.get("description")),
        _optional_text(row.get("urgency")),
        _optional_text(row.get("closed_at")),
        _optional_text(row.get("facility_id")),
        raw_json,
    )


def _provider_event_id(row: dict[str, Any]) -> str:
    explicit = _optional_text(row.get("action_item_id") or row.get("id"))
    if explicit:
        return explicit
    installation = (
        row.get("installation_point")
        if isinstance(row.get("installation_point"), dict)
        else {}
    )
    stable_identity = {
        "wo_number": row.get("wo_number"),
        "sensor_id": row.get("sensor_id") or installation.get("sensor_id"),
        "installation_point_id": (
            row.get("installation_point_id")
            or installation.get("installation_point_id")
        ),
        "event_type": row.get("type") or row.get("action_item_type"),
        "provider_created_at": row.get("created_at") or row.get("opened_at"),
    }
    if not any(
        stable_identity[field]
        for field in (
            "wo_number",
            "sensor_id",
            "installation_point_id",
            "provider_created_at",
        )
    ):
        stable_identity["title_fallback"] = row.get("title")
    digest = hashlib.sha256(
        json.dumps(stable_identity, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"derived-v1-{digest[:24]}"


def _append_set_filter(
    clauses: list[str],
    params: list[Any],
    field: str,
    values: set[str],
) -> None:
    selected = sorted(value for value in values if value)
    if not selected:
        clauses.append("1 = 0")
        return
    placeholders = ", ".join("?" for _value in selected)
    clauses.append(f"{field} IN ({placeholders})")
    params.extend(selected)


def _retained_action_path(run_dir: Path) -> Path | None:
    for name in ("action-items.json", "action-items.json.gz"):
        path = run_dir / name
        if path.is_file():
            return path
    return None


def _read_action_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as source:
            payload = json.load(source)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("list", [])
    return [row for row in rows if isinstance(row, dict)]


def _legacy_action_rows(
    connection: sqlite3.Connection,
    source: str,
    source_date: str,
) -> list[dict[str, Any]]:
    recorded_sources = {
        str(row["source"])
        for row in connection.execute(
            """
            SELECT DISTINCT source
            FROM waites_loads
            WHERE source_date = ?
            """,
            (source_date,),
        ).fetchall()
    }
    if recorded_sources != {source}:
        return []
    rows = connection.execute(
        """
        SELECT
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
        FROM waites_action_items
        WHERE source_date = ?
        ORDER BY action_item_id
        """,
        (source_date,),
    ).fetchall()
    output = []
    for row in rows:
        raw = _json_object(row["raw_json"])
        output.append({**dict(row), **raw})
    return output


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_source(source: str) -> str:
    selected = source.strip().lower()
    if selected not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise ValueError(f"source must be one of: {allowed}")
    return selected


def _optional_text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)

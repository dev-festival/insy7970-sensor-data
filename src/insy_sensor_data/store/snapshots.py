from __future__ import annotations

from datetime import date, timedelta
from math import isfinite
from typing import Any, Iterable
import sqlite3

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.snapshots.schema import SNAPSHOT_FIELDS
from insy_sensor_data.store.connection import read_store, table_columns
from insy_sensor_data.store.errors import (
    StoreMigrationRequiredError,
    StoreNotFoundError,
)
from insy_sensor_data.store.revision import data_revision
from insy_sensor_data.store.schema import active_snapshot_table, resolve_configured_source


SNAPSHOT_INTERNAL_FIELDS = {
    "source",
    "source_date",
    "built_at",
    "snapshot_csv_path",
    "snapshot_json",
}
SNAPSHOT_IDENTIFIER_FIELDS = [
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
]
DEFAULT_TREND_VALUE_FIELDS = [
    "rms_vel_min_x",
    "rms_vel_mean_x",
    "rms_vel_max_x",
]


def snapshot_catalog(
    settings: AppSettings,
    *,
    source: str | None = None,
) -> list[dict[str, Any]]:
    source_mode = resolve_configured_source(settings, source)
    clauses: list[str] = []
    params: list[Any] = []
    clauses.append("source = ?")
    params.append(source_mode)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_store(settings) as connection:
        table = active_snapshot_table(connection)
        rows = connection.execute(
            f"""
            SELECT
                source,
                source_date AS date,
                COUNT(*) AS record_count,
                MAX(built_at) AS built_at
            FROM {table}
            {where}
            GROUP BY source, source_date
            ORDER BY source, source_date
            """,
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def load_snapshot_view(
    settings: AppSettings,
    *,
    run_date: date,
    source: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    customer_asset_id: str | None = None,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    resolved_source = resolve_configured_source(settings, source)
    with read_store(
        settings,
        required_tables=("waites_ingestion_ledger",),
    ) as connection:
        table = active_snapshot_table(connection)
        selected_fields = _available_snapshot_fields(
            connection,
            table,
            fields or SNAPSHOT_FIELDS,
        )
        clauses = ["source = ?", "source_date = ?"]
        params: list[Any] = [resolved_source, run_date.isoformat()]
        total_row = connection.execute(
            f"""
            SELECT COUNT(*) AS row_count, MAX(built_at) AS built_at
            FROM {table}
            WHERE source = ? AND source_date = ?
            """,
            tuple(params),
        ).fetchone()
        total_count = int(total_row["row_count"] or 0)
        if not total_count:
            raise StoreNotFoundError(
                f"Snapshot is unavailable for source {resolved_source} "
                f"date {run_date.isoformat()}."
            )
        _append_scalar_filter(clauses, params, "equipment_id", equipment_id)
        _append_scalar_filter(
            clauses,
            params,
            "installation_point_id",
            installation_point_id,
        )
        _append_scalar_filter(
            clauses,
            params,
            "customer_asset_id",
            customer_asset_id,
        )
        rows = _query_rows(connection, table, selected_fields, clauses, params)
        revision = data_revision(
            connection,
            resolved_source,
            start_date=run_date,
            end_date=run_date,
        )
    metadata = {
        "source": resolved_source,
        "date": run_date.isoformat(),
        "built_at": total_row["built_at"],
        "record_count": total_count,
        "input_mode": "sqlite",
        "served_from": "sqlite",
        "data_revision": revision,
    }
    return {
        "source": resolved_source,
        "date": run_date.isoformat(),
        "metadata": metadata,
        "data_revision": revision,
        "row_count": total_count,
        "filtered_row_count": len(rows),
        "filters": {
            "source": source,
            "equipment_id": equipment_id,
            "installation_point_id": installation_point_id,
            "customer_asset_id": customer_asset_id,
        },
        "rows": rows,
    }


def query_trend_rows(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    source: str,
    value_fields: Iterable[str] | None = None,
    equipment_ids: Iterable[str] | None = None,
    installation_point_ids: Iterable[str] | None = None,
    sensor_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    resolved_source = resolve_configured_source(settings, source)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    selected_value_fields = list(
        dict.fromkeys(value_fields or DEFAULT_TREND_VALUE_FIELDS)
    )
    with read_store(
        settings,
        required_tables=("waites_ingestion_ledger",),
    ) as connection:
        table = active_snapshot_table(connection)
        available = set(table_columns(connection, table))
        _require_columns(
            available,
            [*SNAPSHOT_IDENTIFIER_FIELDS, *selected_value_fields],
        )
        clauses = [
            "source = ?",
            "source_date >= ?",
            "source_date <= ?",
        ]
        params: list[Any] = [
            resolved_source,
            start_date.isoformat(),
            end_date.isoformat(),
        ]
        _append_id_filter(clauses, params, "equipment_id", equipment_ids)
        _append_id_filter(
            clauses,
            params,
            "installation_point_id",
            installation_point_ids,
        )
        _append_scalar_filter(clauses, params, "sensor_id", sensor_id)
        _append_scalar_filter(
            clauses,
            params,
            "customer_asset_id",
            customer_asset_id,
        )
        available_dates = {
            str(row["source_date"])
            for row in connection.execute(
                f"""
                SELECT DISTINCT source_date
                FROM {table}
                WHERE source = ? AND source_date >= ? AND source_date <= ?
                ORDER BY source_date
                """,
                (
                    resolved_source,
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            ).fetchall()
        }
        if not available_dates:
            raise StoreNotFoundError(
                f"Snapshots are unavailable for source {resolved_source} from "
                f"{start_date.isoformat()} to {end_date.isoformat()}."
            )
        fields = [*SNAPSHOT_IDENTIFIER_FIELDS, *selected_value_fields]
        rows = _query_rows(
            connection,
            table,
            fields,
            clauses,
            params,
            include_date=True,
        )
        revision = data_revision(
            connection,
            resolved_source,
            start_date=start_date,
            end_date=end_date,
        )
    skipped_dates = [
        selected_date.isoformat()
        for selected_date in _date_range(start_date, end_date)
        if selected_date.isoformat() not in available_dates
    ]
    return {
        "source": resolved_source,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows": rows,
        "skipped_dates": skipped_dates,
        "selected_fields": fields,
        "data_revision": revision,
    }


def query_trend_product(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    source: str,
    value_field: str,
    detail_fields: Iterable[str],
    detail_limit: int,
    detail_offset: int,
    aggregate_series: bool,
    range_fields: tuple[str, str] | None = None,
    equipment_ids: Iterable[str] | None = None,
    installation_point_ids: Iterable[str] | None = None,
    sensor_id: str | None = None,
    customer_asset_id: str | None = None,
) -> dict[str, Any]:
    """Return SQL summaries plus the bounded detail slice used by Trend."""
    resolved_source = resolve_configured_source(settings, source)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    selected_detail_fields = list(dict.fromkeys(detail_fields))
    selected_range_fields = list(dict.fromkeys(range_fields or ()))
    with read_store(
        settings,
        required_tables=("waites_ingestion_ledger",),
    ) as connection:
        table = active_snapshot_table(connection)
        available = set(table_columns(connection, table))
        _require_columns(
            available,
            [
                *SNAPSHOT_IDENTIFIER_FIELDS,
                value_field,
                *selected_detail_fields,
                *selected_range_fields,
            ],
        )
        clauses = [
            "source = ?",
            "source_date >= ?",
            "source_date <= ?",
        ]
        params: list[Any] = [
            resolved_source,
            start_date.isoformat(),
            end_date.isoformat(),
        ]
        _append_id_filter(clauses, params, "equipment_id", equipment_ids)
        _append_id_filter(
            clauses,
            params,
            "installation_point_id",
            installation_point_ids,
        )
        _append_scalar_filter(clauses, params, "sensor_id", sensor_id)
        _append_scalar_filter(
            clauses,
            params,
            "customer_asset_id",
            customer_asset_id,
        )
        where = " AND ".join(clauses)
        available_dates = {
            str(row["source_date"])
            for row in connection.execute(
                f"""
                SELECT DISTINCT source_date
                FROM {table}
                WHERE source = ? AND source_date >= ? AND source_date <= ?
                ORDER BY source_date
                """,
                (
                    resolved_source,
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            ).fetchall()
        }
        if not available_dates:
            raise StoreNotFoundError(
                f"Snapshots are unavailable for source {resolved_source} from "
                f"{start_date.isoformat()} to {end_date.isoformat()}."
            )
        range_projection = ""
        if len(selected_range_fields) == 2:
            range_projection = (
                f', "{selected_range_fields[0]}" AS range_min'
                f', "{selected_range_fields[1]}" AS range_max'
            )
        summary_rows = connection.execute(
            f"""
                SELECT
                source_date AS date,
                installation_point_id,
                installation_point_name,
                sensor_id,
                equipment_id,
                "{value_field}" AS value
                {range_projection}
            FROM {table}
            WHERE {where}
            ORDER BY
                source_date,
                CAST(installation_point_id AS INTEGER),
                installation_point_id
            """,
            tuple(params),
        )
        coverage_groups: dict[str, dict[str, Any]] = {}
        equipment_records: set[tuple[str, str]] = set()
        equipment_values: dict[tuple[str, str], list[float | int]] = {}
        series_rows: list[dict[str, Any]] = []
        scoped_dates: set[str] = set()
        sensor_count = 0
        observed_count = 0
        for row in summary_rows:
            raw_date = str(row["date"] or "")
            installation_id = str(row["installation_point_id"] or "")
            raw_sensor_id = str(row["sensor_id"] or "")
            equipment_id = str(row["equipment_id"] or "")
            sensor_key = installation_id or raw_sensor_id or "unknown"
            group = coverage_groups.setdefault(
                sensor_key,
                {
                    "installation_point_id": installation_id,
                    "sensor_name": (
                        row["installation_point_name"]
                        or raw_sensor_id
                        or sensor_key
                    ),
                    "expected_value_count": 0,
                    "observed_value_count": 0,
                    "missing_dates": [],
                },
            )
            group["expected_value_count"] += 1
            sensor_count += 1
            if raw_date:
                scoped_dates.add(raw_date)
                equipment_records.add((raw_date, equipment_id))
            value = _finite_value(row["value"])
            if value is None:
                if raw_date:
                    group["missing_dates"].append(raw_date)
            else:
                group["observed_value_count"] += 1
                observed_count += 1
            if aggregate_series:
                if raw_date and value is not None:
                    aggregate = equipment_values.setdefault(
                        (raw_date, equipment_id or "__unassigned__"),
                        [0.0, 0],
                    )
                    aggregate[0] += value
                    aggregate[1] += 1
            else:
                series_rows.append(
                    {
                        "date": raw_date,
                        "installation_point_id": installation_id,
                        "installation_point_name": row[
                            "installation_point_name"
                        ],
                        "sensor_id": raw_sensor_id,
                        "range_min": row["range_min"] if len(selected_range_fields) == 2 else None,
                        "range_max": row["range_max"] if len(selected_range_fields) == 2 else None,
                        value_field: row["value"],
                    }
                )
        if aggregate_series:
            date_values: dict[str, list[float]] = {}
            for (raw_date, _equipment_id), (total, count) in equipment_values.items():
                date_values.setdefault(raw_date, []).append(total / count)
            series_rows = [
                {
                    "date": raw_date,
                    value_field: (
                        sum(date_values[raw_date]) / len(date_values[raw_date])
                        if raw_date in date_values
                        else None
                    ),
                    "range_min": (
                        min(date_values[raw_date])
                        if raw_date in date_values
                        else None
                    ),
                    "range_max": (
                        max(date_values[raw_date])
                        if raw_date in date_values
                        else None
                    ),
                }
                for raw_date in sorted(scoped_dates)
            ]
        projection = ", ".join(
            f'"{field}"' for field in selected_detail_fields
        )
        detail_rows = connection.execute(
            f"""
            SELECT source_date AS date, {projection}
            FROM {table}
            WHERE {where}
            ORDER BY
                source_date,
                CAST(installation_point_id AS INTEGER),
                installation_point_id
            LIMIT ? OFFSET ?
            """,
            (*params, detail_limit, detail_offset),
        ).fetchall()
        revision = data_revision(
            connection,
            resolved_source,
            start_date=start_date,
            end_date=end_date,
        )
    coverage = {
        "value_field": value_field,
        "expected_value_count": sensor_count,
        "observed_value_count": observed_count,
        "coverage_percent": _coverage_percent(observed_count, sensor_count),
        "sensors": [
            {
                "installation_point_id": group["installation_point_id"],
                "sensor_name": group["sensor_name"],
                "expected_value_count": group["expected_value_count"],
                "observed_value_count": group["observed_value_count"],
                "coverage_percent": _coverage_percent(
                    group["observed_value_count"],
                    group["expected_value_count"],
                ),
                "missing_dates": sorted(group["missing_dates"]),
            }
            for _sensor_key, group in sorted(coverage_groups.items())
        ],
    }
    skipped_dates = [
        selected_date.isoformat()
        for selected_date in _date_range(start_date, end_date)
        if selected_date.isoformat() not in available_dates
    ]
    return {
        "source": resolved_source,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "sensor_record_count": sensor_count,
        "equipment_record_count": len(equipment_records),
        "detail_rows": [dict(row) for row in detail_rows],
        "series_rows": series_rows,
        "coverage": coverage,
        "skipped_dates": skipped_dates,
        "selected_fields": selected_detail_fields,
        "data_revision": revision,
    }


def _query_rows(
    connection: sqlite3.Connection,
    table: str,
    fields: list[str],
    clauses: list[str],
    params: list[Any],
    *,
    include_date: bool = False,
) -> list[dict[str, Any]]:
    projection = [f'"{field}"' for field in fields]
    if include_date:
        projection.insert(0, "source_date AS date")
    rows = connection.execute(
        f"""
        SELECT {", ".join(projection)}
        FROM {table}
        WHERE {" AND ".join(clauses)}
        ORDER BY
            source_date,
            CAST(installation_point_id AS INTEGER),
            installation_point_id
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _available_snapshot_fields(
    connection: sqlite3.Connection,
    table: str,
    requested: Iterable[str],
) -> list[str]:
    available = set(table_columns(connection, table))
    fields = list(requested)
    _require_columns(available, fields)
    return fields


def _require_columns(available: set[str], required: Iterable[str]) -> None:
    missing = sorted(set(required) - available)
    if missing:
        raise StoreMigrationRequiredError(
            "Operational snapshot migration required; missing columns: "
            + ", ".join(missing)
        )


def _finite_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _coverage_percent(observed_count: int, expected_count: int) -> float:
    if not expected_count:
        return 0.0
    return round((observed_count / expected_count) * 100, 1)


def _append_scalar_filter(
    clauses: list[str],
    params: list[Any],
    field: str,
    value: str | None,
) -> None:
    if value not in (None, ""):
        clauses.append(f'"{field}" = ?')
        params.append(str(value))


def _append_id_filter(
    clauses: list[str],
    params: list[Any],
    field: str,
    values: Iterable[str] | None,
) -> None:
    if values is None:
        return
    selected = sorted({str(value) for value in values if str(value)})
    if not selected:
        clauses.append("1 = 0")
        return
    placeholders = ", ".join("?" for _value in selected)
    clauses.append(f'"{field}" IN ({placeholders})')
    params.extend(selected)


def _date_range(start_date: date, end_date: date) -> list[date]:
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


def _optional_source(source: str | None) -> str | None:
    if source in (None, ""):
        return None
    return _validate_source(str(source))


def _validate_source(source: str) -> str:
    selected = source.strip().lower()
    if selected not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise ValueError(f"source must be one of: {allowed}")
    return selected

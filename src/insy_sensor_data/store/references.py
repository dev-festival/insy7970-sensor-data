from __future__ import annotations

from datetime import date
from typing import Any
import sqlite3

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.store.connection import read_store
from insy_sensor_data.store.errors import StoreNotFoundError
from insy_sensor_data.store.revision import data_revision
from insy_sensor_data.store.schema import active_snapshot_table, resolve_configured_source


VALID_SCOPE_TYPES = {"all", "asset_tree", "equipment", "sensor"}
REFERENCE_TABLES = (
    "waites_asset_tree_reference",
    "waites_equipment_reference",
    "waites_installation_point_reference",
    "waites_ingestion_ledger",
)


def list_equipment(
    settings: AppSettings,
    *,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    resolved_source = _resolve_source(settings, source)
    _validate_range(start_date, end_date)
    with read_store(settings, required_tables=REFERENCE_TABLES) as connection:
        table = active_snapshot_table(connection)
        clauses, params = _snapshot_range_clauses(
            resolved_source,
            start_date,
            end_date,
        )
        rows = connection.execute(
            f"""
            SELECT
                source,
                COALESCE(equipment_id, '') AS equipment_id,
                MAX(COALESCE(equipment_name, '')) AS equipment_name,
                MAX(COALESCE(customer_asset_id, '')) AS customer_asset_id,
                COUNT(DISTINCT installation_point_id) AS sensor_count,
                GROUP_CONCAT(DISTINCT installation_point_id) AS installation_point_ids,
                GROUP_CONCAT(DISTINCT source_date) AS dates
            FROM {table}
            WHERE {" AND ".join(clauses)}
            GROUP BY source, COALESCE(equipment_id, '')
            ORDER BY
                CASE WHEN COALESCE(equipment_id, '') GLOB '[0-9]*'
                    THEN CAST(equipment_id AS INTEGER) END,
                equipment_id
            """,
            tuple(params),
        ).fetchall()
        revision = data_revision(
            connection,
            resolved_source,
            start_date=start_date,
            end_date=end_date,
        )
    output = []
    for row in rows:
        dates = _sorted_csv(row["dates"])
        installation_ids = sorted(
            _csv_values(row["installation_point_ids"]),
            key=_sort_key,
        )
        output.append(
            {
                "source": row["source"],
                "equipment_id": _text(row["equipment_id"]),
                "equipment_name": _text(row["equipment_name"]),
                "customer_asset_id": _text(row["customer_asset_id"]),
                "sensor_count": int(row["sensor_count"] or 0),
                "installation_point_ids": installation_ids,
                "dates": dates,
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "date_count": len(dates),
            }
        )
    return {
        "source": resolved_source,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "count": len(output),
        "rows": output,
        "data_revision": revision,
    }


def list_equipment_tree(
    settings: AppSettings,
    *,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    resolved_source = _resolve_source(settings, source)
    _validate_range(start_date, end_date)
    with read_store(settings, required_tables=REFERENCE_TABLES) as connection:
        table = active_snapshot_table(connection)
        asset_refs = _reference_index(
            connection.execute(
                """
                SELECT
                    asset_tree_id,
                    name,
                    parent_asset_tree_id,
                    facility_id,
                    asset_tree_path
                FROM waites_asset_tree_reference
                WHERE source = ?
                """,
                (resolved_source,),
            ).fetchall(),
            "asset_tree_id",
        )
        equipment_refs = _reference_index(
            connection.execute(
                """
                SELECT
                    equipment_id,
                    asset_tree_id,
                    name,
                    facility_id,
                    customer_asset_id
                FROM waites_equipment_reference
                WHERE source = ?
                """,
                (resolved_source,),
            ).fetchall(),
            "equipment_id",
        )
        installation_refs = _reference_index(
            connection.execute(
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
                FROM waites_installation_point_reference
                WHERE source = ?
                """,
                (resolved_source,),
            ).fetchall(),
            "installation_point_id",
        )
        clauses, params = _snapshot_range_clauses(
            resolved_source,
            start_date,
            end_date,
        )
        activity_rows = connection.execute(
            f"""
            SELECT
                COALESCE(equipment_id, '') AS equipment_id,
                installation_point_id,
                MAX(COALESCE(equipment_name, '')) AS equipment_name,
                MAX(COALESCE(installation_point_name, '')) AS installation_point_name,
                MAX(COALESCE(sensor_id, '')) AS sensor_id,
                MAX(COALESCE(customer_asset_id, '')) AS customer_asset_id,
                GROUP_CONCAT(DISTINCT source_date) AS dates
            FROM {table}
            WHERE {" AND ".join(clauses)}
            GROUP BY COALESCE(equipment_id, ''), installation_point_id
            ORDER BY
                CASE WHEN COALESCE(equipment_id, '') GLOB '[0-9]*'
                    THEN CAST(equipment_id AS INTEGER) END,
                equipment_id,
                CAST(installation_point_id AS INTEGER),
                installation_point_id
            """,
            tuple(params),
        ).fetchall()
        revision = data_revision(
            connection,
            resolved_source,
            start_date=start_date,
            end_date=end_date,
        )
    active = _active_equipment(activity_rows)
    tree_map: dict[str, dict[str, Any]] = {}
    for equipment_id, equipment in active.items():
        equipment_ref = equipment_refs.get(equipment_id, {})
        asset_tree_id = _text(equipment_ref.get("asset_tree_id")) or "unknown"
        asset_ref = asset_refs.get(asset_tree_id, {})
        tree = tree_map.setdefault(
            asset_tree_id,
            {
                "asset_tree_id": asset_tree_id,
                "asset_tree_name": _asset_tree_name(asset_tree_id, asset_ref),
                "parent_asset_tree_id": _none_if_empty(
                    _text(asset_ref.get("parent_asset_tree_id"))
                ),
                "facility_id": _none_if_empty(_text(asset_ref.get("facility_id"))),
                "asset_tree_path": _text(asset_ref.get("asset_tree_path"))
                or _asset_tree_name(asset_tree_id, asset_ref),
                "dates": set(),
                "equipment": [],
            },
        )
        equipment_dates = sorted(equipment["dates"])
        tree["dates"].update(equipment_dates)
        sensors = []
        for installation_id, sensor in sorted(
            equipment["sensors"].items(),
            key=lambda item: _sort_key(item[0]),
        ):
            sensor_ref = installation_refs.get(installation_id, {})
            sensor_dates = sorted(sensor["dates"])
            sensors.append(
                {
                    "installation_point_id": installation_id,
                    "installation_point_name": sensor["installation_point_name"]
                    or _text(sensor_ref.get("name"))
                    or f"Sensor {installation_id}",
                    "sensor_id": sensor["sensor_id"]
                    or _text(sensor_ref.get("sensor_id")),
                    "customer_asset_id": sensor["customer_asset_id"]
                    or _text(sensor_ref.get("customer_asset_id")),
                    "active_dates": sensor_dates,
                    "first_date": sensor_dates[0] if sensor_dates else None,
                    "last_date": sensor_dates[-1] if sensor_dates else None,
                    "date_count": len(sensor_dates),
                }
            )
        tree["equipment"].append(
            {
                "equipment_id": equipment_id,
                "equipment_name": _text(equipment_ref.get("name"))
                or equipment["equipment_name"]
                or f"Equipment {equipment_id}",
                "customer_asset_id": equipment["customer_asset_id"]
                or _text(equipment_ref.get("customer_asset_id")),
                "asset_tree_id": asset_tree_id,
                "active_dates": equipment_dates,
                "first_date": equipment_dates[0] if equipment_dates else None,
                "last_date": equipment_dates[-1] if equipment_dates else None,
                "date_count": len(equipment_dates),
                "sensor_count": len(sensors),
                "sensors": sensors,
            }
        )
    trees = []
    for tree in tree_map.values():
        dates = sorted(tree.pop("dates"))
        tree["equipment"].sort(
            key=lambda row: (_sort_key(row["equipment_id"]), row["equipment_name"])
        )
        tree["equipment_count"] = len(tree["equipment"])
        tree["sensor_count"] = sum(
            len(row["sensors"]) for row in tree["equipment"]
        )
        tree["active_dates"] = dates
        tree["first_date"] = dates[0] if dates else None
        tree["last_date"] = dates[-1] if dates else None
        tree["date_count"] = len(dates)
        trees.append(tree)
    trees.sort(key=lambda row: (_sort_key(row["asset_tree_id"]), row["asset_tree_name"]))
    return {
        "source": resolved_source,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "asset_tree_count": len(trees),
        "equipment_count": sum(row["equipment_count"] for row in trees),
        "sensor_count": sum(row["sensor_count"] for row in trees),
        "asset_trees": trees,
        "data_revision": revision,
    }


def resolve_scope(
    settings: AppSettings,
    *,
    source: str,
    start_date: date,
    end_date: date,
    scope: str,
    scope_id: str | None = None,
    asset_tree_id: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
) -> dict[str, Any]:
    scope_type = scope if scope in VALID_SCOPE_TYPES else "all"
    canonical_id = _text(scope_id)
    if canonical_id:
        if scope_type == "asset_tree" and not asset_tree_id:
            asset_tree_id = canonical_id
        elif scope_type == "equipment" and not equipment_id:
            equipment_id = canonical_id
        elif scope_type == "sensor" and not installation_point_id and not sensor_id:
            installation_point_id = canonical_id
    context: dict[str, Any] = {
        "type": scope_type,
        "asset_tree_id": _text(asset_tree_id),
        "equipment_id": _text(equipment_id),
        "installation_point_id": _text(installation_point_id),
        "sensor_id": _text(sensor_id),
        "label": "All equipment",
        "equipment_ids": set(),
        "installation_point_ids": set(),
        "activation_equipment_ids": set(),
    }
    if scope_type == "all":
        return context

    with read_store(settings, required_tables=REFERENCE_TABLES) as connection:
        if scope_type == "asset_tree":
            _resolve_asset_tree_scope(connection, source, context, start_date, end_date)
        elif scope_type == "equipment":
            _resolve_equipment_scope(connection, source, context, start_date, end_date)
        else:
            _resolve_sensor_scope(connection, source, context, start_date, end_date)
    return context


def public_scope(context: dict[str, Any]) -> dict[str, Any]:
    scope_type = str(context["type"])
    canonical_id = {
        "asset_tree": context.get("asset_tree_id"),
        "equipment": context.get("equipment_id"),
        "sensor": context.get("installation_point_id") or context.get("sensor_id"),
    }.get(scope_type)
    return {
        "type": scope_type,
        "id": _none_if_empty(_text(canonical_id)),
        "asset_tree_id": _none_if_empty(context.get("asset_tree_id", "")),
        "equipment_id": _none_if_empty(context.get("equipment_id", "")),
        "installation_point_id": _none_if_empty(
            context.get("installation_point_id", "")
        ),
        "sensor_id": _none_if_empty(context.get("sensor_id", "")),
        "label": context.get("label", "All equipment"),
    }


def row_matches_scope(
    row: dict[str, Any],
    scope_context: dict[str, Any],
) -> bool:
    """Return whether an operational row belongs to a resolved canonical scope."""
    if scope_context["type"] == "all":
        return True
    installation_ids = scope_context.get("installation_point_ids", set())
    equipment_ids = scope_context.get("equipment_ids", set())
    installation_id = _text(row.get("installation_point_id"))
    equipment_id = _text(row.get("equipment_id"))
    if scope_context["type"] == "sensor":
        return bool(installation_id and installation_id in installation_ids)
    if installation_id and installation_ids:
        return installation_id in installation_ids
    if equipment_id and equipment_ids:
        return equipment_id in equipment_ids
    return False


def filter_rows_for_scope(
    rows: list[dict[str, Any]],
    scope_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if scope_context["type"] == "all":
        return rows
    return [row for row in rows if row_matches_scope(row, scope_context)]


def _resolve_asset_tree_scope(
    connection: sqlite3.Connection,
    source: str,
    context: dict[str, Any],
    start_date: date,
    end_date: date,
) -> None:
    selected_id = context["asset_tree_id"]
    asset = connection.execute(
        """
        SELECT name
        FROM waites_asset_tree_reference
        WHERE source = ? AND CAST(asset_tree_id AS TEXT) = ?
        """,
        (source, selected_id),
    ).fetchone()
    equipment_ids = {
        _text(row["equipment_id"])
        for row in connection.execute(
            """
            SELECT equipment_id
            FROM waites_equipment_reference
            WHERE source = ? AND CAST(asset_tree_id AS TEXT) = ?
            """,
            (source, selected_id),
        ).fetchall()
        if _text(row["equipment_id"])
    }
    active_equipment, installation_ids = _active_scope_ids(
        connection,
        source,
        start_date,
        end_date,
        equipment_ids=equipment_ids,
    )
    context["label"] = (
        _text(asset["name"]) if asset is not None else f"Asset Tree {selected_id}"
    )
    context["equipment_ids"] = active_equipment
    context["activation_equipment_ids"] = active_equipment
    context["installation_point_ids"] = installation_ids


def _resolve_equipment_scope(
    connection: sqlite3.Connection,
    source: str,
    context: dict[str, Any],
    start_date: date,
    end_date: date,
) -> None:
    selected_id = context["equipment_id"]
    equipment = connection.execute(
        """
        SELECT asset_tree_id, name, customer_asset_id
        FROM waites_equipment_reference
        WHERE source = ? AND CAST(equipment_id AS TEXT) = ?
        """,
        (source, selected_id),
    ).fetchone()
    asset_tree_id = _text(equipment["asset_tree_id"]) if equipment else ""
    activation_ids = _equipment_ids_for_asset(connection, source, asset_tree_id)
    active_equipment, installation_ids = _active_scope_ids(
        connection,
        source,
        start_date,
        end_date,
        equipment_ids={selected_id},
    )
    context.update(
        {
            "asset_tree_id": asset_tree_id,
            "equipment_name": _text(equipment["name"]) if equipment else "",
            "customer_asset_id": (
                _text(equipment["customer_asset_id"]) if equipment else ""
            ),
            "label": (
                _text(equipment["name"]) if equipment else f"Equipment {selected_id}"
            ),
            "equipment_ids": active_equipment or {selected_id},
            "activation_equipment_ids": activation_ids,
            "installation_point_ids": installation_ids,
        }
    )


def _resolve_sensor_scope(
    connection: sqlite3.Connection,
    source: str,
    context: dict[str, Any],
    start_date: date,
    end_date: date,
) -> None:
    clauses = ["source = ?"]
    params: list[Any] = [source]
    if context["installation_point_id"]:
        clauses.append("CAST(installation_point_id AS TEXT) = ?")
        params.append(context["installation_point_id"])
    elif context["sensor_id"]:
        clauses.append("CAST(sensor_id AS TEXT) = ?")
        params.append(context["sensor_id"])
    else:
        raise StoreNotFoundError("Sensor scope requires an installation point or sensor ID.")
    sensor = connection.execute(
        f"""
        SELECT installation_point_id, name, equipment_id, sensor_id, customer_asset_id
        FROM waites_installation_point_reference
        WHERE {" AND ".join(clauses)}
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if sensor is None:
        table = active_snapshot_table(connection)
        snapshot_clauses = [
            "source = ?",
            "source_date >= ?",
            "source_date <= ?",
        ]
        snapshot_params: list[Any] = [
            source,
            start_date.isoformat(),
            end_date.isoformat(),
        ]
        if context["installation_point_id"]:
            snapshot_clauses.append("installation_point_id = ?")
            snapshot_params.append(context["installation_point_id"])
        else:
            snapshot_clauses.append("sensor_id = ?")
            snapshot_params.append(context["sensor_id"])
        sensor = connection.execute(
            f"""
            SELECT
                installation_point_id,
                installation_point_name AS name,
                equipment_id,
                sensor_id,
                customer_asset_id
            FROM {table}
            WHERE {" AND ".join(snapshot_clauses)}
            LIMIT 1
            """,
            tuple(snapshot_params),
        ).fetchone()
    if sensor is None:
        raise StoreNotFoundError("Selected sensor is not present in the operational store.")
    installation_id = _text(sensor["installation_point_id"])
    equipment_id = _text(sensor["equipment_id"])
    equipment = connection.execute(
        """
        SELECT asset_tree_id, name, customer_asset_id
        FROM waites_equipment_reference
        WHERE source = ? AND CAST(equipment_id AS TEXT) = ?
        """,
        (source, equipment_id),
    ).fetchone()
    asset_tree_id = _text(equipment["asset_tree_id"]) if equipment else ""
    context.update(
        {
            "asset_tree_id": asset_tree_id,
            "equipment_id": equipment_id,
            "installation_point_id": installation_id,
            "sensor_id": _text(sensor["sensor_id"]),
            "equipment_name": _text(equipment["name"]) if equipment else "",
            "customer_asset_id": _text(sensor["customer_asset_id"])
            or (_text(equipment["customer_asset_id"]) if equipment else ""),
            "sensor_name": _text(sensor["name"]),
            "label": _text(sensor["name"]) or f"Sensor {installation_id}",
            "equipment_ids": {equipment_id} if equipment_id else set(),
            "activation_equipment_ids": _equipment_ids_for_asset(
                connection,
                source,
                asset_tree_id,
            ),
            "installation_point_ids": {installation_id},
        }
    )


def _active_scope_ids(
    connection: sqlite3.Connection,
    source: str,
    start_date: date,
    end_date: date,
    *,
    equipment_ids: set[str],
) -> tuple[set[str], set[str]]:
    if not equipment_ids:
        return set(), set()
    placeholders = ", ".join("?" for _value in equipment_ids)
    table = active_snapshot_table(connection)
    rows = connection.execute(
        f"""
        SELECT DISTINCT equipment_id, installation_point_id
        FROM {table}
        WHERE source = ?
          AND source_date >= ?
          AND source_date <= ?
          AND equipment_id IN ({placeholders})
        """,
        (
            source,
            start_date.isoformat(),
            end_date.isoformat(),
            *sorted(equipment_ids),
        ),
    ).fetchall()
    return (
        {_text(row["equipment_id"]) for row in rows if _text(row["equipment_id"])},
        {
            _text(row["installation_point_id"])
            for row in rows
            if _text(row["installation_point_id"])
        },
    )


def _equipment_ids_for_asset(
    connection: sqlite3.Connection,
    source: str,
    asset_tree_id: str,
) -> set[str]:
    if not asset_tree_id:
        return set()
    return {
        _text(row["equipment_id"])
        for row in connection.execute(
            """
            SELECT equipment_id
            FROM waites_equipment_reference
            WHERE source = ? AND CAST(asset_tree_id AS TEXT) = ?
            """,
            (source, asset_tree_id),
        ).fetchall()
        if _text(row["equipment_id"])
    }


def _active_equipment(rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        equipment_id = _text(row["equipment_id"])
        equipment = active.setdefault(
            equipment_id,
            {
                "equipment_name": "",
                "customer_asset_id": "",
                "dates": set(),
                "sensors": {},
            },
        )
        dates = set(_csv_values(row["dates"]))
        equipment["dates"].update(dates)
        equipment["equipment_name"] = (
            equipment["equipment_name"] or _text(row["equipment_name"])
        )
        equipment["customer_asset_id"] = (
            equipment["customer_asset_id"] or _text(row["customer_asset_id"])
        )
        installation_id = _text(row["installation_point_id"])
        if not installation_id:
            continue
        equipment["sensors"][installation_id] = {
            "installation_point_name": _text(row["installation_point_name"]),
            "sensor_id": _text(row["sensor_id"]),
            "customer_asset_id": _text(row["customer_asset_id"]),
            "dates": dates,
        }
    return active


def _reference_index(
    rows: list[sqlite3.Row],
    key: str,
) -> dict[str, dict[str, Any]]:
    return {
        _text(row[key]): dict(row)
        for row in rows
        if _text(row[key])
    }


def _snapshot_range_clauses(
    source: str,
    start_date: date | None,
    end_date: date | None,
) -> tuple[list[str], list[Any]]:
    clauses = ["source = ?"]
    params: list[Any] = [source]
    if start_date is not None:
        clauses.append("source_date >= ?")
        params.append(start_date.isoformat())
    if end_date is not None:
        clauses.append("source_date <= ?")
        params.append(end_date.isoformat())
    return clauses, params


def _validate_range(start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date must be on or after start_date")


def _resolve_source(settings: AppSettings, source: str | None) -> str:
    return resolve_configured_source(settings, source)


def _asset_tree_name(asset_tree_id: str, row: dict[str, Any]) -> str:
    if asset_tree_id == "unknown":
        return _text(row.get("name")) or "Unknown Asset Tree"
    return _text(row.get("name")) or f"Asset Tree {asset_tree_id}"


def _sorted_csv(value: Any) -> list[str]:
    return sorted(_csv_values(value))


def _csv_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    return list(dict.fromkeys(part for part in str(value).split(",") if part))


def _none_if_empty(value: str) -> str | None:
    return value or None


def _text(value: Any) -> str:
    return "" if value in (None, "") else str(value)


def _sort_key(value: Any) -> tuple[int, Any]:
    text = _text(value)
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)

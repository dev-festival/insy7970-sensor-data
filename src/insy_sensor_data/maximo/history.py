from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.joins import normalize_asset_number
from insy_sensor_data.maximo.db import MaximoDatabaseError, fetch_asset_workorders
from insy_sensor_data.maximo.fixtures import load_workorder_fixture


WorkorderQuery = Callable[[AppSettings, Sequence[str], date, date], list[dict[str, Any]]]


def load_asset_history(
    settings: AppSettings,
    assetnums: Iterable[object],
    start_date: date,
    end_date: date,
    source: str = "mock",
    fixture_dir: Path | None = None,
    query_runner: WorkorderQuery = fetch_asset_workorders,
) -> dict[str, Any]:
    """Return normalized work orders for the supplied, bounded Waites asset set."""
    source_mode = source.strip().lower()
    if source_mode not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise ValueError(f"source must be one of: {allowed}")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    normalized_assets = sorted(
        {normalize_asset_number(value) for value in assetnums if normalize_asset_number(value)}
    )
    queryable_assets, skipped_assets = _queryable_assets(
        normalized_assets,
        settings.maximo_assetnum_max_length,
    )
    if source_mode == "mock":
        records = load_workorder_fixture(fixture_dir)
        rows = [
            _normalize_workorder(record, assetnum=None)
            for record in records
        ]
    else:
        records, query_warnings, successful_assets = _load_live_records(
            settings=settings,
            assetnums=queryable_assets,
            start_date=start_date,
            end_date=end_date,
            query_runner=query_runner,
        )
        skipped_assets.extend(query_warnings)
        rows = [_normalize_workorder(record, assetnum=None) for record in records]
        if query_warnings and not successful_assets:
            return {
                "status": "unavailable",
                "message": "Maximo could not query any valid asset number in this scope.",
                "provider": "maximo",
                "input": source_mode,
                "assetnums": normalized_assets,
                "queried_assetnums": [],
                "skipped_assets": skipped_assets,
                "warning_count": len(skipped_assets),
                "row_count": 0,
                "rows": [],
            }

    selected = [
        row
        for row in rows
        if row["assetnum"] in queryable_assets
        and _within_range(row["reportdate"], start_date, end_date)
    ]
    deduplicated = {
        (row["assetnum"], row["wonum"]): row
        for row in selected
        if row["assetnum"] and row["wonum"]
    }
    ordered = sorted(
        deduplicated.values(),
        key=lambda row: (row["reportdate"], row["wonum"]),
        reverse=True,
    )
    return {
        "status": "partial" if skipped_assets else "available",
        "provider": "maximo",
        "input": source_mode,
        "assetnums": normalized_assets,
        "queried_assetnums": queryable_assets,
        "skipped_assets": skipped_assets,
        "warning_count": len(skipped_assets),
        "row_count": len(ordered),
        "rows": ordered,
    }


def _queryable_assets(assetnums: list[str], max_length: int) -> tuple[list[str], list[dict[str, str]]]:
    queryable = []
    skipped = []
    for assetnum in assetnums:
        if any(character.isspace() for character in assetnum):
            skipped.append(
                {
                    "assetnum": assetnum,
                    "reason": "contains whitespace and is not a Maximo asset identifier",
                }
            )
        elif len(assetnum) > max_length:
            skipped.append(
                {
                    "assetnum": assetnum,
                    "reason": f"exceeds Maximo asset number length {max_length}",
                }
            )
        else:
            queryable.append(assetnum)
    return queryable, skipped


def _load_live_records(
    settings: AppSettings,
    assetnums: list[str],
    start_date: date,
    end_date: date,
    query_runner: WorkorderQuery,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    if not assetnums:
        return [], [], []
    try:
        return query_runner(settings, assetnums, start_date, end_date), [], list(assetnums)
    except MaximoDatabaseError as exc:
        if not _is_asset_data_error(exc) or len(assetnums) == 1:
            return [], [{"assetnum": ", ".join(assetnums), "reason": str(exc)}], []

    midpoint = len(assetnums) // 2
    left_rows, left_warnings, left_successes = _load_live_records(
        settings, assetnums[:midpoint], start_date, end_date, query_runner
    )
    right_rows, right_warnings, right_successes = _load_live_records(
        settings, assetnums[midpoint:], start_date, end_date, query_runner
    )
    return (
        left_rows + right_rows,
        left_warnings + right_warnings,
        left_successes + right_successes,
    )


def _is_asset_data_error(exc: MaximoDatabaseError) -> bool:
    message = str(exc).lower()
    return "22001" in message or "string data right truncation" in message


def _normalize_workorder(record: dict[str, Any], assetnum: str | None) -> dict[str, str]:
    matched_asset = normalize_asset_number(_value(record, "assetnum")) or assetnum or ""
    return {
        "wonum": _text(_value(record, "wonum")),
        "assetnum": matched_asset,
        "reportdate": _date_text(_value(record, "reportdate")),
        "description": _text(_value(record, "description")),
        "worktype": _text(_value(record, "worktype")),
        "status": _text(_value(record, "status")),
        "actfinish": _date_text(_value(record, "actfinish")),
    }


def _value(record: dict[str, Any], key: str) -> Any:
    for candidate, value in record.items():
        if candidate.lower() == key:
            return value
    return None


def _within_range(reportdate: str, start_date: date, end_date: date) -> bool:
    if not reportdate:
        return False
    try:
        return start_date <= date.fromisoformat(reportdate) <= end_date
    except ValueError:
        return False


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)[:10]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()

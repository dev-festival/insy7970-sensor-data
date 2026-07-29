"""Shared alignment helpers for Waites and Maximo data."""
from __future__ import annotations

from typing import Any, Iterable


def normalize_asset_number(value: object) -> str:
    """Normalize the asset key used to align Waites and Maximo records."""
    return "" if value is None else str(value).strip().upper()


def index_snapshot_assets(
    rows: Iterable[dict[str, Any]],
    equipment_ids: set[str],
) -> dict[str, dict[str, set[str]]]:
    """Index a bounded snapshot scope by normalized customer asset number."""
    index: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        equipment_id = _text_id(row.get("equipment_id"))
        if equipment_id not in equipment_ids:
            continue
        assetnum = normalize_asset_number(row.get("customer_asset_id"))
        if not assetnum:
            continue
        bucket = index.setdefault(
            assetnum,
            {"equipment_ids": set(), "installation_point_ids": set()},
        )
        if equipment_id:
            bucket["equipment_ids"].add(equipment_id)
        installation_point_id = _text_id(row.get("installation_point_id"))
        if installation_point_id:
            bucket["installation_point_ids"].add(installation_point_id)
    return index


def _text_id(value: object) -> str:
    return "" if value is None else str(value).strip()

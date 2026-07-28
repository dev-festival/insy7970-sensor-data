from __future__ import annotations

from typing import Any, Iterable


CHILD_FIELDS = ("children", "child_nodes", "nodes", "items", "asset_trees")
ID_FIELDS = ("asset_tree_id", "assetTreeId", "id")
NAME_FIELDS = ("name", "asset_tree_name", "assetTreeName", "label", "title")
PARENT_FIELDS = ("parent_asset_tree_id", "parentAssetTreeId", "parent_id", "parentId")
PATH_FIELDS = ("asset_tree_path", "assetTreePath", "path", "breadcrumb")
FACILITY_FIELDS = ("facility_id", "facilityId")


def asset_tree_records_from_payload(payload: dict[str, Any]) -> list[Any]:
    records = payload.get("list")
    if isinstance(records, list):
        return records

    for field in ("data", "records", "asset_trees", "assetTrees"):
        nested_records = payload.get(field)
        if isinstance(nested_records, list):
            return nested_records

    for field in ("tree", "asset_tree", "assetTree"):
        tree = payload.get(field)
        if isinstance(tree, dict):
            return [tree]

    if _looks_like_asset_tree(payload):
        return [payload]
    return []


def normalize_asset_tree_records(records: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        _walk_asset_tree(record, parent_id=None, path_parts=(), rows=rows, seen=seen)
    return rows


def _walk_asset_tree(
    record: Any,
    parent_id: Any | None,
    path_parts: tuple[str, ...],
    rows: list[dict[str, Any]],
    seen: set[str],
) -> None:
    if not isinstance(record, dict):
        return

    asset_tree_id = _first_value(record, ID_FIELDS)
    name = _first_value(record, NAME_FIELDS)
    explicit_parent_id = _first_value(record, PARENT_FIELDS)
    row_parent_id = explicit_parent_id if explicit_parent_id not in (None, "") else parent_id
    facility_id = _first_value(record, FACILITY_FIELDS)

    label = str(name or f"Asset Tree {asset_tree_id}") if asset_tree_id not in (None, "") else str(name or "")
    next_path_parts = (*path_parts, label) if label else path_parts
    asset_tree_path = _first_value(record, PATH_FIELDS) or " / ".join(next_path_parts)

    if asset_tree_id not in (None, ""):
        key = str(asset_tree_id)
        if key not in seen:
            rows.append(
                {
                    "asset_tree_id": asset_tree_id,
                    "name": name,
                    "parent_asset_tree_id": row_parent_id,
                    "facility_id": facility_id,
                    "asset_tree_path": asset_tree_path,
                }
            )
            seen.add(key)

    for field in CHILD_FIELDS:
        children = record.get(field)
        if isinstance(children, list):
            for child in children:
                _walk_asset_tree(
                    child,
                    parent_id=asset_tree_id if asset_tree_id not in (None, "") else row_parent_id,
                    path_parts=next_path_parts,
                    rows=rows,
                    seen=seen,
                )


def _first_value(record: dict[str, Any], fields: Iterable[str]) -> Any:
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return value
    return None


def _looks_like_asset_tree(record: dict[str, Any]) -> bool:
    return any(record.get(field) not in (None, "") for field in ID_FIELDS + NAME_FIELDS) or any(
        isinstance(record.get(field), list)
        for field in CHILD_FIELDS
    )

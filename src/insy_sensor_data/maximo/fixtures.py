from __future__ import annotations

from pathlib import Path
from typing import Any

from insy_sensor_data.artifacts import read_json
from insy_sensor_data.storage import get_default_fixture_dir


def load_workorder_fixture(fixture_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = fixture_dir or get_default_fixture_dir()
    path = base_dir / "maximo" / "workorders.json"
    payload = read_json(path)
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise ValueError(f"Maximo fixture must be an object with a list: {path}")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Maximo fixture rows must be objects: {path}")
    return rows

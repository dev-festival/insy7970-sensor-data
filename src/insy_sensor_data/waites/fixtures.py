from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from insy_sensor_data.storage import get_default_fixture_dir
from insy_sensor_data.waites.client import ENDPOINT_FILENAMES


def load_waites_fixture(endpoint: str, fixture_dir: Path | None = None) -> dict[str, Any]:
    if endpoint not in ENDPOINT_FILENAMES:
        raise ValueError(f"Unknown Waites endpoint: {endpoint}")

    base_dir = fixture_dir or get_default_fixture_dir()
    path = base_dir / "waites" / ENDPOINT_FILENAMES[endpoint]
    if not path.exists():
        raise FileNotFoundError(f"Missing Waites fixture: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise ValueError(f"Waites fixture must be an object with a list: {path}")
    return data

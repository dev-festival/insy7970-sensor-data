from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
import json

from insy_sensor_data.storage import get_default_fixture_dir
from insy_sensor_data.waites.client import ENDPOINT_FILENAMES, READING_ENDPOINTS


MOCK_TREND_DATES = ("2025-07-09", "2025-07-10", "2025-07-11")
MOCK_TREND_BEHAVIORS = {
    "2025-07-09": {
        "label": "baseline",
        "behaviors": [
            "201300 starts a rising vibration pattern",
            "201301 remains stable",
            "201303 starts high before normalizing",
            "201307 starts a temperature spike pattern",
        ],
    },
    "2025-07-10": {
        "label": "movement",
        "behaviors": [
            "201300 vibration increases",
            "201301 remains stable",
            "201303 vibration and temperature normalize",
            "201307 temperature spikes",
            "201305 has no readings",
        ],
    },
    "2025-07-11": {
        "label": "follow-up",
        "behaviors": [
            "201300 vibration increases again",
            "201301 remains stable",
            "201303 continues normalizing",
            "201307 temperature returns near baseline",
            "201305 readings return",
        ],
    },
}

_MISSING_READING_IDS = {
    "2025-07-10": {201305},
}

_RMS_SCALES = {
    "2025-07-10": {
        201300: {"acceleration": 1.20, "velocity": 1.20, "pk-pk": 1.15},
        201303: {"acceleration": 0.78, "velocity": 0.78, "pk-pk": 0.78},
    },
    "2025-07-11": {
        201300: {"acceleration": 1.45, "velocity": 1.45, "pk-pk": 1.35},
        201303: {"acceleration": 0.55, "velocity": 0.55, "pk-pk": 0.55},
    },
}

_IMPACT_SCALES = {
    "2025-07-10": {
        201300: 1.25,
        201303: 0.60,
    },
    "2025-07-11": {
        201300: 1.60,
        201303: 0.35,
    },
}

_TEMPERATURE_DELTAS = {
    "2025-07-10": {
        201303: {"value": -22.0},
        201307: {"value": 25.0, "ambient": 2.0},
    },
    "2025-07-11": {
        201303: {"value": -42.0},
        201307: {"value": 1.0, "ambient": 0.3},
    },
}


def load_waites_fixture(
    endpoint: str,
    fixture_dir: Path | None = None,
    run_date: date | None = None,
) -> dict[str, Any]:
    if endpoint not in ENDPOINT_FILENAMES:
        raise ValueError(f"Unknown Waites endpoint: {endpoint}")

    base_dir = fixture_dir or get_default_fixture_dir()
    path = base_dir / "waites" / ENDPOINT_FILENAMES[endpoint]
    if not path.exists():
        raise FileNotFoundError(f"Missing Waites fixture: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise ValueError(f"Waites fixture must be an object with a list: {path}")
    if run_date is not None:
        data = _apply_mock_trend_overlay(endpoint, data, run_date)
    return data


def describe_mock_trend_date(run_date: date) -> dict[str, Any] | None:
    scenario = MOCK_TREND_BEHAVIORS.get(run_date.isoformat())
    if scenario is None:
        return None
    return {"date": run_date.isoformat(), **scenario}


def _apply_mock_trend_overlay(
    endpoint: str,
    envelope: dict[str, Any],
    run_date: date,
) -> dict[str, Any]:
    data = deepcopy(envelope)
    if endpoint not in READING_ENDPOINTS:
        return data

    run_date_key = run_date.isoformat()
    missing_ids = _MISSING_READING_IDS.get(run_date_key, set())
    rows: list[dict[str, Any]] = []
    for row in data["list"]:
        installation_id = row.get("installation_point_id")
        if installation_id in missing_ids:
            continue

        dated_row = _with_run_date(row, run_date)
        _apply_reading_adjustments(endpoint, dated_row, run_date_key)
        rows.append(dated_row)

    data["list"] = rows
    return data


def _with_run_date(row: dict[str, Any], run_date: date) -> dict[str, Any]:
    output = dict(row)
    timestamp = output.get("timestamp")
    if isinstance(timestamp, str) and "T" in timestamp:
        _old_date, time_part = timestamp.split("T", 1)
        output["timestamp"] = f"{run_date.isoformat()}T{time_part}"
    return output


def _apply_reading_adjustments(endpoint: str, row: dict[str, Any], run_date_key: str) -> None:
    installation_id = row.get("installation_point_id")
    if endpoint == "readings-rms":
        for field, factor in _RMS_SCALES.get(run_date_key, {}).get(installation_id, {}).items():
            row[field] = _scale_value(row.get(field), factor)
    elif endpoint == "readings-impact-vue":
        factor = _IMPACT_SCALES.get(run_date_key, {}).get(installation_id)
        if factor is not None:
            row["impact_vue_acceleration"] = _scale_value(
                row.get("impact_vue_acceleration"),
                factor,
            )
    elif endpoint == "readings-temperature":
        for field, delta in _TEMPERATURE_DELTAS.get(run_date_key, {}).get(installation_id, {}).items():
            row[field] = _add_value(row.get(field), delta)


def _scale_value(value: Any, factor: float) -> float | None:
    if value is None:
        return None
    return round(float(value) * factor, 6)


def _add_value(value: Any, delta: float) -> float | None:
    if value is None:
        return None
    return round(float(value) + delta, 6)

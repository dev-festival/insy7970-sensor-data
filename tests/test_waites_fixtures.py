from pathlib import Path
from typing import Any
import json


FIXTURE_DIR = Path(__file__).parent / "fixtures"
WAITES_DIR = FIXTURE_DIR / "waites"

WAITES_REQUIRED_FIELDS = {
    "equipment.json": {"equipment_id", "asset_tree_id", "name", "facility_id", "customer_asset_id"},
    "installation-points.json": {
        "installation_point_id",
        "name",
        "equipment_id",
        "sensor_id",
        "facility_id",
        "last_seen",
        "customer_asset_id",
    },
    "readings-rms.json": {
        "timestamp",
        "installation_point_id",
        "axis",
        "facility_id",
        "acceleration",
        "velocity",
        "pk-pk",
        "cf",
    },
    "readings-impact-vue.json": {
        "timestamp",
        "installation_point_id",
        "axis",
        "facility_id",
        "impact_vue_acceleration",
    },
    "readings-temperature.json": {"timestamp", "installation_point_id", "value", "ambient", "facility_id"},
    "action-items.json": {"closed_at"},
}


def test_waites_fixtures_are_enveloped_lists() -> None:
    for fixture_name in WAITES_REQUIRED_FIELDS:
        payload = _load_waites(fixture_name)
        assert isinstance(payload, dict)
        assert isinstance(payload["list"], list)
        assert payload["list"], f"{fixture_name} should not be empty"


def test_waites_fixtures_include_required_fields() -> None:
    for fixture_name, required_fields in WAITES_REQUIRED_FIELDS.items():
        records = _load_waites(fixture_name)["list"]
        for record in records:
            assert required_fields <= record.keys(), fixture_name


def test_waites_fixtures_include_contract_awkwardness() -> None:
    equipment = _load_waites("equipment.json")["list"]
    installation_points = _load_waites("installation-points.json")["list"]
    rms = _load_waites("readings-rms.json")["list"]
    impact = _load_waites("readings-impact-vue.json")["list"]
    temperature = _load_waites("readings-temperature.json")["list"]
    action_items = _load_waites("action-items.json")["list"]

    equipment_ids = {record["equipment_id"] for record in equipment}
    installation_ids = {record["installation_point_id"] for record in installation_points}
    rms_ids = {record["installation_point_id"] for record in rms}
    temp_ids = {record["installation_point_id"] for record in temperature}

    assert any(record["customer_asset_id"] == "" for record in equipment)
    assert any(record["name"] != record["name"].strip() for record in equipment)
    assert any(record["sensor_id"] is None for record in installation_points)
    assert any(record["last_seen"] in (None, "2024-01-01 00:00:00") for record in installation_points)
    assert any(record["equipment_id"] not in equipment_ids for record in installation_points)
    assert any(record["customer_asset_id"] == "" for record in installation_points)
    assert any(record["customer_asset_id"] == "A119451" for record in installation_points)
    assert any(record["installation_point_id"] not in installation_ids for record in rms)
    assert any(record["acceleration"] is None for record in rms)
    assert _has_duplicate_timestamp_axis(rms)
    assert any(record["impact_vue_acceleration"] is None for record in impact)
    assert any((record["impact_vue_acceleration"] or 0) >= 10 for record in impact)
    assert 201307 in temp_ids - rms_ids
    assert 201304 in rms_ids - temp_ids
    assert any(record["ambient"] is None for record in temperature)
    assert any(record["value"] >= 80 for record in temperature)
    assert any(record["closed_at"] is None for record in action_items)
    assert any(record["closed_at"] for record in action_items)
    assert any("installation_point" not in record for record in action_items)


def test_maximo_fixture_includes_future_join_cases() -> None:
    payload = json.loads((FIXTURE_DIR / "maximo" / "workorders.json").read_text(encoding="utf-8"))
    records = payload["list"]

    assert any(record["assetnum"] == "LEVF412TS" for record in records)
    assert any(record["assetnum"] == "MAXIMO-ONLY" for record in records)
    assert any(record["actfinish"] is None for record in records)


def _load_waites(name: str) -> dict[str, list[dict[str, Any]]]:
    return json.loads((WAITES_DIR / name).read_text(encoding="utf-8"))


def _has_duplicate_timestamp_axis(records: list[dict[str, Any]]) -> bool:
    seen: set[tuple[int, str, str]] = set()
    for record in records:
        key = (record["installation_point_id"], record["axis"], record["timestamp"])
        if key in seen:
            return True
        seen.add(key)
    return False

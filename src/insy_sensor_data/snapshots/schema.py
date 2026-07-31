from __future__ import annotations


RMS_METRIC_PREFIXES = ("rms_accel", "rms_vel", "rms_pkpk", "rms_cf")
AXES = ("x", "y", "z")
STATS = ("mean", "std", "max", "min")

SNAPSHOT_METADATA_FIELDS = [
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "facility_id",
    "customer_asset_id",
    "installation_customer_asset_id",
    "equipment_customer_asset_id",
]
IMPACT_FIELDS = [f"impact_{stat}" for stat in STATS]
RMS_FIELDS = [
    f"{prefix}_{stat}_{axis}"
    for prefix in RMS_METRIC_PREFIXES
    for stat in STATS
    for axis in AXES
]
TEMP_FIELDS = [f"temp_sensor_{stat}" for stat in STATS] + [
    f"temp_ambient_{stat}" for stat in STATS
]
SNAPSHOT_FIELDS = SNAPSHOT_METADATA_FIELDS + IMPACT_FIELDS + RMS_FIELDS + TEMP_FIELDS

SNAPSHOT_TEXT_FIELDS = {
    "installation_point_id",
    "installation_point_name",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "facility_id",
    "customer_asset_id",
    "installation_customer_asset_id",
    "equipment_customer_asset_id",
}


def snapshot_column_type(field: str) -> str:
    return "TEXT" if field in SNAPSHOT_TEXT_FIELDS else "REAL"


def snapshot_column_value(field: str, value: object) -> object:
    if value in (None, ""):
        return None
    if field in SNAPSHOT_TEXT_FIELDS:
        return str(value)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"Snapshot field {field!r} must be numeric or null.")

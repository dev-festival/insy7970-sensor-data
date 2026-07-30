from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.store.references import public_scope, resolve_scope
from insy_sensor_data.store.snapshots import query_trend_product


VALID_SCOPE_TYPES = {"all", "asset_tree", "equipment", "sensor"}
AXIS_METRICS = {"rms_vel", "rms_accel", "rms_pkpk", "rms_cf"}
NON_AXIS_METRICS = {"impact", "temp_sensor", "temp_ambient"}
DEFAULT_DETAIL_LIMIT = 500
MAX_DETAIL_LIMIT = 2_000


def load_trend_view(
    settings: AppSettings,
    *,
    start_date: date,
    end_date: date,
    source: str | None = None,
    scope: str = "all",
    asset_tree_id: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
    customer_asset_id: str | None = None,
    metric: str = "rms_vel",
    dimension: str = "x",
    stat: str = "mean",
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
    detail_offset: int = 0,
    _scope_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_source = _resolve_source(settings, source)
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    selected_limit = _validate_detail_limit(detail_limit)
    selected_offset = _validate_detail_offset(detail_offset)
    selected_metric = _normalize_metric(metric)
    selected_dimension = _normalize_dimension(selected_metric, dimension)
    selected_stat = _normalize_stat(stat)
    value_field = metric_field(
        selected_metric,
        selected_stat,
        selected_dimension,
    )
    value_fields = trend_value_fields(
        selected_metric,
        selected_dimension,
        selected_stat,
    )
    scope_context = _scope_context or resolve_scope(
        settings,
        source=resolved_source,
        start_date=start_date,
        end_date=end_date,
        scope=scope,
        asset_tree_id=asset_tree_id,
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        sensor_id=sensor_id,
    )
    query_equipment_ids, query_installation_ids = _query_scope_ids(
        scope_context,
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
    )
    queried = query_trend_product(
        settings,
        start_date=start_date,
        end_date=end_date,
        source=resolved_source,
        value_field=value_field,
        detail_fields=[
            "installation_point_id",
            "installation_point_name",
            "equipment_id",
            "equipment_name",
            "sensor_id",
            "customer_asset_id",
            *value_fields,
        ],
        detail_limit=selected_limit,
        detail_offset=selected_offset,
        aggregate_series=scope_context["type"] in {"all", "asset_tree"},
        equipment_ids=query_equipment_ids,
        installation_point_ids=query_installation_ids,
        sensor_id=sensor_id,
        customer_asset_id=customer_asset_id,
    )
    detail_rows = queried["detail_rows"]
    series = (
        [
            {
                "id": "scope",
                "label": "Equipment average",
                "aggregation": "mean_of_equipment_means",
                "rows": queried["series_rows"],
            }
        ]
        if scope_context["type"] in {"all", "asset_tree"}
        and queried["series_rows"]
        else trend_series(queried["series_rows"], scope_context, value_field)
    )
    sensor_record_count = queried["sensor_record_count"]
    equipment_record_count = queried["equipment_record_count"]
    metadata = {
        "source": resolved_source,
        "input_mode": "sqlite",
        "served_from": "sqlite",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "metric": selected_metric,
        "dimension": selected_dimension,
        "stat": selected_stat,
        "value_field": value_field,
        "sensor_record_count": sensor_record_count,
        "equipment_record_count": equipment_record_count,
        "detail_limit": selected_limit,
        "detail_offset": selected_offset,
        "skipped_dates": queried["skipped_dates"],
        "source_mismatch_dates": [],
        "selected_fields": queried["selected_fields"],
        "data_revision": queried["data_revision"],
    }
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "source": resolved_source,
        "input": "sqlite",
        "input_mode": "sqlite",
        "metric": selected_metric,
        "dimension": selected_dimension,
        "stat": selected_stat,
        "value_field": value_field,
        "metadata": metadata,
        "data_revision": queried["data_revision"],
        "scope": public_scope(scope_context),
        "coverage": queried["coverage"],
        "sensor_row_count": sensor_record_count,
        "filtered_sensor_row_count": sensor_record_count,
        "equipment_row_count": equipment_record_count,
        "filtered_equipment_row_count": equipment_record_count,
        "series_count": len(series),
        "series": series,
        "detail": {
            "limit": selected_limit,
            "offset": selected_offset,
            "row_count": len(detail_rows),
            "total_row_count": sensor_record_count,
            "truncated": selected_offset + len(detail_rows) < sensor_record_count,
        },
        "filters": {
            "source": source,
            "scope": scope,
            "asset_tree_id": asset_tree_id,
            "equipment_id": equipment_id,
            "installation_point_id": installation_point_id,
            "sensor_id": sensor_id,
            "customer_asset_id": customer_asset_id,
        },
        "sensor_rows": detail_rows,
    }


def trend_coverage(
    rows: list[dict[str, Any]],
    value_field: str,
) -> dict[str, Any]:
    sensor_groups: dict[str, dict[str, Any]] = {}
    observed = 0
    for row in rows:
        installation_id = _text(row.get("installation_point_id"))
        sensor_key = installation_id or _text(row.get("sensor_id")) or "unknown"
        group = sensor_groups.setdefault(
            sensor_key,
            {
                "first": row,
                "expected": 0,
                "observed": 0,
                "missing_dates": [],
            },
        )
        group["expected"] += 1
        value = finite_observation(row.get(value_field))
        if value is not None:
            group["observed"] += 1
            observed += 1
        else:
            raw_date = _text(row.get("date"))
            if raw_date:
                group["missing_dates"].append(raw_date)
    sensors = []
    for sensor_key, group in sorted(sensor_groups.items()):
        first = group["first"]
        sensors.append(
            {
                "installation_point_id": _text(
                    first.get("installation_point_id")
                ),
                "sensor_name": first.get("installation_point_name")
                or first.get("sensor_name")
                or first.get("sensor_id")
                or sensor_key,
                "expected_value_count": group["expected"],
                "observed_value_count": group["observed"],
                "coverage_percent": coverage_percent(
                    group["observed"],
                    group["expected"],
                ),
                "missing_dates": sorted(group["missing_dates"]),
            }
        )
    expected = len(rows)
    return {
        "value_field": value_field,
        "expected_value_count": expected,
        "observed_value_count": observed,
        "coverage_percent": coverage_percent(observed, expected),
        "sensors": sensors,
    }


def trend_series(
    rows: list[dict[str, Any]],
    scope_context: dict[str, Any],
    value_field: str,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    if scope_context["type"] in {"all", "asset_tree"}:
        equipment_values: dict[tuple[str, str], list[float | int]] = {}
        available_dates: set[str] = set()
        for row in rows:
            raw_date = _text(row.get("date"))
            equipment_id = _text(row.get("equipment_id")) or "__unassigned__"
            if not raw_date:
                continue
            available_dates.add(raw_date)
            value = finite_observation(row.get(value_field))
            if value is not None:
                aggregate = equipment_values.setdefault(
                    (raw_date, equipment_id),
                    [0.0, 0],
                )
                aggregate[0] += value
                aggregate[1] += 1
        date_equipment_means: dict[str, list[float | int]] = {}
        for (raw_date, _equipment_id), (total, count) in equipment_values.items():
            aggregate = date_equipment_means.setdefault(raw_date, [0.0, 0])
            aggregate[0] += total / count
            aggregate[1] += 1
        return [
            {
                "id": "scope",
                "label": "Equipment average",
                "aggregation": "mean_of_equipment_means",
                "rows": [
                    {
                        "date": raw_date,
                        value_field: (
                            date_equipment_means[raw_date][0]
                            / date_equipment_means[raw_date][1]
                            if raw_date in date_equipment_means
                            else None
                        ),
                    }
                    for raw_date in sorted(available_dates)
                ],
            }
        ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        installation_id = _text(row.get("installation_point_id"))
        if installation_id:
            grouped.setdefault(installation_id, []).append(row)
    series = []
    for installation_id, sensor_rows in sorted(
        grouped.items(),
        key=lambda item: _sort_key(item[0]),
    ):
        chronological = sorted(
            sensor_rows,
            key=lambda row: _text(row.get("date")),
        )
        first = chronological[0]
        series.append(
            {
                "id": installation_id,
                "label": first.get("installation_point_name")
                or first.get("sensor_id")
                or installation_id,
                "aggregation": "sensor",
                "rows": [
                    {
                        "date": row.get("date"),
                        "installation_point_id": installation_id,
                        value_field: row.get(value_field),
                    }
                    for row in chronological
                ],
            }
        )
    return series


def trend_value_fields(metric: str, dimension: str, stat: str) -> list[str]:
    fields = [
        metric_field(metric, selected_stat, dimension)
        for selected_stat in [stat, "mean", "max", "min"]
    ]
    return list(dict.fromkeys(fields))


def metric_field(metric: str, stat: str, dimension: str) -> str:
    if metric in AXIS_METRICS:
        return f"{metric}_{stat}_{dimension}"
    return f"{metric}_{stat}"


def finite_observation(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def coverage_percent(observed_count: int, expected_count: int) -> float:
    if not expected_count:
        return 0.0
    return round((observed_count / expected_count) * 100, 1)


def _query_scope_ids(
    scope_context: dict[str, Any],
    *,
    equipment_id: str | None,
    installation_point_id: str | None,
) -> tuple[list[str] | None, list[str] | None]:
    if scope_context["type"] == "all":
        equipment_ids: set[str] | None = None
        installation_ids: set[str] | None = None
    else:
        equipment_ids = set(scope_context.get("equipment_ids", set()))
        installation_ids = set(
            scope_context.get("installation_point_ids", set())
        )
    if equipment_id not in (None, ""):
        selected = str(equipment_id)
        equipment_ids = (
            {selected}
            if equipment_ids is None
            else equipment_ids & {selected}
        )
    if installation_point_id not in (None, ""):
        selected = str(installation_point_id)
        installation_ids = (
            {selected}
            if installation_ids is None
            else installation_ids & {selected}
        )
    return (
        sorted(equipment_ids) if equipment_ids is not None else None,
        sorted(installation_ids) if installation_ids is not None else None,
    )


def _normalize_metric(metric: str) -> str:
    selected = metric.strip().lower()
    if selected not in AXIS_METRICS | NON_AXIS_METRICS:
        allowed = ", ".join(sorted(AXIS_METRICS | NON_AXIS_METRICS))
        raise ValueError(f"metric must be one of: {allowed}")
    return selected


def _normalize_stat(stat: str) -> str:
    selected = stat.strip().lower()
    if selected not in {"mean", "min", "max"}:
        raise ValueError("stat must be one of: max, mean, min")
    return selected


def _normalize_dimension(metric: str, dimension: str) -> str:
    selected = dimension.strip().lower()
    if metric in NON_AXIS_METRICS:
        return "x"
    if selected not in {"x", "y", "z"}:
        raise ValueError("dimension must be one of: x, y, z")
    return selected


def _resolve_source(settings: AppSettings, source: str | None) -> str:
    selected = (source or settings.source_mode).strip().lower()
    if selected not in VALID_SOURCE_MODES:
        allowed = ", ".join(sorted(VALID_SOURCE_MODES))
        raise ValueError(f"source must be one of: {allowed}")
    return selected


def _validate_detail_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_DETAIL_LIMIT:
        raise ValueError(
            f"detail_limit must be between 1 and {MAX_DETAIL_LIMIT}"
        )
    return limit


def _validate_detail_offset(offset: int) -> int:
    if offset < 0:
        raise ValueError("detail_offset must be zero or greater")
    return offset


def _text(value: Any) -> str:
    return "" if value in (None, "") else str(value)


def _sort_key(value: Any) -> tuple[int, Any]:
    text = _text(value)
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)

from __future__ import annotations

from datetime import date
from typing import Any

from insy_sensor_data.config import AppSettings, VALID_SOURCE_MODES
from insy_sensor_data.clustering.policy import ACTIVE_MODEL_POLICY
from insy_sensor_data.joins import index_snapshot_assets, normalize_asset_number
from insy_sensor_data.maximo.db import MaximoDatabaseError
from insy_sensor_data.maximo.history import load_asset_history
from insy_sensor_data.snapshots.build import SNAPSHOT_FIELDS
from insy_sensor_data.services.trends import (
    AXIS_METRICS,
    NON_AXIS_METRICS,
    load_trend_view,
    metric_field,
    trend_coverage,
)
from insy_sensor_data.store.errors import (
    StoreMigrationRequiredError,
    StoreNotFoundError,
)
from insy_sensor_data.store.events import query_waites_events
from insy_sensor_data.store.models import load_cluster
from insy_sensor_data.store.references import public_scope, resolve_scope
from insy_sensor_data.store.snapshots import load_snapshot_view


SNAPSHOT_REVIEW_BASE_COLUMNS = [
    "sensor_name",
    "installation_point_id",
    "asset_number",
    "rms_vel_mean_x",
    "rms_vel_mean_y",
    "rms_vel_mean_z",
    "rms_accel_mean_x",
    "rms_accel_mean_y",
    "rms_accel_mean_z",
    "impact_mean",
    "temp_sensor_mean",
]


def load_snapshot_review(
    settings: AppSettings,
    *,
    run_date: date,
    source: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    scope: str = "all",
    asset_tree_id: str | None = None,
    equipment_id: str | None = None,
    installation_point_id: str | None = None,
    sensor_id: str | None = None,
    metric: str = "rms_vel",
    dimension: str = "x",
    feature_space: str | None = None,
    stat: str = "mean",
    k: int | None = None,
) -> dict[str, Any]:
    resolved_source = _resolve_source(settings, source)
    effective_start = start_date or run_date
    effective_end = end_date or run_date
    if effective_end < effective_start:
        raise ValueError("end_date must be on or after start_date")
    if run_date < effective_start or run_date > effective_end:
        raise ValueError(
            "snapshot date must be within the selected start_date and end_date range"
        )
    selected_metric = _normalize_metric(metric)
    selected_dimension = _normalize_dimension(selected_metric, dimension)
    selected_stat = _normalize_stat(stat)
    measurement_columns = _measurement_columns(
        selected_metric,
        selected_dimension,
        selected_stat,
    )
    review_snapshot_fields = list(
        dict.fromkeys(
            [
                "installation_point_id",
                "installation_point_name",
                "equipment_id",
                "equipment_name",
                "sensor_id",
                "customer_asset_id",
                *(
                    field
                    for field in measurement_columns
                    if field in SNAPSHOT_FIELDS
                ),
            ]
        )
    )
    snapshot = load_snapshot_view(
        settings,
        run_date=run_date,
        source=resolved_source,
        fields=review_snapshot_fields,
    )
    snapshot_rows = snapshot["rows"]
    scope_context = resolve_scope(
        settings,
        source=resolved_source,
        start_date=effective_start,
        end_date=effective_end,
        scope=scope,
        asset_tree_id=asset_tree_id,
        equipment_id=equipment_id,
        installation_point_id=installation_point_id,
        sensor_id=sensor_id,
    )
    scoped_rows = filter_rows_for_scope(snapshot_rows, scope_context)
    cluster_feature = ACTIVE_MODEL_POLICY.feature_space_for(
        metric=selected_metric,
        dimension=selected_dimension,
        requested=feature_space,
    )
    selected_k = ACTIVE_MODEL_POLICY.validate_k(k)
    return {
        "source": resolved_source,
        "date": run_date.isoformat(),
        "start_date": effective_start.isoformat(),
        "end_date": effective_end.isoformat(),
        "scope": public_scope(scope_context),
        "context": _review_context(scope_context, scoped_rows, snapshot_rows),
        "trend": _review_trend(
            settings,
            effective_start,
            effective_end,
            resolved_source,
            scope_context,
            selected_metric,
            selected_dimension,
            selected_stat,
        ),
        "cluster_context": _review_cluster(
            settings,
            run_date,
            resolved_source,
            selected_metric,
            selected_dimension,
            cluster_feature.name,
            selected_k,
            scope_context,
        ),
        "events": _review_events(
            settings,
            effective_start,
            effective_end,
            resolved_source,
            scope_context,
            snapshot_rows,
        ),
        "measurements": _measurements(
            scoped_rows,
            run_date,
            selected_metric,
            selected_dimension,
            selected_stat,
        ),
        "data_revision": snapshot["data_revision"],
        "metadata": {
            "snapshot_row_count": len(snapshot_rows),
            "filtered_snapshot_row_count": len(scoped_rows),
            "metric": selected_metric,
            "dimension": selected_dimension,
            "stat": selected_stat,
            "cluster_dimension": cluster_feature.dimension,
            "cluster_feature_space": cluster_feature.name,
            "k": selected_k,
            "model_policy_version": ACTIVE_MODEL_POLICY.version,
            "data_revision": snapshot["data_revision"],
        },
    }


def filter_rows_for_scope(
    rows: list[dict[str, Any]],
    scope_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if scope_context["type"] == "all":
        return rows
    return [
        row
        for row in rows
        if _row_matches_scope(row, scope_context)
    ]


def _review_context(
    scope_context: dict[str, Any],
    scoped_rows: list[dict[str, Any]],
    snapshot_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = scoped_rows if scope_context["type"] != "all" else snapshot_rows
    first = rows[0] if rows else {}
    equipment_ids = {
        _text(row.get("equipment_id"))
        for row in rows
        if _text(row.get("equipment_id"))
    }
    sensor_ids = {
        _text(row.get("installation_point_id"))
        for row in rows
        if _text(row.get("installation_point_id"))
    }
    context = {
        "label": scope_context.get("label", "All equipment"),
        "equipment_name": scope_context.get("equipment_name")
        or first.get("equipment_name")
        or "",
        "customer_asset_id": scope_context.get("customer_asset_id")
        or first.get("customer_asset_id")
        or "",
        "sensor_name": scope_context.get("sensor_name")
        or first.get("installation_point_name")
        or "",
        "equipment_count": len(equipment_ids),
        "sensor_count": len(sensor_ids),
        "snapshot_row_count": len(rows),
        "all_snapshot_row_count": len(snapshot_rows),
    }
    if (
        scope_context["type"] == "sensor"
        and not context["sensor_count"]
        and scope_context.get("installation_point_id")
    ):
        context["sensor_count"] = 1
    return context


def _review_trend(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    scope_context: dict[str, Any],
    metric: str,
    dimension: str,
    stat: str,
) -> dict[str, Any]:
    value_field = metric_field(metric, stat, dimension)
    try:
        payload = load_trend_view(
            settings,
            start_date=start_date,
            end_date=end_date,
            source=source,
            scope=scope_context["type"],
            asset_tree_id=scope_context.get("asset_tree_id"),
            equipment_id=scope_context.get("equipment_id"),
            installation_point_id=scope_context.get("installation_point_id"),
            sensor_id=scope_context.get("sensor_id"),
            metric=metric,
            dimension=dimension,
            stat=stat,
            _scope_context=scope_context,
        )
    except StoreNotFoundError as exc:
        return {
            "status": "missing",
            "message": str(exc),
            "value_field": value_field,
            "coverage": trend_coverage([], value_field),
            "sensor_rows": [],
            "series": [],
        }
    return {
        "status": "available",
        "value_field": value_field,
        "row_count": payload["sensor_row_count"],
        "coverage": payload["coverage"],
        "series": payload["series"],
        "series_count": payload["series_count"],
        "sensor_rows": payload["sensor_rows"],
        "detail": payload["detail"],
        "skipped_dates": payload["metadata"]["skipped_dates"],
        "data_revision": payload["data_revision"],
    }


def _review_cluster(
    settings: AppSettings,
    run_date: date,
    source: str,
    metric: str,
    dimension: str,
    feature_space: str,
    k: int,
    scope_context: dict[str, Any],
) -> dict[str, Any]:
    try:
        payload = load_cluster(
            settings,
            run_date=run_date,
            source=source,
            metric=metric,
            dimension=dimension,
            feature_space=feature_space,
            k=k,
        )
    except (StoreNotFoundError, StoreMigrationRequiredError) as exc:
        return {
            "status": "missing",
            "message": str(exc),
            "dimension": dimension,
            "feature_space": feature_space,
            "k": k,
            "points": [],
            "rows": [],
            "selected_ids": sorted(
                scope_context.get("installation_point_ids", set())
            ),
        }
    points = filter_rows_for_scope(payload.get("pca_rows", []), scope_context)
    rows = filter_rows_for_scope(payload.get("rows", []), scope_context)
    return {
        "status": "available",
        "dimension": payload["dimension"],
        "feature_space": payload.get("feature_space"),
        "k": payload["k"],
        "row_count": len(rows),
        "all_row_count": payload["row_count"],
        "points": points,
        "rows": rows,
        "selected_ids": sorted(
            scope_context.get("installation_point_ids", set())
        ),
        "metrics": payload.get("metrics", {}),
        "data_revision": payload["data_revision"],
    }


def _review_events(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    scope_context: dict[str, Any],
    snapshot_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    waites = query_waites_events(
        settings,
        source=source,
        start_date=start_date,
        end_date=end_date,
    )
    maximo = _maximo_events(
        settings,
        start_date,
        end_date,
        source,
        scope_context,
        snapshot_rows,
    )
    events = waites["rows"] + maximo["rows"]
    scoped_events = [
        row for row in events if _event_matches_scope(row, scope_context)
    ]
    return {
        "status": (
            "partial"
            if (
                waites["status"] == "partial"
                or maximo["status"] in {"partial", "unavailable"}
            )
            else "available"
        ),
        "input": "sqlite",
        "row_count": len(scoped_events),
        "all_row_count": len(events),
        "rows": _sort_events(scoped_events),
        "data_revision": waites["data_revision"],
        "providers": {
            "waites": {
                "status": waites["status"],
                "input": "sqlite",
                "row_count": waites["row_count"],
                "coverage": waites["coverage"],
            },
            "maximo": {
                key: value
                for key, value in maximo.items()
                if key != "rows"
            },
        },
    }


def _maximo_events(
    settings: AppSettings,
    start_date: date,
    end_date: date,
    source: str,
    scope_context: dict[str, Any],
    snapshot_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if scope_context["type"] == "all":
        return {
            "status": "not_requested",
            "message": "Select an Asset Tree to load Maximo work orders.",
            "input": None,
            "assetnums": [],
            "row_count": 0,
            "rows": [],
        }
    asset_index = index_snapshot_assets(
        snapshot_rows,
        scope_context.get("activation_equipment_ids", set()),
    )
    if not asset_index:
        return {
            "status": "available",
            "message": (
                "No non-empty customer asset numbers are available "
                "in this Asset Tree."
            ),
            "input": source,
            "assetnums": [],
            "row_count": 0,
            "rows": [],
        }
    try:
        history = load_asset_history(
            settings=settings,
            assetnums=asset_index.keys(),
            start_date=start_date,
            end_date=end_date,
            source=source,
        )
    except (FileNotFoundError, MaximoDatabaseError, ValueError) as exc:
        return {
            "status": "unavailable",
            "message": str(exc),
            "input": source,
            "assetnums": sorted(asset_index),
            "row_count": 0,
            "rows": [],
        }
    rows = [
        _event_from_workorder(row, asset_index)
        for row in history["rows"]
    ]
    return {
        "status": history["status"],
        "message": history.get("message"),
        "input": history["input"],
        "assetnums": history["assetnums"],
        "queried_assetnums": history["queried_assetnums"],
        "skipped_assets": history["skipped_assets"],
        "warning_count": history["warning_count"],
        "row_count": len(rows),
        "rows": rows,
    }


def _event_from_workorder(
    row: dict[str, Any],
    asset_index: dict[str, dict[str, set[str]]],
) -> dict[str, Any]:
    asset_number = normalize_asset_number(row.get("assetnum"))
    matched = asset_index.get(asset_number, {})
    equipment_ids = sorted(
        matched.get("equipment_ids", set()),
        key=_sort_key,
    )
    installation_ids = sorted(
        matched.get("installation_point_ids", set()),
        key=_sort_key,
    )
    return {
        "date": _text(row.get("reportdate")),
        "source": "maximo",
        "status": _text(row.get("status")),
        "type": _text(row.get("worktype")),
        "asset_number": asset_number,
        "installation_point_id": "",
        "installation_point_ids": installation_ids,
        "sensor_name": "",
        "equipment_id": equipment_ids[0] if len(equipment_ids) == 1 else "",
        "equipment_ids": equipment_ids,
        "event_id": _text(row.get("wonum")),
        "work_order": _text(row.get("wonum")),
        "work_order_status": _text(row.get("status")),
        "title": _text(row.get("description")),
        "urgency": "",
        "closed_at": _text(row.get("actfinish")),
    }


def _event_matches_scope(
    row: dict[str, Any],
    scope_context: dict[str, Any],
) -> bool:
    if scope_context["type"] == "all":
        return True
    installation_ids = _event_ids(
        row,
        "installation_point_id",
        "installation_point_ids",
    )
    equipment_ids = _event_ids(row, "equipment_id", "equipment_ids")
    if scope_context["type"] == "sensor":
        return bool(
            installation_ids
            & scope_context.get("installation_point_ids", set())
        )
    if installation_ids & scope_context.get("installation_point_ids", set()):
        return True
    return bool(equipment_ids & scope_context.get("equipment_ids", set()))


def _event_ids(
    row: dict[str, Any],
    singular_key: str,
    plural_key: str,
) -> set[str]:
    values = {_text(row.get(singular_key))}
    plural_values = row.get(plural_key)
    if isinstance(plural_values, list):
        values.update(_text(value) for value in plural_values)
    return {value for value in values if value}


def _sort_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _text(row.get("date")),
            _text(row.get("work_order") or row.get("event_id")),
        ),
        reverse=True,
    )


def _measurements(
    rows: list[dict[str, Any]],
    run_date: date,
    metric: str,
    dimension: str,
    stat: str,
) -> dict[str, Any]:
    columns = _measurement_columns(metric, dimension, stat)
    output = []
    for row in rows:
        measurement = {
            "sensor_name": row.get("installation_point_name")
            or row.get("installation_point_id")
            or "",
            "installation_point_id": row.get("installation_point_id") or "",
            "asset_number": row.get("customer_asset_id") or "",
            "equipment_name": row.get("equipment_name") or "",
        }
        for column in columns:
            if column not in measurement:
                measurement[column] = row.get(column)
        output.append(measurement)
    return {
        "status": "available",
        "snapshot_date": run_date.isoformat(),
        "row_count": len(output),
        "columns": columns,
        "rows": output,
    }


def _measurement_columns(
    metric: str,
    dimension: str,
    stat: str,
) -> list[str]:
    selected = metric_field(metric, stat, dimension)
    context_fields = [metric_field(metric, item, dimension) for item in ["min", "mean", "max"]]
    return list(
        dict.fromkeys(
            [
                *SNAPSHOT_REVIEW_BASE_COLUMNS,
                selected,
                *context_fields,
            ]
        )
    )


def _row_matches_scope(
    row: dict[str, Any],
    scope_context: dict[str, Any],
) -> bool:
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


def _text(value: Any) -> str:
    return "" if value in (None, "") else str(value).strip()


def _sort_key(value: Any) -> tuple[int, Any]:
    text = _text(value)
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)

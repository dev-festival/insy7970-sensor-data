from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo
import json
import os
import secrets
import socket

from insy_sensor_data.clustering.policy import ACTIVE_MODEL_POLICY
from insy_sensor_data.clustering.registry import rebuild_active_model_date
from insy_sensor_data.config import AppSettings
from insy_sensor_data.health import build_health_report
from insy_sensor_data.maximo.db import check_maximo_connection
from insy_sensor_data.observations import (
    connect_observation_store,
    load_ingestion_ledger,
    verify_sensor_daily_snapshot,
)
from insy_sensor_data.raw_lifecycle import apply_retention
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.storage import get_storage_paths
from insy_sensor_data.store.connection import read_store, schema_version, store_path
from insy_sensor_data.store.events import backfill_waites_events
from insy_sensor_data.store.schema import active_snapshot_table
from insy_sensor_data.waites.fetch import fetch_waites, refresh_waites_references
from insy_sensor_data.waites.validate import validate_waites_raw


WRITER_LEASE_DURATION = timedelta(hours=6)
VALID_REBUILD_COMPONENTS = {"snapshots", "events", "models", "all"}


class WriterBusyError(RuntimeError):
    """Raised when another administration writer owns the operational store."""


def source_yesterday(
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> date:
    zone = ZoneInfo(settings.source_timezone)
    instant = now or datetime.now(UTC)
    localized = instant.replace(tzinfo=zone) if instant.tzinfo is None else instant.astimezone(zone)
    return localized.date() - timedelta(days=1)


def run_sync(
    settings: AppSettings,
    *,
    run_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_days: int | None = None,
    defer_models: bool = False,
    tree: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    if tree:
        _validate_tree_selection(
            run_date=run_date,
            start_date=start_date,
            end_date=end_date,
            max_days=max_days,
            defer_models=defer_models,
        )
        return _run_tree_refresh(settings, now=now)

    target_date = source_yesterday(settings, now=now)
    _validate_date_selection(run_date, start_date, end_date, target_date)
    control = _initialize_sync_control(
        settings,
        explicit_start=run_date or start_date,
    )
    if (
        run_date is None
        and start_date is None
        and date.fromisoformat(str(control["start_date"])) > target_date
    ):
        raise ValueError(
            f"Synchronization start date {control['start_date']} is after the latest "
            f"complete source date {target_date.isoformat()}."
        )
    planned = _plan_sync_dates(
        control=control,
        run_date=run_date,
        start_date=start_date,
        end_date=end_date,
        target_date=target_date,
    )
    all_dates = planned
    if max_days is not None:
        if max_days < 1:
            raise ValueError("max_days must be greater than zero")
        planned = planned[:max_days]
    remaining_dates = all_dates[len(planned) :]

    if not planned:
        return {
            "operation": "sync",
            "source": settings.source_mode,
            "status": "current",
            "source_timezone": settings.source_timezone,
            "target_date": target_date.isoformat(),
            "start_date": None,
            "end_date": None,
            "planned_date_count": 0,
            "completed_date_count": 0,
            "remaining_date_count": 0,
            "current_through": control.get("current_through"),
            "dates": [],
        }

    action_id = secrets.token_hex(16)
    with writer_lease(settings, operation="sync") as lease:
        _start_audit(
            settings,
            action_id=action_id,
            operation="sync",
            start_date=planned[0],
            end_date=planned[-1],
        )
        results: list[dict[str, Any]] = []
        try:
            for selected_date in planned:
                result = _sync_one_day(
                    settings,
                    run_date=selected_date,
                    defer_models=defer_models,
                    heartbeat=lease.heartbeat,
                )
                results.append(result)
                _advance_sync_cursor(settings, selected_date)
                lease.heartbeat()
        except Exception as exc:
            summary = {
                "completed_date_count": len(results),
                "failed_date": selected_date.isoformat(),
                "error": str(exc),
            }
            _finish_audit(settings, action_id, status="failed", summary=summary)
            raise

        current = _read_sync_control(settings)
        status = "partial" if remaining_dates else "advanced"
        summary = {
            "operation": "sync",
            "source": settings.source_mode,
            "status": status,
            "source_timezone": settings.source_timezone,
            "target_date": target_date.isoformat(),
            "start_date": planned[0].isoformat(),
            "end_date": planned[-1].isoformat(),
            "planned_date_count": len(planned),
            "completed_date_count": len(results),
            "remaining_date_count": len(remaining_dates),
            "next_date": remaining_dates[0].isoformat() if remaining_dates else None,
            "current_through": current.get("current_through"),
            "models_deferred": defer_models,
            "raw_retention_mode": settings.raw_retention_mode,
            "dates": results,
        }
        _finish_audit(settings, action_id, status=status, summary=summary)
        return summary


def run_rebuild(
    settings: AppSettings,
    *,
    run_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    component: str = "all",
    allow_refetch: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    selected_component = component.strip().lower()
    if selected_component not in VALID_REBUILD_COMPONENTS:
        allowed = ", ".join(sorted(VALID_REBUILD_COMPONENTS))
        raise ValueError(f"component must be one of: {allowed}")
    target_date = source_yesterday(settings, now=now)
    _validate_date_selection(run_date, start_date, end_date, target_date, required=True)
    selected_dates = _explicit_dates(run_date, start_date, end_date)
    action_id = secrets.token_hex(16)
    results: list[dict[str, Any]] = []

    with writer_lease(settings, operation="rebuild") as lease:
        _start_audit(
            settings,
            action_id=action_id,
            operation="rebuild",
            start_date=selected_dates[0],
            end_date=selected_dates[-1],
            component=selected_component,
        )
        try:
            if selected_component in {"snapshots", "all"}:
                for selected_date in selected_dates:
                    _ensure_rebuild_raw(
                        settings,
                        selected_date,
                        allow_refetch=allow_refetch,
                    )
                    validation = validate_waites_raw(
                        settings=settings,
                        run_date=selected_date,
                        source=settings.source_mode,
                    )
                    snapshot = build_sensor_snapshot(
                        settings=settings,
                        run_date=selected_date,
                        source=settings.source_mode,
                    )
                    retention = apply_retention(
                        settings=settings,
                        run_date=selected_date,
                        source=settings.source_mode,
                        snapshot_summary=snapshot,
                        raw_retention=settings.raw_retention_mode,
                    )
                    results.append(
                        {
                            "date": selected_date.isoformat(),
                            "component": "snapshots",
                            "validation_status": validation["status"],
                            "snapshot_row_count": snapshot["record_count"],
                            "retention_status": retention["raw_retention_status"],
                        }
                    )
                    lease.heartbeat()

            if selected_component in {"events", "all"}:
                events = backfill_waites_events(
                    settings,
                    source=settings.source_mode,
                    start_date=selected_dates[0],
                    end_date=selected_dates[-1],
                )
                if events["refetch_required_dates"]:
                    if not allow_refetch:
                        dates = ", ".join(events["refetch_required_dates"])
                        raise ValueError(
                            "Retained event evidence is absent for dates "
                            f"{dates}; rerun with --allow-refetch."
                        )
                    refetched_dates = list(events["refetch_required_dates"])
                    for raw_date in refetched_dates:
                        selected_date = date.fromisoformat(raw_date)
                        fetch_waites(
                            settings=settings,
                            run_date=selected_date,
                            facility_id=settings.waites_facility_id,
                            source=settings.source_mode,
                        )
                        validate_waites_raw(
                            settings=settings,
                            run_date=selected_date,
                            source=settings.source_mode,
                        )
                    events = backfill_waites_events(
                        settings,
                        source=settings.source_mode,
                        start_date=selected_dates[0],
                        end_date=selected_dates[-1],
                    )
                    for raw_date in refetched_dates:
                        selected_date = date.fromisoformat(raw_date)
                        verification = verify_sensor_daily_snapshot(
                            settings,
                            selected_date,
                            settings.source_mode,
                        )
                        if verification["status"] == "valid":
                            apply_retention(
                                settings=settings,
                                run_date=selected_date,
                                source=settings.source_mode,
                                snapshot_summary={"record_count": verification["row_count"]},
                                raw_retention=settings.raw_retention_mode,
                            )
                results.append({"component": "events", **events})
                lease.heartbeat()

            if selected_component in {"models", "all"}:
                for selected_date in selected_dates:
                    models = rebuild_active_model_date(
                        settings,
                        run_date=selected_date,
                        source=settings.source_mode,
                        force=True,
                    )
                    results.append(
                        {
                            "date": selected_date.isoformat(),
                            "component": "models",
                            "model_counts": models["model_counts"],
                            "drift_counts": models["drift_counts"],
                            "readiness": models["readiness"],
                        }
                    )
                    lease.heartbeat()
        except Exception as exc:
            _finish_audit(
                settings,
                action_id,
                status="failed",
                summary={"result_count": len(results), "error": str(exc)},
            )
            raise

        summary = {
            "operation": "rebuild",
            "source": settings.source_mode,
            "status": "complete",
            "component": selected_component,
            "start_date": selected_dates[0].isoformat(),
            "end_date": selected_dates[-1].isoformat(),
            "allow_refetch": allow_refetch,
            "results": results,
        }
        _finish_audit(settings, action_id, status="complete", summary=summary)
        return summary


def build_doctor_report(
    settings: AppSettings,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    check_maximo: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    target_date = source_yesterday(settings, now=now)
    path = store_path(settings)
    base = build_health_report(settings)
    if not path.is_file():
        return {
            **base,
            "operation": "doctor",
            "status": "error",
            "target_date": target_date.isoformat(),
            "store": {"status": "missing", "path": path.as_posix()},
            "synchronization": {
                **base["synchronization"],
                "current_through": None,
                "snapshot_gaps": [],
                "event_gaps": [],
                "model_gaps": [],
            },
        }

    with read_store(settings) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        version = schema_version(connection)
        table = active_snapshot_table(connection)
        control = None
        lease = None
        incomplete_runs: list[dict[str, Any]] = []
        if "sync_control" in tables:
            row = connection.execute(
                "SELECT * FROM sync_control WHERE source = ?",
                (settings.source_mode,),
            ).fetchone()
            control = dict(row) if row else None
        if "admin_writer_lease" in tables:
            row = connection.execute(
                "SELECT operation, process_id, host_name, acquired_at, heartbeat_at, expires_at "
                "FROM admin_writer_lease WHERE lease_id = 1"
            ).fetchone()
            lease = dict(row) if row else None
        if "sync_date_runs" in tables:
            incomplete_runs = [
                dict(row)
                for row in connection.execute(
                    "SELECT source_date, status, data_status, model_status, "
                    "retention_status, updated_at, error FROM sync_date_runs "
                    "WHERE source = ? AND status NOT IN ('complete', 'deferred') "
                    "ORDER BY source_date",
                    (settings.source_mode,),
                ).fetchall()
            ]

        ledger_dates = [
            str(row["source_date"])
            for row in connection.execute(
                "SELECT source_date FROM waites_ingestion_ledger "
                "WHERE source = ? ORDER BY source_date",
                (settings.source_mode,),
            ).fetchall()
        ] if "waites_ingestion_ledger" in tables else []
        lower = start_date or (
            date.fromisoformat(str(control["start_date"]))
            if control
            else (date.fromisoformat(ledger_dates[0]) if ledger_dates else None)
        )
        upper = end_date or target_date
        if lower is not None and upper < lower:
            raise ValueError("end_date must be on or after start_date")
        expected = _date_range(lower, upper) if lower is not None else []
        expected_values = {value.isoformat() for value in expected}

        snapshot_dates = {
            str(row["source_date"])
            for row in connection.execute(
                f"SELECT DISTINCT source_date FROM {table} WHERE source = ?",
                (settings.source_mode,),
            ).fetchall()
        }
        snapshot_gaps = sorted(expected_values - snapshot_dates)

        event_gaps: list[str] = []
        if "waites_event_coverage" in tables:
            event_ready = {
                str(row["source_date"])
                for row in connection.execute(
                    "SELECT source_date FROM waites_event_coverage "
                    "WHERE source = ? AND state IN ('imported', 'genuinely_empty')",
                    (settings.source_mode,),
                ).fetchall()
            }
            event_gaps = sorted(expected_values - event_ready)
        elif expected:
            event_gaps = sorted(expected_values)

        model_gaps: list[dict[str, str]] = []
        stale_models: list[dict[str, str]] = []
        if "cluster_model_runs" in tables and "snapshot_revisions" in tables:
            model_rows = connection.execute(
                """
                SELECT run.source_date, run.feature_space, run.status,
                       run.model_policy_version, run.input_snapshot_revision,
                       snapshot.snapshot_revision
                FROM cluster_model_runs AS run
                LEFT JOIN snapshot_revisions AS snapshot
                  ON snapshot.source = run.source
                 AND snapshot.source_date = run.source_date
                WHERE run.source = ? AND run.k = ?
                """,
                (settings.source_mode, ACTIVE_MODEL_POLICY.k),
            ).fetchall()
            models = {
                (str(row["source_date"]), str(row["feature_space"])): row
                for row in model_rows
            }
            for source_date in sorted(expected_values - set(snapshot_gaps)):
                for spec in ACTIVE_MODEL_POLICY.feature_spaces:
                    model = models.get((source_date, spec.name))
                    if model is None or str(model["status"]) != "complete":
                        model_gaps.append(
                            {"date": source_date, "feature_space": spec.name}
                        )
                    elif (
                        model["model_policy_version"] != ACTIVE_MODEL_POLICY.version
                        or model["input_snapshot_revision"] != model["snapshot_revision"]
                    ):
                        stale_models.append(
                            {"date": source_date, "feature_space": spec.name}
                        )
        elif expected_values - set(snapshot_gaps):
            for source_date in sorted(expected_values - set(snapshot_gaps)):
                for spec in ACTIVE_MODEL_POLICY.feature_spaces:
                    model_gaps.append({"date": source_date, "feature_space": spec.name})

    current_through = control.get("current_through") if control else None
    issues = (
        len(snapshot_gaps)
        + len(event_gaps)
        + len(model_gaps)
        + len(stale_models)
        + len(incomplete_runs)
        + (1 if version < 9 else 0)
    )
    return {
        **base,
        "operation": "doctor",
        "status": "ok" if issues == 0 else "warning",
        "target_date": target_date.isoformat(),
        "range": {
            "start_date": lower.isoformat() if lower else None,
            "end_date": upper.isoformat(),
        },
        "store": {
            "status": "ready" if version >= 9 else "migration_required",
            "path": path.as_posix(),
            "schema_version": version,
            "database_bytes": path.stat().st_size,
        },
        "synchronization": {
            **base["synchronization"],
            "current_through": current_through,
            "is_current": bool(current_through and current_through >= target_date.isoformat()),
            "snapshot_gaps": snapshot_gaps,
            "event_gaps": event_gaps,
            "model_gaps": model_gaps,
            "stale_models": stale_models,
            "incomplete_runs": incomplete_runs,
            "writer": lease,
            "issue_count": issues,
        },
        "maximo_connectivity": (
            check_maximo_connection(settings)
            if check_maximo
            else {
                "status": "not_checked",
                "reason": "Use --check-maximo for one bounded read-only connectivity check.",
                "timeout_seconds": settings.maximo_query_timeout_seconds,
            }
        ),
    }


class _Lease:
    def __init__(self, settings: AppSettings, token: str) -> None:
        self.settings = settings
        self.token = token

    def heartbeat(self) -> None:
        now = datetime.now(UTC)
        with connect_observation_store(self.settings) as connection:
            cursor = connection.execute(
                "UPDATE admin_writer_lease SET heartbeat_at = ?, expires_at = ? "
                "WHERE lease_id = 1 AND owner_token = ?",
                (now.isoformat(), (now + WRITER_LEASE_DURATION).isoformat(), self.token),
            )
            connection.commit()
        if cursor.rowcount != 1:
            raise WriterBusyError("Administration writer ownership was lost.")


@contextmanager
def writer_lease(settings: AppSettings, *, operation: str) -> Iterator[_Lease]:
    token = secrets.token_hex(16)
    now = datetime.now(UTC)
    with connect_observation_store(settings) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT * FROM admin_writer_lease WHERE lease_id = 1"
        ).fetchone()
        if current is not None and str(current["expires_at"]) > now.isoformat():
            connection.rollback()
            raise WriterBusyError(
                "Another administration writer is active: "
                f"{current['operation']} on {current['host_name']} "
                f"(process {current['process_id']}, heartbeat {current['heartbeat_at']})."
            )
        connection.execute("DELETE FROM admin_writer_lease WHERE lease_id = 1")
        connection.execute(
            """
            INSERT INTO admin_writer_lease (
                lease_id, owner_token, operation, process_id, host_name,
                acquired_at, heartbeat_at, expires_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                operation,
                os.getpid(),
                socket.gethostname(),
                now.isoformat(),
                now.isoformat(),
                (now + WRITER_LEASE_DURATION).isoformat(),
            ),
        )
        connection.commit()
    lease = _Lease(settings, token)
    try:
        yield lease
    finally:
        with connect_observation_store(settings) as connection:
            connection.execute(
                "DELETE FROM admin_writer_lease WHERE lease_id = 1 AND owner_token = ?",
                (token,),
            )
            connection.commit()


def _sync_one_day(
    settings: AppSettings,
    *,
    run_date: date,
    defer_models: bool,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    state = _start_sync_date(settings, run_date)
    data_action = "reused"
    model_action = "deferred" if defer_models else "reused"
    retention_action = "reused"
    data_status = "pending"
    model_status = "deferred" if defer_models else "pending"
    retention_status = "pending"
    try:
        verification = verify_sensor_daily_snapshot(
            settings=settings,
            run_date=run_date,
            source=settings.source_mode,
        )
        data_ready = verification["error_count"] == 0 and verification["row_count"] > 0
        if not data_ready:
            validation = _validated_raw_or_fetch(settings, run_date)
            snapshot = build_sensor_snapshot(
                settings=settings,
                run_date=run_date,
                source=settings.source_mode,
            )
            verification = snapshot["snapshot_store"]
            data_action = "built"
            data_status = "ready"
            _record_sync_stage(
                settings,
                run_date,
                status="running",
                data_status="ready",
                model_status="pending",
                retention_status="pending",
            )
            if heartbeat is not None:
                heartbeat()
        else:
            validation = None
            data_status = "ready"
            _record_sync_stage(
                settings,
                run_date,
                status="running",
                data_status="ready",
                model_status=state.get("model_status", "pending"),
                retention_status=state.get("retention_status", "pending"),
            )

        if defer_models:
            model_status = "deferred"
        else:
            models = rebuild_active_model_date(
                settings,
                run_date=run_date,
                source=settings.source_mode,
                force=False,
            )
            readiness = models["readiness"]
            if readiness["status"] != "ready":
                raise RuntimeError(
                    f"Active models are {readiness['status']} for {run_date.isoformat()}."
                )
            model_action = (
                "built" if models["model_counts"].get("built", 0) else "reused"
            )
            model_status = "ready"
            _record_sync_stage(
                settings,
                run_date,
                status="running",
                data_status="ready",
                model_status="ready",
                retention_status="pending",
            )
            if heartbeat is not None:
                heartbeat()

        ledger = load_ingestion_ledger(settings, run_date, settings.source_mode)
        if not _retention_satisfied(ledger, settings.raw_retention_mode):
            snapshot_summary = {
                "record_count": int(verification["row_count"]),
            }
            retention = apply_retention(
                settings=settings,
                run_date=run_date,
                source=settings.source_mode,
                snapshot_summary=snapshot_summary,
                raw_retention=settings.raw_retention_mode,
            )
            retention_action = "applied"
            retention_status = retention["raw_retention_status"]
        else:
            retention_status = str(ledger["raw_retention_status"])

        if heartbeat is not None:
            heartbeat()

        final_status = "deferred" if defer_models else "complete"
        _record_sync_stage(
            settings,
            run_date,
            status=final_status,
            data_status="ready",
            model_status=model_status,
            retention_status=retention_status,
        )
        return {
            "date": run_date.isoformat(),
            "status": final_status,
            "data": data_action,
            "models": model_action,
            "retention": retention_action,
            "snapshot_row_count": int(verification["row_count"]),
            "validation_status": validation["status"] if validation else "reused",
        }
    except Exception as exc:
        _record_sync_stage(
            settings,
            run_date,
            status="failed",
            data_status=data_status,
            model_status=model_status,
            retention_status=retention_status,
            error=str(exc),
        )
        raise


def _run_tree_refresh(
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    action_id = secrets.token_hex(16)
    with writer_lease(settings, operation="sync --tree") as lease:
        _start_audit(
            settings,
            action_id=action_id,
            operation="sync",
            start_date=None,
            end_date=None,
            component="tree",
        )
        try:
            summary = refresh_waites_references(
                settings,
                source=settings.source_mode,
                source_date=source_yesterday(settings, now=now),
            )
            lease.heartbeat()
            result = {
                "operation": "sync",
                "mode": "tree",
                "source": settings.source_mode,
                "status": "complete",
                **summary,
            }
            _finish_audit(settings, action_id, status="complete", summary=result)
            return result
        except Exception as exc:
            _finish_audit(
                settings,
                action_id,
                status="failed",
                summary={"operation": "sync", "mode": "tree", "error": str(exc)},
            )
            raise


def _validated_raw_or_fetch(settings: AppSettings, run_date: date) -> dict[str, Any]:
    raw_dir = get_storage_paths(settings.data_dir).raw_waites_run_dir(run_date.isoformat())
    if raw_dir.joinpath("manifest.json").is_file():
        try:
            report = validate_waites_raw(
                settings=settings,
                run_date=run_date,
                source=settings.source_mode,
            )
            if report["status"] in {"valid", "valid_with_warnings"}:
                return report
        except (FileNotFoundError, ValueError):
            pass
    fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=settings.waites_facility_id,
        source=settings.source_mode,
    )
    report = validate_waites_raw(
        settings=settings,
        run_date=run_date,
        source=settings.source_mode,
    )
    if report["status"] not in {"valid", "valid_with_warnings"}:
        raise ValueError(f"Waites validation failed for {run_date.isoformat()}.")
    return report


def _ensure_rebuild_raw(
    settings: AppSettings,
    run_date: date,
    *,
    allow_refetch: bool,
) -> None:
    raw_dir = get_storage_paths(settings.data_dir).raw_waites_run_dir(run_date.isoformat())
    if raw_dir.joinpath("manifest.json").is_file():
        try:
            report = validate_waites_raw(settings, run_date, settings.source_mode)
            if report["status"] in {"valid", "valid_with_warnings"}:
                return
        except (FileNotFoundError, ValueError):
            pass
    if not allow_refetch:
        raise ValueError(
            f"Retained raw evidence is absent for {run_date.isoformat()}; "
            "rerun with --allow-refetch."
        )
    fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=settings.waites_facility_id,
        source=settings.source_mode,
    )


def _initialize_sync_control(
    settings: AppSettings,
    *,
    explicit_start: date | None,
) -> dict[str, Any]:
    with connect_observation_store(settings) as connection:
        row = connection.execute(
            "SELECT * FROM sync_control WHERE source = ?",
            (settings.source_mode,),
        ).fetchone()
        if row is not None:
            if str(row["source_timezone"]) != settings.source_timezone:
                raise ValueError(
                    "Configured source timezone differs from the persisted sync timezone "
                    f"{row['source_timezone']!r}; reconcile it before synchronizing."
                )
            return dict(row)

        ledger_dates = [
            date.fromisoformat(str(item["source_date"]))
            for item in connection.execute(
                "SELECT source_date FROM waites_ingestion_ledger "
                "WHERE source = ? AND ingestion_state = 'complete' ORDER BY source_date",
                (settings.source_mode,),
            ).fetchall()
        ]
        configured_start = settings.sync_start_date or explicit_start
        if configured_start is None and not ledger_dates:
            raise ValueError(
                "A fresh store needs INSY_SYNC_START_DATE or an explicit --date/--start-date."
            )
        start_date = configured_start or ledger_dates[0]
        complete_dates = set(ledger_dates)
        cursor = start_date
        if cursor not in complete_dates:
            current_through = None
        else:
            while cursor + timedelta(days=1) in complete_dates:
                cursor += timedelta(days=1)
            current_through = cursor.isoformat()
        start_value = start_date.isoformat()
        now = datetime.now(UTC).isoformat()
        connection.execute(
            "INSERT INTO sync_control (source, start_date, current_through, "
            "source_timezone, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                settings.source_mode,
                start_value,
                current_through,
                settings.source_timezone,
                now,
            ),
        )
        connection.commit()
        return {
            "source": settings.source_mode,
            "start_date": start_value,
            "current_through": current_through,
            "source_timezone": settings.source_timezone,
            "updated_at": now,
        }


def _read_sync_control(settings: AppSettings) -> dict[str, Any]:
    with read_store(settings, required_tables=("sync_control",)) as connection:
        row = connection.execute(
            "SELECT * FROM sync_control WHERE source = ?",
            (settings.source_mode,),
        ).fetchone()
    return dict(row) if row else {}


def _plan_sync_dates(
    *,
    control: dict[str, Any],
    run_date: date | None,
    start_date: date | None,
    end_date: date | None,
    target_date: date,
) -> list[date]:
    if run_date is not None or start_date is not None or end_date is not None:
        return _explicit_dates(run_date, start_date, end_date)
    current = control.get("current_through")
    lower = (
        date.fromisoformat(str(current)) + timedelta(days=1)
        if current
        else date.fromisoformat(str(control["start_date"]))
    )
    return _date_range(lower, target_date)


def _advance_sync_cursor(settings: AppSettings, run_date: date) -> None:
    with connect_observation_store(settings) as connection:
        control = connection.execute(
            "SELECT start_date, current_through FROM sync_control WHERE source = ?",
            (settings.source_mode,),
        ).fetchone()
        if control is None:
            raise RuntimeError("Synchronization control state is missing.")
        current = date.fromisoformat(str(control["current_through"])) if control["current_through"] else None
        expected = current + timedelta(days=1) if current else date.fromisoformat(str(control["start_date"]))
        if run_date == expected:
            connection.execute(
                "UPDATE sync_control SET current_through = ?, updated_at = ? WHERE source = ?",
                (run_date.isoformat(), datetime.now(UTC).isoformat(), settings.source_mode),
            )
            connection.commit()


def _start_sync_date(settings: AppSettings, run_date: date) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    with connect_observation_store(settings) as connection:
        existing = connection.execute(
            "SELECT * FROM sync_date_runs WHERE source = ? AND source_date = ?",
            (settings.source_mode, run_date.isoformat()),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO sync_date_runs (
                source, source_date, facility_id, status, data_status,
                model_status, retention_status, attempt_count, started_at,
                updated_at, completed_at, error
            ) VALUES (?, ?, ?, 'running', 'pending', 'pending', 'pending', 1, ?, ?, NULL, NULL)
            ON CONFLICT(source, source_date) DO UPDATE SET
                status = 'running',
                attempt_count = sync_date_runs.attempt_count + 1,
                updated_at = excluded.updated_at,
                completed_at = NULL,
                error = NULL
            """,
            (
                settings.source_mode,
                run_date.isoformat(),
                settings.waites_facility_id,
                now,
                now,
            ),
        )
        connection.commit()
    return dict(existing) if existing else {}


def _record_sync_stage(
    settings: AppSettings,
    run_date: date,
    *,
    status: str,
    data_status: str,
    model_status: str,
    retention_status: str,
    error: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    with connect_observation_store(settings) as connection:
        connection.execute(
            """
            UPDATE sync_date_runs
            SET status = ?, data_status = ?, model_status = ?, retention_status = ?,
                updated_at = ?, completed_at = ?, error = ?
            WHERE source = ? AND source_date = ?
            """,
            (
                status,
                data_status,
                model_status,
                retention_status,
                now,
                now if status in {"complete", "deferred"} else None,
                error,
                settings.source_mode,
                run_date.isoformat(),
            ),
        )
        connection.commit()


def _retention_satisfied(
    ledger: dict[str, Any] | None,
    requested: str,
) -> bool:
    if ledger is None:
        return False
    raw_status = str(ledger.get("raw_retention_status") or "")
    if requested == "keep":
        return raw_status in {"kept", "compressed", "released"}
    if requested == "compress":
        return raw_status in {"compressed", "released"}
    return raw_status == "released"


def _validate_date_selection(
    run_date: date | None,
    start_date: date | None,
    end_date: date | None,
    target_date: date,
    *,
    required: bool = False,
) -> None:
    if run_date is not None and (start_date is not None or end_date is not None):
        raise ValueError("Use either --date or --start-date/--end-date, not both.")
    if (start_date is None) != (end_date is None):
        raise ValueError("--start-date and --end-date must be supplied together.")
    if required and run_date is None and start_date is None:
        raise ValueError("Select --date or --start-date/--end-date.")
    selected_end = run_date or end_date
    if selected_end is not None and selected_end > target_date:
        raise ValueError(
            f"Synchronization is limited through {target_date.isoformat()} "
            "in the configured source timezone."
        )
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date must be on or after start_date")


def _validate_tree_selection(
    *,
    run_date: date | None,
    start_date: date | None,
    end_date: date | None,
    max_days: int | None,
    defer_models: bool,
) -> None:
    selected = []
    if run_date is not None:
        selected.append("--date")
    if start_date is not None:
        selected.append("--start-date")
    if end_date is not None:
        selected.append("--end-date")
    if max_days is not None:
        selected.append("--max-days")
    if defer_models:
        selected.append("--defer-models")
    if selected:
        joined = ", ".join(selected)
        raise ValueError(f"--tree cannot be combined with {joined}; run it by itself.")


def _explicit_dates(
    run_date: date | None,
    start_date: date | None,
    end_date: date | None,
) -> list[date]:
    if run_date is not None:
        return [run_date]
    if start_date is None or end_date is None:
        raise ValueError("Select a date or inclusive range.")
    return _date_range(start_date, end_date)


def _date_range(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


def _start_audit(
    settings: AppSettings,
    *,
    action_id: str,
    operation: str,
    start_date: date | None,
    end_date: date | None,
    component: str | None = None,
) -> None:
    with connect_observation_store(settings) as connection:
        connection.execute(
            "INSERT INTO admin_action_audit (action_id, operation, source, start_date, "
            "end_date, component, status, started_at) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
            (
                action_id,
                operation,
                settings.source_mode,
                start_date.isoformat() if start_date else None,
                end_date.isoformat() if end_date else None,
                component,
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()


def _finish_audit(
    settings: AppSettings,
    action_id: str,
    *,
    status: str,
    summary: dict[str, Any],
) -> None:
    with connect_observation_store(settings) as connection:
        connection.execute(
            "UPDATE admin_action_audit SET status = ?, completed_at = ?, summary_json = ? "
            "WHERE action_id = ?",
            (
                status,
                datetime.now(UTC).isoformat(),
                json.dumps(summary, sort_keys=True, default=str),
                action_id,
            ),
        )
        connection.commit()

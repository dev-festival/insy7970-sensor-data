from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

import insy_sensor_data.admin as admin
from insy_sensor_data.admin import (
    WriterBusyError,
    build_doctor_report,
    run_rebuild,
    run_sync,
    source_yesterday,
    writer_lease,
)
from insy_sensor_data.config import AppSettings
from insy_sensor_data.store.exports import export_snapshot_csv


RUN_NOW = datetime(2025, 7, 10, 12, tzinfo=UTC)
RUN_DATE = date(2025, 7, 9)


def test_source_yesterday_uses_configured_timezone_across_dst(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", source_timezone="America/Chicago")

    assert source_yesterday(
        settings,
        now=datetime(2026, 3, 9, 5, 30, tzinfo=UTC),
    ) == date(2026, 3, 8)
    assert source_yesterday(
        settings,
        now=datetime(2026, 11, 2, 6, 30, tzinfo=UTC),
    ) == date(2026, 11, 1)


def test_sync_builds_complete_day_and_bare_sync_is_non_mutating_noop(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)

    first = run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)

    assert first["status"] == "advanced"
    assert first["current_through"] == "2025-07-09"
    assert first["dates"][0]["snapshot_row_count"] == 9
    assert first["dates"][0]["models"] == "built"
    assert first["dates"][0]["retention"] == "applied"
    database = settings.data_dir / "processed" / "observations.sqlite"
    before_hash = sha256(database.read_bytes()).hexdigest()
    with sqlite3.connect(database) as connection:
        before_audits = connection.execute(
            "SELECT COUNT(*) FROM admin_action_audit"
        ).fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM cluster_model_runs").fetchone()[0] == 4
        assert connection.execute(
            "SELECT status FROM sync_date_runs WHERE source_date = '2025-07-09'"
        ).fetchone()[0] == "complete"

    second = run_sync(settings, now=RUN_NOW)

    assert second["status"] == "current"
    assert second["planned_date_count"] == 0
    assert sha256(database.read_bytes()).hexdigest() == before_hash
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM admin_action_audit"
        ).fetchone()[0] == before_audits


def test_sync_tree_refreshes_references_without_daily_sync_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        admin,
        "refresh_waites_references",
        lambda *_args, **_kwargs: {
            "status": "complete",
            "source": "mock",
            "facility_id": 679,
            "source_date": "2025-07-09",
            "row_counts": {
                "asset_trees": 2,
                "equipment": 6,
                "installation_points": 8,
            },
        },
    )

    result = run_sync(settings, tree=True, now=RUN_NOW)

    assert result["mode"] == "tree"
    assert result["status"] == "complete"
    with sqlite3.connect(settings.data_dir / "processed" / "observations.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM sync_control").fetchone()[0] == 0
        audit = connection.execute(
            "SELECT component, status FROM admin_action_audit"
        ).fetchone()
        assert audit == ("tree", "complete")


def test_sync_tree_rejects_daily_options(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(ValueError, match="--tree cannot be combined with --date"):
        run_sync(settings, tree=True, run_date=RUN_DATE, now=RUN_NOW)


def test_sync_requires_start_boundary_and_rejects_current_date(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(ValueError, match="INSY_SYNC_START_DATE"):
        run_sync(settings, now=RUN_NOW)
    with pytest.raises(ValueError, match="limited through 2025-07-09"):
        run_sync(settings, run_date=date(2025, 7, 10), now=RUN_NOW)

    future_start = _settings(tmp_path / "future", sync_start_date=date(2025, 7, 10))
    with pytest.raises(ValueError, match="start date 2025-07-10 is after"):
        run_sync(future_start, now=RUN_NOW)


def test_sync_bounds_backlog_and_resumes_next_invocation(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    now = datetime(2025, 7, 12, 12, tzinfo=UTC)

    first = run_sync(settings, max_days=2, now=now)

    assert first["status"] == "partial"
    assert first["completed_date_count"] == 2
    assert first["remaining_date_count"] == 1
    assert first["current_through"] == "2025-07-10"

    second = run_sync(settings, now=now)

    assert second["status"] == "advanced"
    assert second["start_date"] == "2025-07-11"
    assert second["current_through"] == "2025-07-11"


def test_sync_resumes_after_model_failure_without_refetching_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    real_rebuild = admin.rebuild_active_model_date

    def fail_models(*args, **kwargs):
        raise RuntimeError("injected model failure")

    monkeypatch.setattr(admin, "rebuild_active_model_date", fail_models)
    with pytest.raises(RuntimeError, match="injected model failure"):
        run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)

    failed_report = build_doctor_report(settings, now=RUN_NOW)
    assert failed_report["status"] == "warning"
    assert failed_report["synchronization"]["incomplete_runs"][0]["status"] == "failed"
    assert len(failed_report["synchronization"]["model_gaps"]) == 4

    monkeypatch.setattr(admin, "rebuild_active_model_date", real_rebuild)

    def reject_fetch(*args, **kwargs):
        raise AssertionError("verified daily facts must prevent a refetch")

    monkeypatch.setattr(admin, "fetch_waites", reject_fetch)
    resumed = run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)

    assert resumed["dates"][0]["data"] == "reused"
    assert resumed["dates"][0]["models"] == "built"
    with sqlite3.connect(settings.data_dir / "processed" / "observations.sqlite") as connection:
        attempt_count = connection.execute(
            "SELECT attempt_count FROM sync_date_runs WHERE source_date = '2025-07-09'"
        ).fetchone()[0]
    assert attempt_count == 2


def test_writer_lease_rejects_overlap_and_releases_cleanly(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with writer_lease(settings, operation="sync"):
        with pytest.raises(WriterBusyError, match="Another administration writer"):
            with writer_lease(settings, operation="rebuild"):
                pass

    with writer_lease(settings, operation="rebuild"):
        pass


def test_writer_lease_recovers_expired_owner(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with writer_lease(settings, operation="sync"):
        pass
    database = settings.data_dir / "processed" / "observations.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO admin_writer_lease (
                lease_id, owner_token, operation, process_id, host_name,
                acquired_at, heartbeat_at, expires_at
            ) VALUES (1, 'expired', 'sync', 1, 'old-host', ?, ?, ?)
            """,
            (
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T01:00:00+00:00",
            ),
        )
        connection.commit()

    with writer_lease(settings, operation="rebuild"):
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT operation FROM admin_writer_lease WHERE lease_id = 1"
            ).fetchone()[0] == "rebuild"


def test_model_rebuild_uses_durable_snapshot_after_raw_release(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    synced = run_sync(
        settings,
        run_date=RUN_DATE,
        defer_models=True,
        now=RUN_NOW,
    )
    assert synced["dates"][0]["models"] == "deferred"
    assert not (
        settings.data_dir / "raw" / "waites" / "date=2025-07-09" / "equipment.json"
    ).exists()

    rebuilt = run_rebuild(
        settings,
        run_date=RUN_DATE,
        component="models",
        now=RUN_NOW,
    )

    assert rebuilt["status"] == "complete"
    assert rebuilt["results"][0]["readiness"]["status"] == "ready"


def test_snapshot_rebuild_requires_explicit_refetch_after_release(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)

    with pytest.raises(ValueError, match="--allow-refetch"):
        run_rebuild(
            settings,
            run_date=RUN_DATE,
            component="snapshots",
            now=RUN_NOW,
        )

    rebuilt = run_rebuild(
        settings,
        run_date=RUN_DATE,
        component="snapshots",
        allow_refetch=True,
        now=RUN_NOW,
    )
    assert rebuilt["results"][0]["snapshot_row_count"] == 9
    assert rebuilt["results"][0]["retention_status"] == "released"


def test_doctor_reports_healthy_current_store(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)

    report = build_doctor_report(settings, now=RUN_NOW)

    assert report["status"] == "ok"
    assert report["store"]["schema_version"] == 10
    assert report["synchronization"]["is_current"] is True
    assert report["synchronization"]["snapshot_gaps"] == []
    assert report["synchronization"]["event_gaps"] == []
    assert report["synchronization"]["model_gaps"] == []
    assert report["synchronization"]["writer"] is None


def test_doctor_reports_event_gap_and_stale_models(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)
    database = settings.data_dir / "processed" / "observations.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM waites_event_coverage WHERE source = 'mock' AND source_date = '2025-07-09'"
        )
        connection.execute(
            "UPDATE cluster_model_runs SET input_snapshot_revision = 'stale' "
            "WHERE source = 'mock' AND source_date = '2025-07-09'"
        )
        connection.commit()

    report = build_doctor_report(settings, now=RUN_NOW)

    assert report["status"] == "warning"
    assert report["synchronization"]["event_gaps"] == ["2025-07-09"]
    assert len(report["synchronization"]["stale_models"]) == 4


def test_doctor_maximo_check_is_explicit_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)
    monkeypatch.setattr(
        admin,
        "check_maximo_connection",
        lambda _settings: {"status": "available", "elapsed_ms": 2.5},
    )

    default = build_doctor_report(settings, now=RUN_NOW)
    checked = build_doctor_report(settings, now=RUN_NOW, check_maximo=True)

    assert default["maximo_connectivity"]["status"] == "not_checked"
    assert checked["maximo_connectivity"] == {"status": "available", "elapsed_ms": 2.5}


def test_export_rejects_destination_inside_operational_data(tmp_path: Path) -> None:
    settings = _settings(tmp_path, sync_start_date=RUN_DATE)
    run_sync(settings, run_date=RUN_DATE, now=RUN_NOW)

    with pytest.raises(ValueError, match="outside the configured operational data"):
        export_snapshot_csv(
            settings,
            run_date=RUN_DATE,
            source="mock",
            destination=settings.data_dir / "snapshot.csv",
        )


def _settings(tmp_path: Path, *, sync_start_date: date | None = None) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        source_mode="mock",
        source_timezone="America/Chicago",
        sync_start_date=sync_start_date,
        raw_retention_mode="release",
    )

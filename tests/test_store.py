from __future__ import annotations

from datetime import date
from pathlib import Path
import importlib.util
import json
import sqlite3

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.clustering.registry import build_cluster_model_grid
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store
from insy_sensor_data.raw_lifecycle import apply_retention
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.store.events import (
    backfill_waites_events,
    query_waites_events,
    upsert_waites_events,
)
from insy_sensor_data.store.references import list_equipment_tree
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.waites.validate import validate_waites_raw


def test_store_errors_are_visible_for_absent_corrupt_and_partial_schemas(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "missing")
    missing = TestClient(create_app(settings=settings)).get("/api/context")
    assert missing.status_code == 404
    assert "Operational SQLite store is missing" in missing.json()["detail"]

    corrupt_settings = AppSettings(data_dir=tmp_path / "corrupt")
    corrupt_path = corrupt_settings.data_dir / "processed" / "observations.sqlite"
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_text("not a sqlite database", encoding="utf-8")
    corrupt = TestClient(create_app(settings=corrupt_settings)).get("/api/context")
    assert corrupt.status_code == 503
    assert "corrupt" in corrupt.json()["detail"].lower()

    partial_settings = AppSettings(data_dir=tmp_path / "partial")
    partial_path = partial_settings.data_dir / "processed" / "observations.sqlite"
    partial_path.parent.mkdir(parents=True)
    with sqlite3.connect(partial_path) as connection:
        connection.executescript(
            """
            CREATE TABLE observation_schema (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE sensor_daily_snapshots (
                source TEXT NOT NULL,
                source_date TEXT NOT NULL,
                installation_point_id TEXT NOT NULL,
                built_at TEXT NOT NULL,
                snapshot_csv_path TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                PRIMARY KEY (source, source_date, installation_point_id)
            );
            CREATE TABLE waites_ingestion_ledger (
                source TEXT,
                source_date TEXT,
                updated_at TEXT
            );
            """
        )
    partial = TestClient(create_app(settings=partial_settings)).get(
        "/api/snapshots/2025-07-09"
    )
    assert partial.status_code == 409
    assert "missing columns" in partial.json()["detail"]


def test_waites_event_identity_deduplicates_and_keeps_latest_provider_state(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    with connect_observation_store(settings) as connection:
        upsert_waites_events(
            connection,
            source="mock",
            source_date="2025-07-09",
            observed_at="2025-07-09T12:00:00Z",
            rows=[
                {
                    "action_item_id": "event-1",
                    "status": "open",
                    "title": "Inspect bearing",
                    "installation_point_id": "201300",
                }
            ],
        )
        upsert_waites_events(
            connection,
            source="mock",
            source_date="2025-07-10",
            observed_at="2025-07-10T12:00:00Z",
            rows=[
                {
                    "action_item_id": "event-1",
                    "status": "closed",
                    "title": "Inspect bearing",
                    "installation_point_id": "201300",
                    "closed_at": "2025-07-10T11:00:00Z",
                }
            ],
        )
        upsert_waites_events(
            connection,
            source="mock",
            source_date="2025-07-09",
            observed_at="2025-07-11T12:00:00Z",
            rows=[
                {
                    "action_item_id": "event-1",
                    "status": "open",
                    "title": "Stale replay",
                    "installation_point_id": "201300",
                }
            ],
        )
        upsert_waites_events(
            connection,
            source="mock",
            source_date="2025-07-09",
            observed_at="2025-07-09T12:00:00Z",
            rows=[
                {
                    "created_at": "2025-07-09T08:00:00Z",
                    "sensor_id": "derived-sensor",
                    "type": "inspection",
                    "status": "open",
                    "title": "Initial derived title",
                }
            ],
        )
        upsert_waites_events(
            connection,
            source="mock",
            source_date="2025-07-10",
            observed_at="2025-07-10T12:00:00Z",
            rows=[
                {
                    "created_at": "2025-07-09T08:00:00Z",
                    "sensor_id": "derived-sensor",
                    "type": "inspection",
                    "status": "closed",
                    "title": "Revised derived title",
                }
            ],
        )
        connection.commit()

    payload = query_waites_events(
        settings,
        source="mock",
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 10),
    )

    assert payload["row_count"] == 2
    by_id = {row["event_id"]: row for row in payload["rows"]}
    assert by_id["event-1"]["first_seen_date"] == "2025-07-09"
    assert by_id["event-1"]["last_seen_date"] == "2025-07-10"
    assert by_id["event-1"]["status"] == "closed"
    assert by_id["event-1"]["title"] == "Inspect bearing"
    derived = next(
        row for event_id, row in by_id.items() if event_id.startswith("derived-v1-")
    )
    assert derived["status"] == "closed"
    assert derived["title"] == "Revised derived title"


def test_event_backfill_distinguishes_import_refetch_and_empty_dates(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    _prepare_day(settings, date(2025, 7, 9), "release")
    _prepare_day(settings, date(2025, 7, 10), "release")
    _prepare_day(settings, date(2025, 7, 11), "keep")
    with connect_observation_store(settings) as connection:
        connection.execute("DELETE FROM waites_events")
        ledger = connection.execute(
            """
            SELECT endpoint_counts_json
            FROM waites_ingestion_ledger
            WHERE source = ? AND source_date = ?
            """,
            ("mock", "2025-07-10"),
        ).fetchone()
        counts = json.loads(ledger["endpoint_counts_json"])
        counts["action-items"] = 0
        connection.execute(
            """
            UPDATE waites_ingestion_ledger
            SET endpoint_counts_json = ?
            WHERE source = ? AND source_date = ?
            """,
            (json.dumps(counts, sort_keys=True), "mock", "2025-07-10"),
        )
        connection.commit()

    report = backfill_waites_events(settings, source="mock")

    assert report["imported_dates"] == ["2025-07-11"]
    assert report["refetch_required_dates"] == ["2025-07-09"]
    assert report["genuinely_empty_dates"] == ["2025-07-10"]
    assert report["imported_event_count"] == 4
    events = query_waites_events(
        settings,
        source="mock",
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )
    assert events["status"] == "partial"
    assert events["coverage"]["incomplete_dates"] == ["2025-07-09"]


def test_equipment_tree_repository_uses_direct_store_references(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    _prepare_operational_window(settings)

    operational = list_equipment_tree(
        settings,
        source="mock",
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )

    assert operational["asset_tree_count"] == 4
    assert operational["equipment_count"] == 7
    assert operational["sensor_count"] == 9


def test_equipment_tree_prefers_current_reference_name_over_daily_fact_name(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    _prepare_operational_window(settings)
    with connect_observation_store(settings) as connection:
        connection.execute(
            "UPDATE waites_equipment_reference SET name = ? "
            "WHERE source = 'mock' AND equipment_id = 55576",
            ("Current - Aluminium Pinch Roll",),
        )
        connection.commit()

    operational = list_equipment_tree(
        settings,
        source="mock",
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )
    equipment = next(
        equipment
        for tree in operational["asset_trees"]
        for equipment in tree["equipment"]
        if equipment["equipment_id"] == "55576"
    )

    assert equipment["equipment_name"] == "Current - Aluminium Pinch Roll"


def test_operational_routes_do_not_read_legacy_artifacts(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    _prepare_operational_window(settings)
    build_cluster_model_grid(
        settings=settings,
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
        source="mock",
        feature_spaces=["x_accel"],
        ks=[5],
    )

    assert importlib.util.find_spec("insy_sensor_data.artifact_views") is None
    assert importlib.util.find_spec("insy_sensor_data.reports") is None
    assert importlib.util.find_spec("insy_sensor_data.workflows") is None
    import insy_sensor_data.clustering.registry as registry

    assert not hasattr(registry, "read_csv_rows")

    client = TestClient(create_app(settings=settings))
    paths = [
        "/api/context",
        "/api/equipment-tree?start_date=2025-07-09&end_date=2025-07-11",
        "/api/snapshots/2025-07-09",
        "/api/trends?start_date=2025-07-09&end_date=2025-07-11",
        (
            "/api/snapshot-review/2025-07-09?start_date=2025-07-09"
            "&end_date=2025-07-11"
            "&metric=rms_accel&dimension=x"
        ),
        "/api/cluster-models?start_date=2025-07-09&end_date=2025-07-11",
        "/api/clusters?date=2025-07-09&dimension=x",
        (
            "/api/drift?from_date=2025-07-09&to_date=2025-07-10&dimension=x"
        ),
        (
            "/api/cluster-windows?start_date=2025-07-09"
            "&end_date=2025-07-11&dimension=x"
        ),
    ]

    assert [(path, client.get(path).status_code) for path in paths] == [
        (path, 200) for path in paths
    ]


def test_operational_scope_queries_use_covering_indexes(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    _prepare_day(settings, date(2025, 7, 9), "keep")
    with connect_observation_store(settings) as connection:
        equipment_plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT installation_point_id
            FROM sensor_daily_facts
            WHERE source = ?
              AND source_date >= ?
              AND source_date <= ?
              AND equipment_id = ?
            """,
            ("mock", "2025-07-09", "2025-07-09", "55576"),
        ).fetchall()
        sensor_plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT rms_vel_mean_x
            FROM sensor_daily_facts
            WHERE source = ?
              AND source_date >= ?
              AND source_date <= ?
              AND installation_point_id = ?
            """,
            ("mock", "2025-07-09", "2025-07-09", "201300"),
        ).fetchall()
        event_plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT provider_event_id
            FROM waites_events
            WHERE source = ?
              AND first_seen_date <= ?
              AND last_seen_date >= ?
              AND equipment_id = ?
            """,
            ("mock", "2025-07-09", "2025-07-09", "55576"),
        ).fetchall()

    assert "idx_sensor_daily_facts_equipment_scope" in _plan_text(equipment_plan)
    assert "idx_sensor_daily_facts_installation_scope" in _plan_text(sensor_plan)
    assert "idx_waites_events_equipment_scope" in _plan_text(event_plan)


def _prepare_operational_window(settings: AppSettings) -> None:
    for run_date in [
        date(2025, 7, 9),
        date(2025, 7, 10),
        date(2025, 7, 11),
    ]:
        _prepare_day(settings, run_date, "keep")


def _prepare_day(settings: AppSettings, run_date: date, retention: str) -> None:
    fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=settings.waites_facility_id,
        source=settings.source_mode,
    )
    validate_waites_raw(settings=settings, run_date=run_date, source=settings.source_mode)
    snapshot = build_sensor_snapshot(
        settings=settings,
        run_date=run_date,
        source=settings.source_mode,
    )
    apply_retention(
        settings=settings,
        run_date=run_date,
        source=settings.source_mode,
        snapshot_summary=snapshot,
        raw_retention=retention,
    )


def _plan_text(rows: list[sqlite3.Row]) -> str:
    return " ".join(str(value) for row in rows for value in row)

from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import sqlite3

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.artifact_views import list_equipment_tree_view
from insy_sensor_data.clustering.registry import build_cluster_model_grid
from insy_sensor_data.config import AppSettings
from insy_sensor_data.observations import connect_observation_store
from insy_sensor_data.store.events import (
    backfill_waites_events,
    query_waites_events,
    upsert_waites_events,
)
from insy_sensor_data.store.references import list_equipment_tree
from insy_sensor_data.workflows import run_mock_day_workflow


def test_store_errors_are_visible_for_absent_corrupt_and_partial_schemas(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "missing")
    missing = TestClient(create_app(settings=settings)).get("/api/artifacts")
    assert missing.status_code == 404
    assert "Operational SQLite store is missing" in missing.json()["detail"]

    corrupt_settings = AppSettings(data_dir=tmp_path / "corrupt")
    corrupt_path = corrupt_settings.data_dir / "processed" / "observations.sqlite"
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_text("not a sqlite database", encoding="utf-8")
    corrupt = TestClient(create_app(settings=corrupt_settings)).get("/api/artifacts")
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
        "/api/snapshots/2025-07-09?source=mock"
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
    run_mock_day_workflow(
        settings=settings,
        run_date=date(2025, 7, 9),
        raw_retention="release",
    )
    run_mock_day_workflow(
        settings=settings,
        run_date=date(2025, 7, 10),
        raw_retention="release",
    )
    run_mock_day_workflow(
        settings=settings,
        run_date=date(2025, 7, 11),
        raw_retention="keep",
    )
    with connect_observation_store(settings) as connection:
        connection.execute("DELETE FROM waites_events")
        connection.execute(
            "DELETE FROM waites_action_items WHERE source_date IN (?, ?)",
            ("2025-07-09", "2025-07-10"),
        )
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


def test_equipment_tree_repository_matches_legacy_semantics(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    _prepare_operational_window(settings)

    legacy = list_equipment_tree_view(
        settings,
        source="mock",
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )
    operational = list_equipment_tree(
        settings,
        source="mock",
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )

    assert operational["asset_tree_count"] == legacy["asset_tree_count"]
    assert operational["equipment_count"] == legacy["equipment_count"]
    assert operational["sensor_count"] == legacy["sensor_count"]
    assert [
        (row["asset_tree_id"], row["asset_tree_name"], row["sensor_count"])
        for row in operational["asset_trees"]
    ] == [
        (row["asset_tree_id"], row["asset_tree_name"], row["sensor_count"])
        for row in legacy["asset_trees"]
    ]


def test_operational_routes_do_not_read_legacy_artifacts(
    tmp_path: Path,
    monkeypatch,
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

    def artifact_read_forbidden(*_args, **_kwargs):
        raise AssertionError("normal FastAPI path opened a legacy artifact")

    monkeypatch.setattr(
        "insy_sensor_data.artifact_views.read_csv_rows",
        artifact_read_forbidden,
    )
    monkeypatch.setattr(
        "insy_sensor_data.artifact_views.read_json",
        artifact_read_forbidden,
    )
    monkeypatch.setattr(
        "insy_sensor_data.snapshots.build.load_snapshot",
        artifact_read_forbidden,
        raising=False,
    )
    monkeypatch.setattr(
        "insy_sensor_data.snapshots.trends.load_trends",
        artifact_read_forbidden,
    )
    monkeypatch.setattr(
        "insy_sensor_data.clustering.registry.read_csv_rows",
        artifact_read_forbidden,
    )

    client = TestClient(create_app(settings=settings))
    paths = [
        "/api/artifacts",
        "/api/equipment-tree?source=mock&start_date=2025-07-09&end_date=2025-07-11",
        "/api/snapshots/2025-07-09?source=mock",
        "/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11",
        (
            "/api/snapshot-review/2025-07-09?source=mock"
            "&start_date=2025-07-09&end_date=2025-07-11"
            "&feature_space=x_accel&k=5"
        ),
        "/api/cluster-models?source=mock&start_date=2025-07-09&end_date=2025-07-11",
        "/api/clusters?source=mock&date=2025-07-09&feature_space=x_accel&k=5",
        (
            "/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10"
            "&feature_space=x_accel&k=5"
        ),
        (
            "/api/cluster-windows?source=mock&start_date=2025-07-09"
            "&end_date=2025-07-11&feature_space=x_accel&k=5"
        ),
    ]

    assert [(path, client.get(path).status_code) for path in paths] == [
        (path, 200) for path in paths
    ]


def test_operational_scope_queries_use_covering_indexes(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_mock_day_workflow(
        settings=settings,
        run_date=date(2025, 7, 9),
        raw_retention="keep",
    )
    with connect_observation_store(settings) as connection:
        equipment_plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT installation_point_id
            FROM sensor_daily_snapshots
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
            FROM sensor_daily_snapshots
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

    assert "idx_sensor_daily_snapshots_equipment_scope" in _plan_text(equipment_plan)
    assert "idx_sensor_daily_snapshots_installation_scope" in _plan_text(sensor_plan)
    assert "idx_waites_events_equipment_scope" in _plan_text(event_plan)


def _prepare_operational_window(settings: AppSettings) -> None:
    for run_date in [
        date(2025, 7, 9),
        date(2025, 7, 10),
        date(2025, 7, 11),
    ]:
        run_mock_day_workflow(
            settings=settings,
            run_date=run_date,
            raw_retention="keep",
        )


def _plan_text(rows: list[sqlite3.Row]) -> str:
    return " ".join(str(value) for row in rows for value in row)

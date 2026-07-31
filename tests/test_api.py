from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from insy_sensor_data.api.main import create_app
from insy_sensor_data.clustering.registry import build_cluster_model_grid
from insy_sensor_data.clustering.window import build_cluster_window
from insy_sensor_data.config import AppSettings
from insy_sensor_data.maximo.db import MaximoDatabaseError
from insy_sensor_data.observations import (
    connect_observation_store,
    load_waites_observations,
)
from insy_sensor_data.services.trends import (
    trend_coverage as _trend_coverage,
    trend_series as _trend_series,
)
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.workflows import run_mock_day_workflow


def test_health_endpoint_returns_shared_health_payload(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", source_mode="mock")
    client = TestClient(create_app(settings=settings))

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["source_mode"] == "mock"
    assert payload["data_dir"] == str(tmp_path / "data")


def test_root_serves_static_shell(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    response = client.get("/")

    assert response.status_code == 200
    assert "INSY Sensor Data" in response.text
    assert 'id="source-select"' in response.text
    assert 'class="global-context"' in response.text
    assert 'id="equipment-search"' in response.text
    assert 'id="equipment-tree"' in response.text
    assert 'id="scope-status"' in response.text
    assert 'id="metric-select"' in response.text
    assert 'id="snapshot-review"' in response.text
    assert 'id="view-pinned"' in response.text
    assert 'id="snapshot-context"' in response.text
    assert 'id="snapshot-scroll"' in response.text
    assert response.text.index('id="snapshot-context"') < response.text.index('class="view-controls"')
    assert 'class="snapshot-chart-grid"' in response.text
    assert 'id="snapshot-trend-chart"' in response.text
    assert 'id="snapshot-cluster-chart"' in response.text
    assert 'id="snapshot-events-detail"' in response.text
    assert 'id="snapshot-measurements-detail"' in response.text
    assert 'id="metric-coverage"' in response.text
    assert 'id="snapshot-diagnostics-head"' in response.text
    assert 'id="snapshot-diagnostics-body"' in response.text
    assert 'data-view="cluster"' in response.text
    assert "/static/charts.js" in response.text
    assert "plotly" not in response.text.lower()

    chart_response = client.get("/static/charts.js")
    assert chart_response.status_code == 200
    assert "window.SensorCharts" in chart_response.text


def test_waites_raw_runs_endpoint_lists_available_manifests(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    empty_response = client.get("/api/waites/raw-runs")
    assert empty_response.status_code == 200
    assert empty_response.json() == {"runs": [], "count": 0}

    fetch_waites(settings=settings, run_date=date(2025, 7, 9), facility_id=679)
    response = client.get("/api/waites/raw-runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["runs"][0]["date"] == "2025-07-09"
    assert payload["runs"][0]["endpoint_count"] == 7
    assert payload["runs"][0]["record_counts"]["asset-tree"] == 2
    assert payload["runs"][0]["record_counts"]["readings-rms"] == 21


def test_snapshot_and_trend_endpoints_read_processed_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    run_date = date(2025, 7, 9)

    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=run_date, end_date=run_date)

    dates_response = client.get("/api/dates")
    assert dates_response.status_code == 200
    dates_payload = dates_response.json()
    assert dates_payload["raw_waites"] == ["2025-07-09"]
    assert dates_payload["snapshots"] == ["2025-07-09"]
    assert dates_payload["trends"] == [{"start_date": "2025-07-09", "end_date": "2025-07-09"}]

    snapshot_response = client.get("/api/snapshots/2025-07-09")
    assert snapshot_response.status_code == 200
    snapshot_payload = snapshot_response.json()
    assert snapshot_payload["metadata"]["record_count"] == 9
    assert len(snapshot_payload["rows"]) == 9

    trend_response = client.get("/api/trends?start_date=2025-07-09&end_date=2025-07-09")
    assert trend_response.status_code == 200
    trend_payload = trend_response.json()
    assert trend_payload["input"] == "sqlite"
    assert len(trend_payload["sensor_rows"]) == 9
    assert trend_payload["metadata"]["equipment_record_count"] >= 1


def test_trend_endpoint_reads_multi_day_sqlite_snapshots_without_artifact(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    for run_date in [date(2025, 7, 9), date(2025, 7, 10), date(2025, 7, 11)]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)

    response = client.get("/api/trends?start_date=2025-07-09&end_date=2025-07-11")

    assert response.status_code == 200
    payload = response.json()
    assert payload["input"] == "sqlite"
    assert payload["metadata"]["sensor_record_count"] == 27
    assert payload["metadata"]["skipped_dates"] == []
    assert {row["date"] for row in payload["sensor_rows"]} == {
        "2025-07-09",
        "2025-07-10",
        "2025-07-11",
    }
    assert not (
        tmp_path
        / "data"
        / "processed"
        / "trends"
        / "start=2025-07-09_end=2025-07-11"
        / "sensor_trends.csv"
    ).exists()


def test_trend_endpoint_reports_missing_store_facts_without_artifact_fallback(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    start = date(2025, 7, 9)
    end = date(2025, 7, 11)
    for run_date in [start, date(2025, 7, 10), end]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=start, end_date=end)
    with connect_observation_store(settings) as connection:
        connection.execute("DELETE FROM sensor_daily_facts")
        connection.commit()

    response = client.get("/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11")

    assert response.status_code == 404
    assert "Snapshots are unavailable" in response.json()["detail"]


def test_snapshot_endpoint_returns_404_for_missing_artifact(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    response = client.get("/api/snapshots/2025-07-09")

    assert response.status_code == 404


def test_artifact_and_equipment_endpoints_discover_processed_outputs(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    artifacts_response = client.get("/api/artifacts")
    assert artifacts_response.status_code == 200
    artifacts = artifacts_response.json()
    assert artifacts["counts"]["snapshots"] == 3
    assert artifacts["counts"]["trends"] == 1
    assert artifacts["counts"]["clusters"] == 0
    assert artifacts["counts"]["drift"] == 0
    assert artifacts["counts"]["cluster_windows"] == 0
    assert artifacts["sources"] == ["mock"]
    assert artifacts["dimensions"] == []
    assert artifacts["ks"] == []
    assert artifacts["data_revisions"]["mock"]["store"] == "sqlite"
    assert artifacts["latest_readiness"]["mock"] == {
        "snapshot_date": "2025-07-11",
        "registered_model_date": None,
        "fully_ready_date": None,
    }
    assert [
        (row["date"], row["snapshot_ready"], row["registered_model_ready"])
        for row in artifacts["readiness"]
    ] == [
        ("2025-07-09", True, False),
        ("2025-07-10", True, False),
        ("2025-07-11", True, False),
    ]

    equipment_response = client.get("/api/equipment?source=mock")
    assert equipment_response.status_code == 200
    equipment = equipment_response.json()
    assert equipment["count"] >= 1
    assert equipment["rows"][0]["sensor_count"] >= 1
    assert "dates" in equipment["rows"][0]

    tree_response = client.get("/api/equipment-tree?source=mock")
    assert tree_response.status_code == 200
    tree = tree_response.json()
    assert tree["asset_tree_count"] >= 1
    assert tree["equipment_count"] >= 1
    assert tree["sensor_count"] >= 1
    blanking_line = next(row for row in tree["asset_trees"] if row["asset_tree_id"] == "12440")
    assert blanking_line["asset_tree_name"] == "Blanking Line"
    assert blanking_line["equipment"][0]["sensors"][0]["installation_point_id"]

    ranged_response = client.get(
        "/api/equipment?source=mock&start_date=2025-07-10&end_date=2025-07-10"
    )
    assert ranged_response.status_code == 200
    ranged = ranged_response.json()
    assert ranged["start_date"] == "2025-07-10"
    assert ranged["end_date"] == "2025-07-10"
    assert all(row["dates"] == ["2025-07-10"] for row in ranged["rows"])

    ranged_tree_response = client.get(
        "/api/equipment-tree?source=mock&start_date=2025-07-10&end_date=2025-07-10"
    )
    assert ranged_tree_response.status_code == 200
    ranged_tree = ranged_tree_response.json()
    assert ranged_tree["start_date"] == "2025-07-10"
    assert ranged_tree["end_date"] == "2025-07-10"
    assert all(row["active_dates"] == ["2025-07-10"] for row in ranged_tree["asset_trees"])


def test_equipment_endpoint_validates_date_range(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    bad_date = client.get("/api/equipment?source=mock&start_date=bad")
    assert bad_date.status_code == 422

    bad_tree_date = client.get("/api/equipment-tree?source=mock&start_date=bad")
    assert bad_tree_date.status_code == 422

    reversed_range = client.get(
        "/api/equipment?source=mock&start_date=2025-07-11&end_date=2025-07-09"
    )
    assert reversed_range.status_code == 422


def test_cluster_drift_and_window_endpoints_read_processed_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    cluster_response = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=x&k=4")
    assert cluster_response.status_code == 409
    assert "registered feature_space" in cluster_response.json()["detail"]

    drift_response = client.get(
        "/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10&dimension=x&k=4"
    )
    assert drift_response.status_code == 409

    window_response = client.get(
        "/api/cluster-windows?source=mock&start_date=2025-07-09&end_date=2025-07-11&dimension=x&k=4"
    )
    assert window_response.status_code == 409

    build_cluster_model_grid(
        settings=settings,
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
        source="mock",
        feature_spaces=["x_accel"],
        ks=[5],
    )

    models_response = client.get(
        "/api/cluster-models?source=mock&start_date=2025-07-09&end_date=2025-07-11"
    )
    assert models_response.status_code == 200
    models = models_response.json()
    assert models["complete_count"] == 3
    assert models["feature_spaces"] == ["x_accel"]
    assert models["ks"] == [5]
    readiness = client.get("/api/artifacts").json()
    assert readiness["latest_readiness"]["mock"] == {
        "snapshot_date": "2025-07-11",
        "registered_model_date": "2025-07-11",
        "fully_ready_date": "2025-07-11",
    }
    assert all(
        row["snapshot_ready"] and row["registered_model_ready"]
        for row in readiness["readiness"]
        if row["source"] == "mock"
    )

    registered_cluster_response = client.get(
        "/api/clusters?source=mock&date=2025-07-09&feature_space=x_accel&k=5"
    )
    assert registered_cluster_response.status_code == 200
    registered_cluster = registered_cluster_response.json()
    assert registered_cluster["registered"] is True
    assert registered_cluster["feature_space"] == "x_accel"
    assert registered_cluster["metrics"]["feature_count"] == 4

    registered_window_response = client.get(
        "/api/cluster-windows?source=mock&start_date=2025-07-09&end_date=2025-07-11&feature_space=x_accel&k=5"
    )
    assert registered_window_response.status_code == 200
    registered_window = registered_window_response.json()
    assert registered_window["registered"] is True
    assert registered_window["metrics"]["pair_count"] == 2
    assert len(registered_window["quality_rows"]) == 3

    registered_drift_response = client.get(
        "/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10&feature_space=x_accel&k=5"
    )
    assert registered_drift_response.status_code == 200
    registered_drift = registered_drift_response.json()
    assert registered_drift["registered"] is True
    assert registered_drift["aligned_metrics"]["matched_sensor_count"] == 9


def test_snapshot_review_endpoint_composes_sensor_scope(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=sensor&installation_point_id=201300&metric=rms_vel&dimension=x&k=4"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["type"] == "sensor"
    assert payload["scope"]["installation_point_id"] == "201300"
    assert payload["context"]["snapshot_row_count"] == 1
    assert payload["context"]["sensor_count"] == 1
    assert payload["trend"]["status"] == "available"
    assert payload["trend"]["row_count"] == 3
    assert {row["installation_point_id"] for row in payload["trend"]["sensor_rows"]} == {"201300"}
    assert payload["cluster_context"]["status"] == "missing"
    assert payload["events"]["status"] == "available"
    assert payload["events"]["row_count"] == 1
    assert payload["events"]["rows"][0]["event_id"] == "9001"
    assert payload["measurements"]["row_count"] == 1
    assert "rms_vel_mean_x" in payload["measurements"]["columns"]
    assert payload["measurements"]["snapshot_date"] == "2025-07-09"


def test_snapshot_review_reports_selected_field_coverage_and_diagnostics(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    response = client.get(
        "/api/snapshot-review/2025-07-11"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=sensor&installation_point_id=201305&metric=rms_vel&dimension=x"
    )

    assert response.status_code == 200
    trend = response.json()["trend"]
    assert trend["value_field"] == "rms_vel_mean_x"
    assert trend["row_count"] == 3
    assert trend["coverage"] == {
        "value_field": "rms_vel_mean_x",
        "expected_value_count": 3,
        "observed_value_count": 2,
        "coverage_percent": 66.7,
        "sensors": [
            {
                "installation_point_id": "201305",
                "sensor_name": "Motor - NDE",
                "expected_value_count": 3,
                "observed_value_count": 2,
                "coverage_percent": 66.7,
                "missing_dates": ["2025-07-10"],
            }
        ],
    }


def test_snapshot_review_coverage_counts_zero_as_an_observation() -> None:
    coverage = _trend_coverage(
        [
            {"date": "2025-07-09", "installation_point_id": "sensor-a", "rms_vel_mean_x": 0},
            {"date": "2025-07-10", "installation_point_id": "sensor-a", "rms_vel_mean_x": ""},
        ],
        "rms_vel_mean_x",
    )

    assert coverage["observed_value_count"] == 1
    assert coverage["coverage_percent"] == 50.0
    assert coverage["sensors"][0]["missing_dates"] == ["2025-07-10"]


def test_all_equipment_series_preserves_unassigned_equipment_bucket() -> None:
    series = _trend_series(
        [
            {"date": "2025-07-09", "equipment_id": "10", "rms_vel_mean_x": 1.0},
            {"date": "2025-07-09", "equipment_id": "10", "rms_vel_mean_x": 3.0},
            {"date": "2025-07-09", "equipment_id": "20", "rms_vel_mean_x": 8.0},
            {"date": "2025-07-09", "equipment_id": "", "rms_vel_mean_x": 20.0},
        ],
        {"type": "all"},
        "rms_vel_mean_x",
    )

    assert series[0]["aggregation"] == "mean_of_equipment_means"
    assert series[0]["rows"] == [
        {"date": "2025-07-09", "rms_vel_mean_x": 10.0}
    ]


def test_snapshot_review_rejects_a_snapshot_date_outside_the_selected_range(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    response = client.get(
        "/api/snapshot-review/2025-07-11?source=mock&start_date=2025-07-09&end_date=2025-07-10"
    )

    assert response.status_code == 422
    assert "snapshot date must be within" in response.json()["detail"]


def test_snapshot_review_endpoint_composes_equipment_scope(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=equipment&equipment_id=55576&metric=rms_accel&dimension=x&k=4"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["type"] == "equipment"
    assert payload["scope"]["equipment_id"] == "55576"
    assert payload["context"]["snapshot_row_count"] == 2
    assert payload["context"]["sensor_count"] == 2
    assert payload["trend"]["row_count"] == 6
    assert payload["cluster_context"]["status"] == "missing"
    assert {row["event_id"] for row in payload["events"]["rows"]} == {"9001", "9002"}


def test_snapshot_review_maximo_events_activate_at_asset_tree_scope(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    all_response = client.get(
        "/api/snapshot-review/2025-07-09?source=mock&start_date=2025-07-09&end_date=2025-07-11"
    )
    assert all_response.status_code == 200
    assert all_response.json()["events"]["providers"]["maximo"]["status"] == "not_requested"
    assert all(row["source"] != "maximo" for row in all_response.json()["events"]["rows"])

    tree_response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=asset_tree&asset_tree_id=12440"
    )
    assert tree_response.status_code == 200
    tree_events = tree_response.json()["events"]
    assert tree_events["providers"]["maximo"]["status"] == "available"
    assert tree_events["providers"]["maximo"]["assetnums"] == [
        "HYDF128PX",
        "LEVF412TS",
        "LEVF454TS",
    ]
    assert {row["event_id"] for row in tree_events["rows"]} == {"9001", "9002", "1234570"}
    assert next(row for row in tree_events["rows"] if row["source"] == "maximo") == {
        "date": "2025-07-10",
        "source": "maximo",
        "status": "APPR",
        "type": "CM",
        "asset_number": "LEVF454TS",
        "installation_point_id": "",
        "installation_point_ids": ["201302", "201303"],
        "sensor_name": "",
        "equipment_id": "55577",
        "equipment_ids": ["55577"],
        "event_id": "1234570",
        "work_order": "1234570",
        "work_order_status": "APPR",
        "title": "Open inspection for steel pinch roll",
        "urgency": "",
        "closed_at": "",
    }

    sensor_response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=sensor&asset_tree_id=12440&equipment_id=55577&installation_point_id=201303"
    )
    assert sensor_response.status_code == 200
    assert {row["event_id"] for row in sensor_response.json()["events"]["rows"]} == {"1234570"}


def test_snapshot_review_keeps_waites_events_when_maximo_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    def unavailable(*_args, **_kwargs):
        raise MaximoDatabaseError("test DB2 outage")

    monkeypatch.setattr("insy_sensor_data.services.review.load_asset_history", unavailable)
    response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=asset_tree&asset_tree_id=12440"
    )

    assert response.status_code == 200
    events = response.json()["events"]
    assert events["status"] == "partial"
    assert events["providers"]["maximo"]["status"] == "unavailable"
    assert {row["event_id"] for row in events["rows"]} == {"9001", "9002"}


def test_maximo_asset_history_endpoint_returns_mock_records(tmp_path: Path) -> None:
    client = TestClient(create_app(settings=AppSettings(data_dir=tmp_path / "data")))

    response = client.get(
        "/api/maximo/asset-history?assetnum=LEVF412TS&start_date=2025-06-01&end_date=2025-07-09&source=mock"
    )

    assert response.status_code == 200
    assert response.json()["rows"][0]["wonum"] == "1234567"

    invalid_response = client.get(
        "/api/maximo/asset-history?assetnum=LEVF412TS&start_date=not-a-date&end_date=2025-07-09"
    )
    assert invalid_response.status_code == 422


def test_snapshot_review_endpoint_handles_missing_trend_and_cluster_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    load_waites_observations(
        settings=settings,
        run_date=run_date,
        source="mock",
    )
    build_sensor_snapshot(settings=settings, run_date=run_date)

    response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&scope=sensor&installation_point_id=201300"
        "&feature_space=x_accel&k=5"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["snapshot_row_count"] == 1
    assert payload["trend"]["status"] == "available"
    assert payload["trend"]["row_count"] == 1
    assert payload["cluster_context"]["status"] == "missing"
    assert payload["events"]["row_count"] == 1
    assert payload["measurements"]["row_count"] == 1


def test_cluster_endpoint_validates_parameters_and_requires_registered_model(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    bad_dimension = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=q&k=4")
    assert bad_dimension.status_code == 422

    bad_k = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=x&k=0")
    assert bad_k.status_code == 422

    missing = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=x&k=4")
    assert missing.status_code == 409


def test_snapshot_and_trend_endpoints_support_source_and_sensor_filters(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    snapshot_response = client.get(
        "/api/snapshots/2025-07-09?source=mock&installation_point_id=201300"
    )
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["row_count"] == 9
    assert snapshot["filtered_row_count"] == 1
    assert snapshot["rows"][0]["installation_point_id"] == "201300"

    trend_response = client.get(
        "/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11&installation_point_id=201300"
    )
    assert trend_response.status_code == 200
    trend = trend_response.json()
    assert trend["sensor_row_count"] == 3
    assert trend["filtered_sensor_row_count"] == 3
    assert {row["installation_point_id"] for row in trend["sensor_rows"]} == {"201300"}
    assert trend["series_count"] == 1
    assert trend["detail"]["truncated"] is False

    scoped_response = client.get(
        "/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&scope=asset_tree&asset_tree_id=12440&metric=rms_accel&dimension=x&stat=mean"
    )
    assert scoped_response.status_code == 200
    scoped = scoped_response.json()
    assert scoped["scope"]["type"] == "asset_tree"
    assert scoped["value_field"] == "rms_accel_mean_x"
    assert 0 < scoped["sensor_row_count"] < 27
    assert scoped["filtered_sensor_row_count"] == scoped["sensor_row_count"]
    assert scoped["series"][0]["aggregation"] == "mean_of_equipment_means"


@pytest.mark.parametrize(
    ("scope_query", "scope_type", "expected_row_count", "aggregation"),
    [
        ("", "all", 27, "mean_of_equipment_means"),
        ("&scope=asset_tree&asset_tree_id=12440", "asset_tree", 15, "mean_of_equipment_means"),
        ("&scope=equipment&equipment_id=55576", "equipment", 6, "sensor"),
        (
            "&scope=sensor&installation_point_id=201300",
            "sensor",
            3,
            "sensor",
        ),
    ],
)
def test_trend_endpoint_projects_and_bounds_each_scope(
    tmp_path: Path,
    scope_query: str,
    scope_type: str,
    expected_row_count: int,
    aggregation: str,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)

    response = client.get(
        "/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-11"
        "&metric=rms_accel&dimension=x&limit=2"
        f"{scope_query}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["type"] == scope_type
    assert payload["sensor_row_count"] == expected_row_count
    assert payload["detail"] == {
        "limit": 2,
        "offset": 0,
        "row_count": 2,
        "total_row_count": expected_row_count,
        "truncated": True,
    }
    assert len(payload["sensor_rows"]) == 2
    assert payload["series"][0]["aggregation"] == aggregation
    assert set(payload["sensor_rows"][0]) == {
        "date",
        "installation_point_id",
        "installation_point_name",
        "equipment_id",
        "equipment_name",
        "sensor_id",
        "customer_asset_id",
        "rms_accel_mean_x",
        "rms_accel_max_x",
        "rms_accel_min_x",
    }
    assert "equipment_rows" not in payload


def test_trend_endpoint_validates_detail_bounds(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    assert client.get(
        "/api/trends?start_date=2025-07-09&end_date=2025-07-11&limit=0"
    ).status_code == 422
    assert client.get(
        "/api/trends?start_date=2025-07-09&end_date=2025-07-11&offset=-1"
    ).status_code == 422


def test_release_workflow_preserves_waites_events_for_snapshot_review(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    run_mock_day_workflow(
        settings=settings,
        run_date=run_date,
        raw_retention="release",
    )
    client = TestClient(create_app(settings=settings))

    response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-09"
        "&scope=sensor&installation_point_id=201300"
    )

    assert response.status_code == 200
    events = response.json()["events"]
    assert events["input"] == "sqlite"
    assert events["providers"]["waites"]["row_count"] == 4
    assert events["row_count"] == 1
    assert events["rows"][0]["event_id"] == "9001"


def test_representative_web_payloads_stay_within_response_budget(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    _prepare_mock_window(settings)
    _seed_representative_sqlite_window(
        settings=settings,
        start_date=date(2025, 7, 9),
        day_count=22,
        sensor_count=120,
    )

    trend_response = client.get(
        "/api/trends?source=mock&start_date=2025-07-09&end_date=2025-07-30"
        "&metric=rms_vel&dimension=x"
    )
    snapshot_response = client.get(
        "/api/snapshot-review/2025-07-09"
        "?source=mock&start_date=2025-07-09&end_date=2025-07-30"
        "&metric=rms_vel&dimension=x&k=4"
    )

    assert trend_response.status_code == 200
    assert snapshot_response.status_code == 200
    assert len(trend_response.content) < 2_000_000
    assert len(snapshot_response.content) < 2_000_000
    assert trend_response.json()["detail"]["truncated"] is True
    assert "rows" not in snapshot_response.json()["trend"]
    assert set(trend_response.json()["sensor_rows"][0]) < {
        "date",
        "installation_point_id",
        "installation_point_name",
        "equipment_id",
        "equipment_name",
        "sensor_id",
        "customer_asset_id",
        "rms_vel_mean_x",
        "rms_vel_max_x",
        "rms_vel_min_x",
        "unexpected_sentinel",
    }


def _prepare_mock_window(settings: AppSettings) -> None:
    start = date(2025, 7, 9)
    end = date(2025, 7, 11)
    for run_date in [start, date(2025, 7, 10), end]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        load_waites_observations(
            settings=settings,
            run_date=run_date,
            source="mock",
        )
        build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=start, end_date=end)
    build_cluster_window(settings=settings, start_date=start, end_date=end, source="mock", dimension="x", k=4)


def _seed_representative_sqlite_window(
    settings: AppSettings,
    start_date: date,
    day_count: int,
    sensor_count: int,
) -> None:
    with connect_observation_store(settings) as connection:
        connection.row_factory = None
        columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(sensor_daily_facts)")
        ]
        base = connection.execute(
            f"""
            SELECT {", ".join(f'"{column}"' for column in columns)}
            FROM sensor_daily_facts
            WHERE source = ? AND source_date = ?
            LIMIT 1
            """,
            ("mock", start_date.isoformat()),
        ).fetchone()
        assert base is not None
        indexes = {column: index for index, column in enumerate(columns)}
        rows = []
        for day_offset in range(day_count):
            source_date = (start_date + timedelta(days=day_offset)).isoformat()
            for sensor_offset in range(sensor_count):
                values = list(base)
                installation_id = str(300_000 + sensor_offset)
                equipment_id = str(400_000 + sensor_offset // 4)
                values[indexes["source_date"]] = source_date
                values[indexes["installation_point_id"]] = installation_id
                values[indexes["installation_point_name"]] = f"Budget Sensor {sensor_offset}"
                values[indexes["sensor_id"]] = str(500_000 + sensor_offset)
                values[indexes["equipment_id"]] = equipment_id
                values[indexes["equipment_name"]] = f"Budget Equipment {sensor_offset // 4}"
                rows.append(values)
        placeholders = ", ".join("?" for _column in columns)
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.executemany(
            f"""
            INSERT OR REPLACE INTO sensor_daily_facts ({quoted_columns})
            VALUES ({placeholders})
            """,
            rows,
        )
        connection.commit()

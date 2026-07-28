from pathlib import Path

from fastapi.testclient import TestClient

from insy_sensor_data.api.main import create_app
from insy_sensor_data.clustering.window import build_cluster_window
from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.snapshots.trends import build_trends
from insy_sensor_data.waites.fetch import fetch_waites
from datetime import date


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
    assert 'id="snapshot-context"' in response.text
    assert 'id="snapshot-trend-chart"' in response.text
    assert 'id="snapshot-cluster-chart"' in response.text
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
    assert len(trend_payload["sensor_rows"]) == 9
    assert trend_payload["metadata"]["equipment_record_count"] >= 1


def test_trend_endpoint_reads_multi_day_mock_artifact(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    for run_date in [date(2025, 7, 9), date(2025, 7, 10), date(2025, 7, 11)]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=date(2025, 7, 9), end_date=date(2025, 7, 11))

    response = client.get("/api/trends?start_date=2025-07-09&end_date=2025-07-11")

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["sensor_record_count"] == 27
    assert payload["metadata"]["skipped_dates"] == []
    assert {row["date"] for row in payload["sensor_rows"]} == {
        "2025-07-09",
        "2025-07-10",
        "2025-07-11",
    }


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
    assert artifacts["counts"]["clusters"] == 3
    assert artifacts["counts"]["drift"] == 2
    assert artifacts["counts"]["cluster_windows"] == 1
    assert artifacts["sources"] == ["mock"]
    assert artifacts["dimensions"] == ["x"]
    assert artifacts["ks"] == [4]

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
    assert cluster_response.status_code == 200
    cluster = cluster_response.json()
    assert cluster["row_count"] == 9
    assert cluster["cluster_row_count"] == 4
    assert cluster["metrics"]["feature_count"] == 16
    assert len(cluster["pca_rows"]) == 9

    drift_response = client.get(
        "/api/drift?source=mock&from_date=2025-07-09&to_date=2025-07-10&dimension=x&k=4"
    )
    assert drift_response.status_code == 200
    drift = drift_response.json()
    assert len(drift["raw_rows"]) >= 1
    assert len(drift["aligned_rows"]) >= 1
    assert drift["aligned_metrics"]["matched_sensor_count"] >= 1

    window_response = client.get(
        "/api/cluster-windows?source=mock&start_date=2025-07-09&end_date=2025-07-11&dimension=x&k=4"
    )
    assert window_response.status_code == 200
    window = window_response.json()
    assert window["metrics"]["date_count"] == 3
    assert len(window["quality_rows"]) == 3
    assert len(window["aligned_drift_rows"]) == 2


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
    assert payload["cluster_context"]["status"] == "available"
    assert payload["cluster_context"]["row_count"] == 1
    assert payload["events"]["status"] == "available"
    assert payload["events"]["row_count"] == 1
    assert payload["events"]["rows"][0]["event_id"] == "9001"
    assert payload["measurements"]["row_count"] == 1
    assert "rms_vel_mean_x" in payload["measurements"]["columns"]


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
    assert payload["cluster_context"]["row_count"] == 2
    assert {row["event_id"] for row in payload["events"]["rows"]} == {"9001", "9002"}


def test_snapshot_review_endpoint_handles_missing_trend_and_cluster_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    response = client.get(
        "/api/snapshot-review/2025-07-09?source=mock&scope=sensor&installation_point_id=201300"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["snapshot_row_count"] == 1
    assert payload["trend"]["status"] == "missing"
    assert payload["cluster_context"]["status"] == "missing"
    assert payload["events"]["row_count"] == 1
    assert payload["measurements"]["row_count"] == 1


def test_cluster_endpoint_validates_parameters_and_missing_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    client = TestClient(create_app(settings=settings))

    bad_dimension = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=q&k=4")
    assert bad_dimension.status_code == 422

    bad_k = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=x&k=0")
    assert bad_k.status_code == 422

    missing = client.get("/api/clusters?source=mock&date=2025-07-09&dimension=x&k=4")
    assert missing.status_code == 404


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
    assert trend["sensor_row_count"] == 27
    assert trend["filtered_sensor_row_count"] == 3
    assert {row["installation_point_id"] for row in trend["sensor_rows"]} == {"201300"}


def _prepare_mock_window(settings: AppSettings) -> None:
    start = date(2025, 7, 9)
    end = date(2025, 7, 11)
    for run_date in [start, date(2025, 7, 10), end]:
        fetch_waites(settings=settings, run_date=run_date, facility_id=679)
        build_sensor_snapshot(settings=settings, run_date=run_date)
    build_trends(settings=settings, start_date=start, end_date=end)
    build_cluster_window(settings=settings, start_date=start, end_date=end, source="mock", dimension="x", k=4)

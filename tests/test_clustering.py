from __future__ import annotations

from datetime import date
from pathlib import Path
import csv
import json

from typer.testing import CliRunner

from insy_sensor_data.cli import app
from insy_sensor_data.clustering.model import build_cluster_run, compare_cluster_drift
from insy_sensor_data.config import AppSettings
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.waites.fetch import fetch_waites


runner = CliRunner()


def test_build_cluster_run_writes_deterministic_cluster_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    _prepare_snapshot(settings, run_date)

    summary = build_cluster_run(
        settings=settings,
        run_date=run_date,
        source="mock",
        dimension="x",
        k=3,
    )
    first_clusters = Path(summary["sensor_clusters_path"]).read_text(encoding="utf-8")
    second = build_cluster_run(
        settings=settings,
        run_date=run_date,
        source="mock",
        dimension="x",
        k=3,
    )
    second_clusters = Path(second["sensor_clusters_path"]).read_text(encoding="utf-8")

    assert summary["row_count"] == 9
    assert summary["feature_count"] > 0
    assert summary["cluster_counts"] == second["cluster_counts"]
    assert first_clusters == second_clusters
    assert (tmp_path / "data" / "processed" / "features" / "date=2025-07-09_source=mock").exists()

    sensor_rows = _csv_rows(Path(summary["sensor_clusters_path"]))
    cluster_summary_rows = _csv_rows(Path(summary["cluster_summary_path"]))
    pca_rows = _csv_rows(Path(summary["pca_coordinates_path"]))
    metrics = json.loads(Path(summary["metrics_path"]).read_text(encoding="utf-8"))

    assert len(sensor_rows) == 9
    assert "cluster" in sensor_rows[0]
    assert "distance_to_centroid" in sensor_rows[0]
    assert len(cluster_summary_rows) == 3
    assert any(field.startswith("mean_rms_vel") for field in cluster_summary_rows[0])
    assert len(pca_rows) == 9
    assert {"pc1", "pc2"} <= set(pca_rows[0])
    assert metrics["metrics"]["silhouette_score"]["available"] is True
    assert metrics["metrics"]["calinski_harabasz_score"]["available"] is True


def test_cluster_metrics_are_marked_unavailable_when_k_is_one(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    _prepare_snapshot(settings, run_date)

    summary = build_cluster_run(
        settings=settings,
        run_date=run_date,
        source="mock",
        dimension="temperature",
        k=1,
    )

    metrics = json.loads(Path(summary["metrics_path"]).read_text(encoding="utf-8"))
    assert metrics["metrics"]["silhouette_score"]["available"] is False
    assert metrics["metrics"]["calinski_harabasz_score"]["available"] is False
    assert summary["cluster_counts"] == {"0": 9}


def test_compare_cluster_drift_writes_assignment_and_centroid_outputs(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    from_date = date(2025, 7, 9)
    to_date = date(2025, 7, 10)
    for run_date in [from_date, to_date]:
        _prepare_snapshot(settings, run_date)
        build_cluster_run(
            settings=settings,
            run_date=run_date,
            source="mock",
            dimension="x",
            k=3,
        )

    summary = compare_cluster_drift(
        settings=settings,
        from_date=from_date,
        to_date=to_date,
        source="mock",
        dimension="x",
        k=3,
    )

    drift_rows = _csv_rows(Path(summary["cluster_drift_path"]))
    centroid_rows = _csv_rows(Path(summary["centroid_drift_path"]))
    metrics = json.loads(Path(summary["metrics_path"]).read_text(encoding="utf-8"))
    assert summary["matched_sensor_count"] == 9
    assert len(drift_rows) == 9
    assert {"from_cluster", "to_cluster", "changed"} <= set(drift_rows[0])
    assert len(centroid_rows) == 3
    assert metrics["matched_sensor_count"] == 9


def test_cli_cluster_run_and_drift_write_json_summaries(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")
    settings = AppSettings.from_env(env_file=env_file)
    for run_date in [date(2025, 7, 9), date(2025, 7, 10)]:
        _prepare_snapshot(settings, run_date)

    run_result = runner.invoke(
        app,
        [
            "cluster",
            "run",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--dimension",
            "x",
            "--k",
            "3",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.stdout)
    assert run_payload["dimension"] == "x"
    assert run_payload["k"] == 3
    assert Path(run_payload["sensor_clusters_path"]).exists()

    second_result = runner.invoke(
        app,
        [
            "cluster",
            "run",
            "--source",
            "mock",
            "--date",
            "2025-07-10",
            "--dimension",
            "x",
            "--k",
            "3",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )
    assert second_result.exit_code == 0

    drift_result = runner.invoke(
        app,
        [
            "cluster",
            "drift",
            "--source",
            "mock",
            "--from-date",
            "2025-07-09",
            "--to-date",
            "2025-07-10",
            "--dimension",
            "x",
            "--k",
            "3",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert drift_result.exit_code == 0
    drift_payload = json.loads(drift_result.stdout)
    assert drift_payload["matched_sensor_count"] == 9
    assert Path(drift_payload["cluster_drift_path"]).exists()


def _prepare_snapshot(settings: AppSettings, run_date: date) -> None:
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))

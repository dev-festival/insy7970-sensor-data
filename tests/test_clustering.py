from __future__ import annotations

from datetime import date
from pathlib import Path
import csv
import json

from typer.testing import CliRunner

from insy_sensor_data.artifacts import write_csv_rows, write_json
from insy_sensor_data.cli import app
from insy_sensor_data.clustering.model import build_cluster_run, compare_cluster_drift
from insy_sensor_data.clustering.registry import (
    build_cluster_model_grid,
    list_registered_cluster_models,
    load_registered_cluster_view,
    load_registered_cluster_window_view,
    load_registered_drift_view,
)
from insy_sensor_data.clustering.window import align_cluster_drift, build_cluster_window
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


def test_align_cluster_drift_handles_centroid_label_permutation(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    from_date = date(2025, 7, 9)
    to_date = date(2025, 7, 10)
    _write_permuted_cluster_artifacts(settings, from_date, to_date)

    summary = align_cluster_drift(
        settings=settings,
        from_date=from_date,
        to_date=to_date,
        source="mock",
        dimension="x",
        k=2,
    )

    assert summary["matched_sensor_count"] == 2
    assert summary["raw_label_changed_count"] == 2
    assert summary["aligned_changed_count"] == 0
    assert summary["warnings"][0]["code"] == "label_alignment_adjusted_drift"

    alignment_rows = _csv_rows(Path(summary["centroid_alignment_path"]))
    assert {row["from_cluster"]: row["to_cluster"] for row in alignment_rows} == {"0": "1", "1": "0"}

    drift_rows = _csv_rows(Path(summary["aligned_cluster_drift_path"]))
    assert {row["aligned_changed"] for row in drift_rows} == {"false"}


def test_build_cluster_window_writes_quality_and_aligned_drift_outputs(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 10)
    for run_date in [start_date, end_date]:
        _prepare_snapshot(settings, run_date)

    summary = build_cluster_window(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        source="mock",
        dimension="x",
        k=3,
    )

    assert summary["date_count"] == 2
    assert summary["pair_count"] == 1
    assert summary["warning_count"] >= 2
    assert Path(summary["window_summary_path"]).exists()
    assert Path(summary["quality_summary_path"]).exists()
    assert Path(summary["aligned_drift_summary_path"]).exists()
    assert Path(summary["centroid_alignment_path"]).exists()

    quality_rows = _csv_rows(Path(summary["quality_summary_path"]))
    assert quality_rows[0]["quality_level"] == "contract_test_only"
    assert "contract" in quality_rows[0]["interpretation"].lower()


def test_build_cluster_model_grid_persists_registered_models_and_drift(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 10)
    for run_date in [start_date, end_date]:
        _prepare_snapshot(settings, run_date)

    summary = build_cluster_model_grid(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        source="mock",
        feature_spaces=["x_accel", "temperature"],
        ks=[5],
    )

    assert summary["model_count"] == 4
    assert summary["models_built"] == 4
    assert summary["drift_built"] == 2
    assert summary["drift_skipped"] == 0
    assert Path(summary["database_path"]).exists()

    discovery = list_registered_cluster_models(settings, source="mock")
    assert discovery["complete_count"] == 4
    assert set(discovery["feature_spaces"]) == {"x_accel", "temperature"}
    assert discovery["ks"] == [5]

    cluster = load_registered_cluster_view(
        settings=settings,
        run_date=start_date,
        source="mock",
        feature_space="x_accel",
        k=5,
    )
    assert cluster["registered"] is True
    assert cluster["row_count"] == 9
    assert cluster["cluster_row_count"] == 5
    assert cluster["metrics"]["feature_count"] == 4
    assert cluster["metrics"]["features"] == [
        "rms_accel_mean_x",
        "rms_accel_std_x",
        "rms_accel_max_x",
        "rms_accel_min_x",
    ]
    assert len(cluster["pca_rows"]) == 9

    drift = load_registered_drift_view(
        settings=settings,
        from_date=start_date,
        to_date=end_date,
        source="mock",
        feature_space="x_accel",
        k=5,
    )
    assert drift["registered"] is True
    assert drift["metrics"]["matched_sensor_count"] == 9
    assert len(drift["aligned_rows"]) == 9

    window = load_registered_cluster_window_view(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        source="mock",
        feature_space="x_accel",
        k=5,
    )
    assert window["registered"] is True
    assert window["metrics"]["pair_count"] == 1
    assert len(window["quality_rows"]) == 2
    assert len(window["aligned_drift_rows"]) == 1

    resumed = build_cluster_model_grid(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        source="mock",
        feature_spaces=["x_accel", "temperature"],
        ks=[5],
    )
    assert resumed["models_reused"] == 4
    assert resumed["drift_reused"] == 2


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


def test_cli_cluster_window_and_align_drift_write_json_summaries(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")
    settings = AppSettings.from_env(env_file=env_file)
    for run_date in [date(2025, 7, 9), date(2025, 7, 10)]:
        _prepare_snapshot(settings, run_date)

    window_result = runner.invoke(
        app,
        [
            "cluster",
            "window",
            "--source",
            "mock",
            "--start-date",
            "2025-07-09",
            "--end-date",
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

    assert window_result.exit_code == 0
    window_payload = json.loads(window_result.stdout)
    assert window_payload["date_count"] == 2
    assert window_payload["pair_count"] == 1
    assert Path(window_payload["quality_summary_path"]).exists()

    align_result = runner.invoke(
        app,
        [
            "cluster",
            "align-drift",
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

    assert align_result.exit_code == 0
    align_payload = json.loads(align_result.stdout)
    assert align_payload["matched_sensor_count"] == 9
    assert Path(align_payload["centroid_alignment_path"]).exists()


def _prepare_snapshot(settings: AppSettings, run_date: date) -> None:
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def _write_permuted_cluster_artifacts(settings: AppSettings, from_date: date, to_date: date) -> None:
    cluster_root = settings.data_dir / "processed" / "clusters"
    from_dir = cluster_root / "date=2025-07-09_source=mock_dimension=x_k=2"
    to_dir = cluster_root / "date=2025-07-10_source=mock_dimension=x_k=2"
    from_dir.mkdir(parents=True, exist_ok=True)
    to_dir.mkdir(parents=True, exist_ok=True)

    sensor_fields = [
        "installation_point_id",
        "installation_point_name",
        "equipment_id",
        "equipment_name",
        "sensor_id",
        "facility_id",
        "customer_asset_id",
        "installation_customer_asset_id",
        "equipment_customer_asset_id",
        "cluster",
        "distance_to_centroid",
        "rms_vel_mean_x",
    ]
    write_csv_rows(
        from_dir / "sensor_clusters.csv",
        [
            _cluster_sensor_row("201300", "0", 1.0),
            _cluster_sensor_row("201301", "1", 9.0),
        ],
        sensor_fields,
    )
    write_csv_rows(
        to_dir / "sensor_clusters.csv",
        [
            _cluster_sensor_row("201300", "1", 1.1),
            _cluster_sensor_row("201301", "0", 8.9),
        ],
        sensor_fields,
    )

    summary_fields = [
        "cluster",
        "sensor_count",
        "sensor_fraction",
        "within_cluster_sse",
        "mean_rms_vel_mean_x",
        "centroid_scaled_rms_vel_mean_x",
    ]
    write_csv_rows(
        from_dir / "cluster_summary.csv",
        [_cluster_summary_row("0", 1.0), _cluster_summary_row("1", 9.0)],
        summary_fields,
    )
    write_csv_rows(
        to_dir / "cluster_summary.csv",
        [_cluster_summary_row("0", 9.1), _cluster_summary_row("1", 1.1)],
        summary_fields,
    )
    write_csv_rows(from_dir / "pca_coordinates.csv", [], ["installation_point_id"])
    write_csv_rows(to_dir / "pca_coordinates.csv", [], ["installation_point_id"])
    for run_date, output_dir in [(from_date, from_dir), (to_date, to_dir)]:
        write_json(
            output_dir / "metrics.json",
            {
                "schema_version": 1,
                "source": "mock",
                "date": run_date.isoformat(),
                "dimension": "x",
                "k": 2,
                "random_seed": 42,
                "row_count": 2,
                "feature_count": 1,
                "cluster_counts": {"0": 1, "1": 1},
                "kmeans": {"inertia": 0.0},
                "metrics": {
                    "silhouette_score": {"available": False, "value": None},
                    "calinski_harabasz_score": {"available": False, "value": None},
                },
                "outputs": {
                    "sensor_clusters": (output_dir / "sensor_clusters.csv").as_posix(),
                    "cluster_summary": (output_dir / "cluster_summary.csv").as_posix(),
                    "pca_coordinates": (output_dir / "pca_coordinates.csv").as_posix(),
                    "metrics": (output_dir / "metrics.json").as_posix(),
                },
            },
        )


def _cluster_sensor_row(installation_point_id: str, cluster: str, value: float) -> dict[str, object]:
    return {
        "installation_point_id": installation_point_id,
        "installation_point_name": f"Sensor {installation_point_id}",
        "equipment_id": "55576",
        "equipment_name": "Equipment",
        "sensor_id": installation_point_id,
        "facility_id": "679",
        "customer_asset_id": "ASSET",
        "installation_customer_asset_id": "ASSET",
        "equipment_customer_asset_id": "ASSET",
        "cluster": cluster,
        "distance_to_centroid": 0.0,
        "rms_vel_mean_x": value,
    }


def _cluster_summary_row(cluster: str, centroid: float) -> dict[str, object]:
    return {
        "cluster": cluster,
        "sensor_count": 1,
        "sensor_fraction": 0.5,
        "within_cluster_sse": 0.0,
        "mean_rms_vel_mean_x": centroid,
        "centroid_scaled_rms_vel_mean_x": centroid,
    }

from __future__ import annotations

from datetime import date
from pathlib import Path
import csv
import json

from typer.testing import CliRunner

from insy_sensor_data.cli import app
from insy_sensor_data.clustering.features import build_feature_preview
from insy_sensor_data.config import AppSettings
from insy_sensor_data.reports import build_mock_trend_report
from insy_sensor_data.snapshots.build import build_sensor_snapshot
from insy_sensor_data.waites.fetch import fetch_waites
from insy_sensor_data.workflows import run_mock_trend_workflow


runner = CliRunner()


def test_build_feature_preview_writes_dimension_specific_matrices(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    summary = build_feature_preview(settings=settings, run_date=run_date)

    feature_dir = tmp_path / "data" / "processed" / "features" / "date=2025-07-09_source=mock"
    assert summary["feature_policy"] == "dimension_specific_vibration_and_temperature"
    assert set(summary["dimensions"]) == {"x", "y", "z", "temperature"}
    assert (feature_dir / "feature_matrix_x.csv").exists()
    assert (feature_dir / "feature_summary_x.csv").exists()
    assert (feature_dir / "feature_matrix_y.csv").exists()
    assert (feature_dir / "feature_matrix_z.csv").exists()
    assert (feature_dir / "feature_matrix_temperature.csv").exists()
    assert (feature_dir / "feature_summary_temperature.csv").exists()

    x_header = _csv_header(feature_dir / "feature_matrix_x.csv")
    y_header = _csv_header(feature_dir / "feature_matrix_y.csv")
    temperature_header = _csv_header(feature_dir / "feature_matrix_temperature.csv")
    assert "installation_point_id" in x_header
    assert "rms_vel_mean_x" in x_header
    assert "rms_accel_max_x" in x_header
    assert "rms_vel_mean_y" not in x_header
    assert "impact_mean" not in x_header
    assert "temp_sensor_mean" not in x_header
    assert "rms_vel_mean_y" in y_header
    assert "rms_vel_mean_x" not in y_header
    assert "temp_sensor_mean" in temperature_header
    assert "temp_ambient_mean" in temperature_header
    assert "rms_vel_mean_x" not in temperature_header
    assert "impact_mean" not in temperature_header


def test_feature_summary_explains_exclusions_and_imputation(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    build_feature_preview(settings=settings, run_date=run_date, axis="x")

    feature_dir = tmp_path / "data" / "processed" / "features" / "date=2025-07-09_source=mock"
    summary_rows = _rows_by_feature(feature_dir / "feature_summary_x.csv")
    assert summary_rows["installation_point_id"]["reason"] == "identifier_or_label"
    assert summary_rows["rms_vel_mean_x"]["included"] == "true"
    assert summary_rows["rms_vel_mean_y"]["reason"] == "axis_mismatch"
    assert summary_rows["impact_mean"]["reason"] == "non_axis_specific"
    assert summary_rows["temp_sensor_mean"]["reason"] == "dimension_mismatch"
    assert int(summary_rows["rms_vel_mean_x"]["imputed_count"]) > 0
    assert summary_rows["rms_vel_mean_x"]["imputation_value"] != ""


def test_build_feature_preview_is_deterministic(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    build_feature_preview(settings=settings, run_date=run_date, axis="x")
    feature_dir = tmp_path / "data" / "processed" / "features" / "date=2025-07-09_source=mock"
    first_matrix = (feature_dir / "feature_matrix_x.csv").read_text(encoding="utf-8")
    build_feature_preview(settings=settings, run_date=run_date, axis="x")
    second_matrix = (feature_dir / "feature_matrix_x.csv").read_text(encoding="utf-8")

    assert first_matrix == second_matrix


def test_feature_preview_fails_for_too_small_inputs(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    summary = build_feature_preview(
        settings=settings,
        run_date=run_date,
        axis="x",
        min_rows=20,
    )

    dimension_summary = summary["dimensions"]["x"]
    assert dimension_summary["status"] == "not_ready"
    assert any(warning["code"] == "too_few_rows" for warning in dimension_summary["warnings"])


def test_temperature_dimension_includes_temperature_features_only(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    summary = build_feature_preview(settings=settings, run_date=run_date, axis="temperature")

    feature_dir = tmp_path / "data" / "processed" / "features" / "date=2025-07-09_source=mock"
    temperature_summary = summary["dimensions"]["temperature"]
    summary_rows = _rows_by_feature(feature_dir / "feature_summary_temperature.csv")
    assert temperature_summary["status"] == "ready"
    assert "temp_sensor_mean" in temperature_summary["features"]
    assert "temp_ambient_mean" in temperature_summary["features"]
    assert summary_rows["temp_sensor_mean"]["included"] == "true"
    assert summary_rows["rms_vel_mean_x"]["reason"] == "dimension_mismatch"
    assert summary_rows["impact_mean"]["reason"] == "non_axis_specific"


def test_report_includes_feature_readiness_when_available(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data")
    start_date = date(2025, 7, 9)
    end_date = date(2025, 7, 11)
    run_mock_trend_workflow(settings=settings, start_date=start_date, end_date=end_date)
    build_feature_preview(settings=settings, run_date=start_date)

    report = build_mock_trend_report(
        settings=settings,
        start_date=start_date,
        end_date=end_date,
        render_quarto=False,
    )

    feature_sample = Path(report["sample_paths"]["feature_readiness"])
    assert feature_sample.exists()
    report_text = Path(report["report_md_path"]).read_text(encoding="utf-8")
    assert "Feature Readiness" in report_text
    feature_sample_text = feature_sample.read_text(encoding="utf-8")
    assert "dimension" in feature_sample_text
    assert "feature_matrix_x.csv" in feature_sample_text
    assert "feature_matrix_temperature.csv" in feature_sample_text


def test_cli_cluster_features_writes_json_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")
    settings = AppSettings.from_env(env_file=env_file)
    run_date = date(2025, 7, 9)
    fetch_waites(settings=settings, run_date=run_date, facility_id=679)
    build_sensor_snapshot(settings=settings, run_date=run_date)

    result = runner.invoke(
        app,
        [
            "cluster",
            "features",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--dimension",
            "temperature",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dimension_mode"] == "temperature"
    assert payload["dimensions"]["temperature"]["feature_count"] > 0
    assert (data_dir / "processed" / "features" / "date=2025-07-09_source=mock" / "metadata.json").exists()

    alias_result = runner.invoke(
        app,
        [
            "cluster",
            "features",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--axis",
            "x",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert alias_result.exit_code == 0
    alias_payload = json.loads(alias_result.stdout)
    assert alias_payload["dimension_mode"] == "x"
    assert alias_payload["dimensions"]["x"]["feature_count"] > 0


def _csv_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return next(csv.reader(csv_file))


def _rows_by_feature(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return {row["feature"]: row for row in csv.DictReader(csv_file)}

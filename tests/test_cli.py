from pathlib import Path
import json
import sqlite3

from typer.testing import CliRunner

from insy_sensor_data.cli import app


runner = CliRunner()


def test_cli_health_outputs_json(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("INSY_DATA_DIR=test-data\n", encoding="utf-8")

    result = runner.invoke(app, ["health", "--env-file", str(env_file)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["source_mode"] == "mock"
    assert payload["data_dir"] == "test-data"
    assert payload["waites"]["token_configured"] is False


def test_cli_health_reads_default_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("INSY_DATA_DIR=default-env-data\n", encoding="utf-8")

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data_dir"] == "default-env-data"


def test_cli_serve_help_is_discoverable() -> None:
    result = runner.invoke(app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "--source" in result.stdout
    assert "--host" in result.stdout
    assert "--port" in result.stdout


def test_cli_waites_fetch_writes_mock_artifacts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "waites",
            "fetch",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--facility",
            "679",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["endpoint_count"] == 7
    assert payload["record_counts"]["asset-tree"] == 2
    assert payload["record_counts"]["equipment"] == 6
    assert (data_dir / "raw" / "waites" / "date=2025-07-09" / "manifest.json").exists()
    assert not (data_dir / "processed" / "waites" / "reference" / "asset_tree.csv").exists()
    assert not (data_dir / "processed" / "waites" / "reference" / "equipment.csv").exists()


def test_cli_waites_fetch_api_requires_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "waites",
            "fetch",
            "--source",
            "api",
            "--date",
            "2025-07-09",
            "--facility",
            "679",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code != 0
    assert "WAITES_ACCESS_TOKEN" in result.output or "WAITES_ACCESS_TOKEN" in str(result.exception)


def test_cli_waites_validate_writes_report(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    fetch_result = runner.invoke(
        app,
        [
            "waites",
            "fetch",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--facility",
            "679",
            "--env-file",
            str(env_file),
        ],
    )
    assert fetch_result.exit_code == 0

    validate_result = runner.invoke(
        app,
        [
            "waites",
            "validate",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )

    assert validate_result.exit_code == 0
    payload = json.loads(validate_result.stdout)
    assert payload["status"] in {"valid", "valid_with_warnings"}
    assert payload["endpoint_record_counts"]["equipment"] == 6
    assert (data_dir / "raw" / "waites" / "date=2025-07-09" / "validation.json").exists()


def test_cli_snapshot_and_trend_builds_write_mock_artifacts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    fetch_result = runner.invoke(
        app,
        [
            "waites",
            "fetch",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--facility",
            "679",
            "--env-file",
            str(env_file),
        ],
    )
    assert fetch_result.exit_code == 0

    snapshot_result = runner.invoke(
        app,
        [
            "snapshot",
            "build",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert snapshot_result.exit_code == 0
    snapshot_payload = json.loads(snapshot_result.stdout)
    assert snapshot_payload["record_count"] == 9
    assert not (data_dir / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv").exists()

    trend_result = runner.invoke(
        app,
        [
            "trend",
            "build",
            "--source",
            "mock",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert trend_result.exit_code == 0
    trend_payload = json.loads(trend_result.stdout)
    assert trend_payload["sensor_record_count"] == 9
    routine_trend = data_dir / "processed" / "trends" / "start=2025-07-09_end=2025-07-09" / "sensor_trends.csv"
    assert not routine_trend.exists()
    snapshot_export = runner.invoke(
        app,
        [
            "snapshot", "export", "--source", "mock", "--date", "2025-07-09",
            "--destination", str(tmp_path / "exports" / "snapshot.csv"),
            "--env-file", str(env_file),
        ],
    )
    trend_export = runner.invoke(
        app,
        [
            "trend", "export", "--source", "mock", "--start-date", "2025-07-09",
            "--end-date", "2025-07-09", "--destination", str(tmp_path / "exports" / "trends"),
            "--env-file", str(env_file),
        ],
    )
    assert snapshot_export.exit_code == trend_export.exit_code == 0
    assert (tmp_path / "exports" / "snapshot.csv").is_file()
    assert (tmp_path / "exports" / "trends" / "sensor_trends.csv").is_file()


def test_cli_raw_lifecycle_commands(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    fetch_result = runner.invoke(
        app,
        [
            "waites",
            "fetch",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--facility",
            "679",
            "--env-file",
            str(env_file),
        ],
    )
    assert fetch_result.exit_code == 0

    verify_result = runner.invoke(
        app,
        [
            "raw",
            "verify",
            "--source",
            "waites",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert verify_result.exit_code == 0
    assert json.loads(verify_result.stdout)["status"] == "valid"

    compress_result = runner.invoke(
        app,
        [
            "raw",
            "compress",
            "--source",
            "waites",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert compress_result.exit_code == 0
    assert json.loads(compress_result.stdout)["compressed_count"] == 7
    assert (data_dir / "raw" / "waites" / "date=2025-07-09" / "equipment.json.gz").exists()

    prune_result = runner.invoke(
        app,
        [
            "raw",
            "prune",
            "--source",
            "waites",
            "--older-than-days",
            "1",
            "--env-file",
            str(env_file),
        ],
    )
    assert prune_result.exit_code == 0
    assert json.loads(prune_result.stdout)["dry_run"] is True


def test_cli_store_load_waites_and_sqlite_snapshot(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    fetch_result = runner.invoke(
        app,
        [
            "waites",
            "fetch",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--facility",
            "679",
            "--env-file",
            str(env_file),
        ],
    )
    assert fetch_result.exit_code == 0

    load_result = runner.invoke(
        app,
        [
            "store",
            "load-waites",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert load_result.exit_code == 0
    load_payload = json.loads(load_result.stdout)
    assert load_payload["row_counts"]["rms"] == 21
    assert (data_dir / "processed" / "observations.sqlite").exists()

    snapshot_result = runner.invoke(
        app,
        [
            "snapshot",
            "build",
            "--source",
            "mock",
            "--input",
            "sqlite",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert snapshot_result.exit_code == 0
    snapshot_payload = json.loads(snapshot_result.stdout)
    assert snapshot_payload["input_mode"] == "sqlite"
    assert snapshot_payload["snapshot_store"]["row_count"] == 9

    purge_preview = runner.invoke(
        app,
        [
            "store",
            "purge-native",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--dry-run",
            "--env-file",
            str(env_file),
        ],
    )
    assert purge_preview.exit_code == 0
    assert json.loads(purge_preview.stdout)["candidates"][0]["delete_ready"] is True

    purge_result = runner.invoke(
        app,
        [
            "store",
            "purge-native",
            "--source",
            "mock",
            "--date",
            "2025-07-09",
            "--confirm-delete",
            "--env-file",
            str(env_file),
        ],
    )
    assert purge_result.exit_code == 0
    assert json.loads(purge_result.stdout)["rows_deleted"] == 53
    assert _sqlite_count(data_dir, "waites_rms_observations") == 0
    assert _sqlite_count(data_dir, "waites_installation_points") == 0
    assert _sqlite_count(data_dir, "waites_action_items") == 4
    assert _sqlite_count(data_dir, "sensor_daily_facts") == 9
    assert _sqlite_count(data_dir, "waites_installation_point_reference") == 8


def test_cli_workflow_mock_day_prints_human_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflow",
            "mock-day",
            "--date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Mock day workflow: 2025-07-09" in result.stdout
    assert "Fetched Waites raw evidence" in result.stdout
    assert "Validated raw evidence" in result.stdout
    assert "Warnings:" in result.stdout
    assert "Loaded SQLite observations" in result.stdout
    assert "Built sensor snapshot" in result.stdout
    assert "Next:" in result.stdout
    assert (data_dir / "raw" / "waites" / "date=2025-07-09" / "manifest.json").exists()
    assert (data_dir / "processed" / "observations.sqlite").exists()
    assert not (data_dir / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv").exists()


def test_cli_workflow_mock_day_json_outputs_combined_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflow",
            "mock-day",
            "--date",
            "2025-07-09",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["workflow"] == "mock-day"
    assert payload["fetch"]["endpoint_count"] == 7
    assert payload["load"]["row_counts"]["rms"] == 0
    assert payload["load"]["staging_row_count"] == 0
    assert payload["snapshot"]["record_count"] == 9
    assert [step["title"] for step in payload["steps"]] == [
        "Fetched Waites raw evidence",
        "Validated raw evidence",
        "Loaded SQLite observations",
        "Built sensor snapshot",
    ]


def test_cli_workflow_mock_day_release_keeps_snapshot_only_operating_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflow",
            "mock-day",
            "--date",
            "2025-07-09",
            "--raw-retention",
            "release",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["retention"]["raw_retention_status"] == "released"
    assert payload["retention"]["native_retention_status"] == "purged"
    assert "Applied retention policy" in [step["title"] for step in payload["steps"]]
    raw_dir = data_dir / "raw" / "waites" / "date=2025-07-09"
    assert not (raw_dir / "equipment.json").exists()
    assert (raw_dir / "manifest.json").exists()
    assert (raw_dir / "validation.json").exists()
    assert _sqlite_count(data_dir, "waites_rms_observations") == 0
    assert _sqlite_count(data_dir, "waites_installation_points") == 0
    assert _sqlite_count(data_dir, "sensor_daily_facts") == 9
    assert _sqlite_count(data_dir, "waites_asset_tree_reference") == 3
    assert _sqlite_count(data_dir, "waites_installation_point_reference") == 8

    trend_result = runner.invoke(
        app,
        [
            "trend",
            "build",
            "--source",
            "mock",
            "--input",
            "sqlite",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-09",
            "--env-file",
            str(env_file),
        ],
    )
    assert trend_result.exit_code == 0
    assert json.loads(trend_result.stdout)["sensor_record_count"] == 9

    feature_result = runner.invoke(
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
    assert feature_result.exit_code == 0
    assert json.loads(feature_result.stdout)["dimensions"]["temperature"]["row_count"] == 9


def test_cli_workflow_mock_trend_writes_sqlite_backed_outputs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflow",
            "mock-trend",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--input",
            "sqlite",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Mock trend workflow: 2025-07-09 to 2025-07-11" in result.stdout
    assert "Prepared mock dates" in result.stdout
    assert "Built trend outputs" in result.stdout
    trend_dir = data_dir / "processed" / "trends" / "start=2025-07-09_end=2025-07-11"
    assert not (trend_dir / "sensor_trends.csv").exists()
    assert not (trend_dir / "equipment_trends.csv").exists()


def test_cli_workflow_mock_range_writes_cluster_window_and_resumes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflow",
            "mock-range",
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

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["workflow"] == "mock-range"
    assert [day["status"] for day in payload["days"]] == ["completed", "completed"]
    assert payload["cluster_windows"][0]["pair_count"] == 1
    assert "small_sample_contract_only" in json.dumps(payload["cluster_windows"][0]["warnings"])

    second_result = runner.invoke(
        app,
        [
            "workflow",
            "mock-range",
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

    assert second_result.exit_code == 0
    second_payload = json.loads(second_result.stdout)
    assert [day["status"] for day in second_payload["days"]] == [
        "skipped_existing",
        "skipped_existing",
    ]
    assert second_payload["cluster_windows"][0]["date_runs"][0]["status"] == "skipped_existing"
    window_dir = data_dir / "processed" / "cluster_windows" / "start=2025-07-09_end=2025-07-10_source=mock_dimension=x_k=3"
    assert (window_dir / "quality_summary.csv").exists()
    assert (window_dir / "aligned_drift_summary.csv").exists()


def test_cli_cluster_registry_build_grid_writes_sqlite_models(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    workflow_result = runner.invoke(
        app,
        [
            "workflow",
            "mock-range",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-10",
            "--skip-cluster",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )
    assert workflow_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "cluster",
            "registry",
            "build-grid",
            "--source",
            "mock",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-10",
            "--feature-spaces",
            "x_accel",
            "--ks",
            "5",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["model_count"] == 2
    assert payload["models_built"] == 2
    assert payload["drift_built"] == 1
    assert payload["feature_spaces"] == ["x_accel"]
    assert payload["ks"] == [5]
    assert _sqlite_count(data_dir, "cluster_model_runs") == 2
    assert _sqlite_count(data_dir, "cluster_drift_runs") == 1
    model_dir = data_dir / "processed" / "cluster_models" / "date=2025-07-09_source=mock_feature_space=x_accel_k=5"
    assert not (model_dir / "sensor_clusters.csv").exists()
    assert not (model_dir / "metrics.json").exists()


def test_cli_workflow_api_day_requires_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflow",
            "api-day",
            "--date",
            "2026-07-19",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code != 0
    assert "WAITES_ACCESS_TOKEN" in result.output or "WAITES_ACCESS_TOKEN" in str(result.exception)


def test_cli_maximo_asset_history_returns_mock_records(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "maximo",
            "asset-history",
            "--assetnum",
            "LEVF454TS",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--source",
            "mock",
            "--env-file",
            str(tmp_path / ".env"),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["row_count"] == 1
    assert payload["rows"][0]["wonum"] == "1234570"


def test_cli_report_mock_trend_writes_evidence_report(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    workflow_result = runner.invoke(
        app,
        [
            "workflow",
            "mock-trend",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--env-file",
            str(env_file),
        ],
    )
    assert workflow_result.exit_code == 0

    report_result = runner.invoke(
        app,
        [
            "report",
            "mock-trend",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--no-render",
            "--env-file",
            str(env_file),
        ],
    )

    assert report_result.exit_code == 0
    assert "Mock trend evidence report: 2025-07-09 to 2025-07-11" in report_result.stdout
    assert "Checks: 5 passed, 0 failed" in report_result.stdout
    report_dir = tmp_path / "reports" / "mock-trend" / "start=2025-07-09_end=2025-07-11"
    assert (report_dir / "report.md").exists()
    assert (report_dir / "checks.json").exists()


def test_cli_report_mock_trend_json_outputs_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    workflow_result = runner.invoke(
        app,
        [
            "workflow",
            "mock-trend",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--env-file",
            str(env_file),
        ],
    )
    assert workflow_result.exit_code == 0

    report_result = runner.invoke(
        app,
        [
            "report",
            "mock-trend",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--no-render",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert report_result.exit_code == 0
    payload = json.loads(report_result.stdout)
    assert payload["report"] == "mock-trend"
    assert payload["failed_check_count"] == 0
    assert payload["check_count"] == 5
    assert "rising-vibration" in payload["chart_paths"]


def test_cli_builds_multi_day_mock_trend(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(f"INSY_DATA_DIR={data_dir}\n", encoding="utf-8")

    for raw_date in ["2025-07-09", "2025-07-10", "2025-07-11"]:
        fetch_result = runner.invoke(
            app,
            [
                "waites",
                "fetch",
                "--source",
                "mock",
                "--date",
                raw_date,
                "--facility",
                "679",
                "--env-file",
                str(env_file),
            ],
        )
        assert fetch_result.exit_code == 0

        snapshot_result = runner.invoke(
            app,
            [
                "snapshot",
                "build",
                "--source",
                "mock",
                "--date",
                raw_date,
                "--env-file",
                str(env_file),
            ],
        )
        assert snapshot_result.exit_code == 0

    trend_result = runner.invoke(
        app,
        [
            "trend",
            "build",
            "--source",
            "mock",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-11",
            "--env-file",
            str(env_file),
        ],
    )

    assert trend_result.exit_code == 0
    trend_payload = json.loads(trend_result.stdout)
    assert trend_payload["sensor_record_count"] == 27
    assert trend_payload["skipped_dates"] == []
    routine_trend = data_dir / "processed" / "trends" / "start=2025-07-09_end=2025-07-11" / "sensor_trends.csv"
    assert not routine_trend.exists()


def _sqlite_count(data_dir: Path, table_name: str) -> int:
    with sqlite3.connect(data_dir / "processed" / "observations.sqlite") as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])

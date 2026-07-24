from pathlib import Path
import json

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
    assert payload["endpoint_count"] == 6
    assert payload["record_counts"]["equipment"] == 6
    assert (data_dir / "raw" / "waites" / "date=2025-07-09" / "manifest.json").exists()
    assert (data_dir / "processed" / "waites" / "reference" / "equipment.csv").exists()


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
    assert (data_dir / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv").exists()

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
    assert (data_dir / "processed" / "trends" / "start=2025-07-09_end=2025-07-09" / "sensor_trends.csv").exists()


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
    assert json.loads(compress_result.stdout)["compressed_count"] == 6
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
    assert json.loads(snapshot_result.stdout)["input_mode"] == "sqlite"


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
    assert (data_dir / "processed" / "snapshots" / "date=2025-07-09" / "sensor_snapshot.csv").exists()


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
    assert payload["fetch"]["endpoint_count"] == 6
    assert payload["load"]["row_counts"]["rms"] == 21
    assert payload["snapshot"]["record_count"] == 9
    assert [step["title"] for step in payload["steps"]] == [
        "Fetched Waites raw evidence",
        "Validated raw evidence",
        "Loaded SQLite observations",
        "Built sensor snapshot",
    ]


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
    assert (trend_dir / "sensor_trends.csv").exists()
    assert (trend_dir / "equipment_trends.csv").exists()


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
    assert (data_dir / "processed" / "trends" / "start=2025-07-09_end=2025-07-11" / "sensor_trends.csv").exists()

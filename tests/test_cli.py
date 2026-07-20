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

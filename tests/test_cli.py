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

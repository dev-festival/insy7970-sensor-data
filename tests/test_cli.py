from pathlib import Path
import json

from click import unstyle
from typer.testing import CliRunner

from insy_sensor_data.cli import app


runner = CliRunner()


def _env_file(tmp_path: Path, *, retention: str = "release") -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"INSY_DATA_DIR={tmp_path / 'data'}",
                "INSY_SOURCE_MODE=mock",
                "INSY_SYNC_START_DATE=2025-07-09",
                f"INSY_RAW_RETENTION={retention}",
            ]
        ),
        encoding="utf-8",
    )
    return env_file


def test_cli_serve_help_is_discoverable() -> None:
    result = runner.invoke(app, ["serve", "--help"], env={"FORCE_COLOR": "1"})
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--env-file" in output
    assert "--source" not in output
    assert "--host" in output
    assert "--port" in output


def test_cli_primary_help_contains_exactly_five_operator_commands() -> None:
    result = runner.invoke(app, ["--help"])
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    for command in ["serve", "sync", "rebuild", "doctor", "export"]:
        assert command in output
    for retired in [
        "health",
        "waites",
        "raw",
        "store",
        "snapshot",
        "trend",
        "workflow",
        "report",
        "cluster",
        "maximo",
    ]:
        result = runner.invoke(app, [retired])
        assert result.exit_code == 2
        assert "No such command" in result.output


def test_cli_sync_doctor_rebuild_and_exports_use_operational_surface(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)

    sync_result = runner.invoke(
        app,
        ["sync", "--date", "2025-07-09", "--json", "--env-file", str(env_file)],
    )
    assert sync_result.exit_code == 0, sync_result.output
    synced = json.loads(sync_result.stdout)
    assert synced["status"] == "advanced"
    assert synced["dates"][0]["models"] == "built"

    doctor_result = runner.invoke(
        app,
        [
            "doctor",
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-09",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )
    assert doctor_result.exit_code == 0, doctor_result.output
    diagnosed = json.loads(doctor_result.stdout)
    assert diagnosed["status"] == "ok"
    assert diagnosed["synchronization"]["issue_count"] == 0

    rebuild_result = runner.invoke(
        app,
        [
            "rebuild",
            "--date",
            "2025-07-09",
            "--component",
            "models",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )
    assert rebuild_result.exit_code == 0, rebuild_result.output
    rebuilt = json.loads(rebuild_result.stdout)
    assert rebuilt["component"] == "models"
    assert rebuilt["results"][0]["readiness"]["status"] == "ready"

    export_dir = tmp_path / "exports"
    export_commands = {
        "snapshots": ["--date", "2025-07-09", "--output", str(export_dir / "snapshot.csv")],
        "trends": [
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-09",
            "--output",
            str(export_dir / "trends"),
        ],
        "events": [
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-09",
            "--output",
            str(export_dir / "events.csv"),
        ],
        "models": [
            "--start-date",
            "2025-07-09",
            "--end-date",
            "2025-07-09",
            "--output",
            str(export_dir / "models.json"),
        ],
    }
    for domain, arguments in export_commands.items():
        result = runner.invoke(
            app,
            ["export", domain, *arguments, "--json", "--env-file", str(env_file)],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["source"] == "mock"

    assert (export_dir / "snapshot.csv").is_file()
    assert (export_dir / "trends" / "sensor_trends.csv").is_file()
    assert (export_dir / "events.csv").is_file()
    assert (export_dir / "models.json").is_file()


def test_cli_sync_tree_refreshes_reference_only(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)

    result = runner.invoke(
        app,
        ["sync", "--tree", "--json", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["operation"] == "sync"
    assert payload["mode"] == "tree"
    assert payload["status"] == "complete"
    assert payload["row_counts"]["equipment"] == 6

    human = runner.invoke(
        app,
        ["sync", "--tree", "--env-file", str(env_file)],
    )
    assert human.exit_code == 0, human.output
    assert "Mode: tree" in human.stdout
    assert "Reference rows:" in human.stdout


def test_cli_sync_tree_conflicts_with_date(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)

    result = runner.invoke(
        app,
        [
            "sync",
            "--tree",
            "--date",
            "2025-07-09",
            "--json",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "--tree cannot be combined with --date" in payload["error"]


def test_cli_admin_json_failures_are_machine_readable_and_secret_safe(tmp_path: Path) -> None:
    env_file = tmp_path / "invalid.env"
    env_file.write_text(
        f"INSY_DATA_DIR={tmp_path / 'data'}\n"
        "INSY_SOURCE_MODE=api\n"
        "WAITES_ACCESS_TOKEN=do-not-print\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["sync", "--date", "2025-07-09", "--json", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["operation"] == "sync"
    assert payload["status"] == "failed"
    assert "do-not-print" not in result.output


def test_cli_serve_source_mismatch_is_concise_and_traceback_free(tmp_path: Path) -> None:
    mock_env = _env_file(tmp_path, retention="keep")
    first = runner.invoke(
        app,
        ["sync", "--date", "2025-07-09", "--json", "--env-file", str(mock_env)],
    )
    assert first.exit_code == 0, first.output

    api_env = tmp_path / "api.env"
    api_env.write_text(
        f"INSY_DATA_DIR={tmp_path / 'data'}\nINSY_SOURCE_MODE=api\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["serve", "--env-file", str(api_env)])

    assert result.exit_code == 1
    assert "source" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_rejects_removed_keep_native_option(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path)

    result = runner.invoke(
        app,
        [
            "sync",
            "--date",
            "2025-07-09",
            "--keep-native",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 2
    assert "No such option" in result.output

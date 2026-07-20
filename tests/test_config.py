from pathlib import Path

import pytest

from insy_sensor_data.config import AppSettings


def test_settings_defaults_without_env_file() -> None:
    settings = AppSettings.from_env(env_file=None, environ={})

    assert settings.app_env == "local"
    assert settings.source_mode == "mock"
    assert settings.data_dir == Path("data")
    assert settings.waites_facility_id == 679
    assert settings.waites_token_configured is False


def test_settings_load_env_file_and_process_env_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "INSY_APP_ENV=file",
                "INSY_SOURCE_MODE=api",
                "INSY_DATA_DIR=file-data",
                "WAITES_FACILITY_ID=111",
                "MAXIMO_QUERY_TIMEOUT_SECONDS=45",
            ]
        ),
        encoding="utf-8",
    )

    settings = AppSettings.from_env(
        env_file=env_file,
        environ={
            "INSY_SOURCE_MODE": "mock",
            "INSY_DATA_DIR": "env-data",
        },
    )

    assert settings.app_env == "file"
    assert settings.source_mode == "mock"
    assert settings.data_dir == Path("env-data")
    assert settings.waites_facility_id == 111
    assert settings.maximo_query_timeout_seconds == 45


def test_settings_reject_invalid_source_mode() -> None:
    with pytest.raises(ValueError, match="INSY_SOURCE_MODE"):
        AppSettings.from_env(env_file=None, environ={"INSY_SOURCE_MODE": "live"})

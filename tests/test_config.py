from pathlib import Path
from datetime import date

import pytest

from insy_sensor_data.config import AppSettings


def test_settings_defaults_without_env_file() -> None:
    settings = AppSettings.from_env(env_file=None, environ={})

    assert settings.app_env == "local"
    assert settings.source_mode == "mock"
    assert settings.data_dir == Path("data")
    assert settings.waites_facility_id == 679
    assert settings.waites_token_configured is False
    assert settings.source_timezone == "America/Chicago"
    assert settings.sync_start_date is None
    assert settings.raw_retention_mode == "release"


def test_settings_load_env_file_and_process_env_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "INSY_APP_ENV=file",
                "INSY_SOURCE_MODE=api",
                "INSY_DATA_DIR=file-data",
                "WAITES_FACILITY_ID=111",
                "INSY_SOURCE_TIMEZONE=America/New_York",
                "INSY_SYNC_START_DATE=2026-07-01",
                "INSY_RAW_RETENTION=compress",
                "MAXIMO_SITE_ID=TEST",
                "MAXIMO_ASSETNUM_MAX_LENGTH=15",
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
    assert settings.source_timezone == "America/New_York"
    assert settings.sync_start_date == date(2026, 7, 1)
    assert settings.raw_retention_mode == "compress"
    assert settings.maximo_site_id == "TEST"
    assert settings.maximo_assetnum_max_length == 15
    assert settings.maximo_query_timeout_seconds == 45


def test_settings_reject_invalid_source_mode() -> None:
    with pytest.raises(ValueError, match="INSY_SOURCE_MODE"):
        AppSettings.from_env(env_file=None, environ={"INSY_SOURCE_MODE": "live"})


def test_settings_reject_invalid_sync_configuration() -> None:
    with pytest.raises(ValueError, match="INSY_SYNC_START_DATE"):
        AppSettings.from_env(env_file=None, environ={"INSY_SYNC_START_DATE": "July 1"})
    with pytest.raises(ValueError, match="INSY_SOURCE_TIMEZONE"):
        AppSettings.from_env(env_file=None, environ={"INSY_SOURCE_TIMEZONE": "Mars/Base"})
    with pytest.raises(ValueError, match="INSY_RAW_RETENTION"):
        AppSettings.from_env(env_file=None, environ={"INSY_RAW_RETENTION": "forever"})

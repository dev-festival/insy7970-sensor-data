from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_WAITES_BASE_URL = "https://data.api.waites.net/v1_1"
VALID_SOURCE_MODES = {"mock", "api"}
VALID_RAW_RETENTION_MODES = {"release", "compress", "keep"}


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "INSY Sensor Data"
    app_env: str = "local"
    source_mode: str = "mock"
    data_dir: Path = Path("data")
    waites_base_url: str = DEFAULT_WAITES_BASE_URL
    waites_access_token: str = ""
    waites_facility_id: int = 679
    source_timezone: str = "America/Chicago"
    sync_start_date: date | None = None
    raw_retention_mode: str = "release"
    maximo_dsn: str = "MaximoMAS9"
    maximo_schema: str = "MAXIMO"
    maximo_site_id: str = "HMA"
    maximo_assetnum_max_length: int = 12
    maximo_query_timeout_seconds: int = 30

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = ".env",
        environ: Mapping[str, str] | None = None,
    ) -> AppSettings:
        env = dict(_read_env_file(env_file))
        env.update(dict(os.environ if environ is None else environ))

        source_mode = env.get("INSY_SOURCE_MODE", cls.source_mode).strip().lower()
        if source_mode not in VALID_SOURCE_MODES:
            allowed = ", ".join(sorted(VALID_SOURCE_MODES))
            raise ValueError(f"INSY_SOURCE_MODE must be one of: {allowed}")

        source_timezone = env.get("INSY_SOURCE_TIMEZONE", cls.source_timezone).strip()
        try:
            ZoneInfo(source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"INSY_SOURCE_TIMEZONE is not a recognized timezone: {source_timezone}"
            ) from exc
        raw_retention_mode = env.get(
            "INSY_RAW_RETENTION",
            cls.raw_retention_mode,
        ).strip().lower()
        if raw_retention_mode not in VALID_RAW_RETENTION_MODES:
            allowed = ", ".join(sorted(VALID_RAW_RETENTION_MODES))
            raise ValueError(f"INSY_RAW_RETENTION must be one of: {allowed}")

        return cls(
            app_env=env.get("INSY_APP_ENV", cls.app_env),
            source_mode=source_mode,
            data_dir=Path(env.get("INSY_DATA_DIR", str(cls.data_dir))),
            waites_base_url=env.get("WAITES_BASE_URL", cls.waites_base_url),
            waites_access_token=env.get("WAITES_ACCESS_TOKEN", cls.waites_access_token),
            waites_facility_id=_get_int(env, "WAITES_FACILITY_ID", cls.waites_facility_id),
            source_timezone=source_timezone,
            sync_start_date=_get_date(env, "INSY_SYNC_START_DATE"),
            raw_retention_mode=raw_retention_mode,
            maximo_dsn=env.get("MAXIMO_DSN", cls.maximo_dsn),
            maximo_schema=env.get("MAXIMO_SCHEMA", cls.maximo_schema).strip() or cls.maximo_schema,
            maximo_site_id=env.get("MAXIMO_SITE_ID", cls.maximo_site_id).strip() or cls.maximo_site_id,
            maximo_assetnum_max_length=_get_positive_int(
                env,
                "MAXIMO_ASSETNUM_MAX_LENGTH",
                cls.maximo_assetnum_max_length,
            ),
            maximo_query_timeout_seconds=_get_int(
                env,
                "MAXIMO_QUERY_TIMEOUT_SECONDS",
                cls.maximo_query_timeout_seconds,
            ),
        )

    @property
    def waites_token_configured(self) -> bool:
        return bool(self.waites_access_token and self.waites_access_token != "replace-me")


def _read_env_file(env_file: str | Path | None) -> dict[str, str]:
    if env_file is None:
        return {}

    path = Path(env_file)
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw_value = env.get(key)
    if raw_value is None or raw_value == "":
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _get_positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = _get_int(env, key, default)
    if value < 1:
        raise ValueError(f"{key} must be greater than zero")
    return value


def _get_date(env: Mapping[str, str], key: str) -> date | None:
    raw_value = env.get(key, "").strip()
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must use YYYY-MM-DD format") from exc

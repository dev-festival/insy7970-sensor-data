from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

from workload_core.calendar import build_calendar
from workload_core.config import AppConfig
from workload_core.data import ReportRequest
from workload_core.follow_up import build_follow_up
from workload_core.params import forecast_params, report_params, site_dept_params, site_only_params
from workload_core.query_loader import read_query


@dataclass(frozen=True)
class DbCheckResult:
    ok: bool
    message: str


def build_connection_string(config: AppConfig) -> str:
    if config.db_connection_string:
        return config.db_connection_string

    parts = [f"DSN={config.db_dsn}"]
    if config.db_user:
        parts.append(f"UID={config.db_user}")
    if config.db_password:
        parts.append(f"PWD={config.db_password}")
    return ";".join(parts)


def get_connection(config: AppConfig):
    import pyodbc

    return pyodbc.connect(build_connection_string(config))


def check_connection(config: AppConfig, timeout: int = 5) -> DbCheckResult:
    import pyodbc

    try:
        conn = pyodbc.connect(build_connection_string(config), timeout=timeout)
        conn.close()
    except pyodbc.Error as exc:
        return DbCheckResult(ok=False, message=str(exc))
    return DbCheckResult(ok=True, message="connection ok")


def run_query(config: AppConfig, sql: str, params: tuple):
    import pandas as pd

    conn = get_connection(config)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="pandas only supports SQLAlchemy",
                category=UserWarning,
            )
            return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()


def load_all_live(
    config: AppConfig,
    request: ReportRequest,
):
    like_params = report_params(
        request.site_id,
        request.dept_1,
        request.dept_2,
        request.start_date,
        request.end_date,
    )
    dept_params = site_dept_params(request.site_id, request.dept_1, request.dept_2)
    site_params = site_only_params(request.site_id)
    pm_forecast_params = forecast_params(
        request.site_id,
        request.dept_1,
        request.dept_2,
        request.start_date,
        request.end_date,
    )
    query_dir: Path = config.query_dir

    cm = run_query(config, read_query(query_dir, "cm_wo.sql"), like_params)
    pc = run_query(config, read_query(query_dir, "pc_wo.sql"), like_params)

    return {
        "asset": run_query(config, read_query(query_dir, "assets.sql"), dept_params),
        "calendar": build_calendar(request.start_date, request.end_date),
        "pm": run_query(config, read_query(query_dir, "pm_wo.sql"), like_params),
        "pc": pc,
        "cm": cm,
        "bdm": run_query(config, read_query(query_dir, "bdm_wo.sql"), like_params),
        "classstructure": run_query(config, read_query(query_dir, "class.sql"), ()),
        "proj": run_query(config, read_query(query_dir, "proj_wo.sql"), like_params),
        "follow_up": build_follow_up(cm, pc),
        "pm_forecast": run_query(
            config,
            read_query(query_dir, "pm_forecast.sql"),
            pm_forecast_params,
        ),
        "parts": run_query(config, read_query(query_dir, "parts_orders.sql"), like_params),
        "labor": run_query(config, read_query(query_dir, "labor.sql"), dept_params),
        "location": run_query(config, read_query(query_dir, "locations.sql"), dept_params),
        "person": run_query(config, read_query(query_dir, "person.sql"), site_params),
        "pm_metadata": run_query(config, read_query(query_dir, "pms.sql"), dept_params),
        "persongroup": run_query(config, read_query(query_dir, "persongroup.sql"), dept_params),
    }

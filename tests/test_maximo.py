from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.maximo.db import MaximoDatabaseError, fetch_asset_workorders
from insy_sensor_data.maximo.fixtures import load_workorder_fixture
from insy_sensor_data.maximo.history import load_asset_history
from insy_sensor_data.maximo.queries import read_query, render_query, render_workorder_query


def test_mock_asset_history_filters_by_asset_and_report_date() -> None:
    payload = load_asset_history(
        settings=AppSettings(),
        assetnums=["levf454ts", "MAXIMO-ONLY"],
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
        source="mock",
    )

    assert payload["status"] == "available"
    assert payload["assetnums"] == ["LEVF454TS", "MAXIMO-ONLY"]
    assert payload["row_count"] == 1
    assert payload["rows"] == [
        {
            "wonum": "1234570",
            "assetnum": "LEVF454TS",
            "reportdate": "2025-07-10",
            "description": "Open inspection for steel pinch roll",
            "worktype": "CM",
            "status": "APPR",
            "actfinish": "",
        }
    ]


def test_maximo_fixture_loader_preserves_explicit_directory_override(tmp_path: Path) -> None:
    maximo_dir = tmp_path / "maximo"
    maximo_dir.mkdir()
    rows = [{"wonum": "TEST-1"}]
    (maximo_dir / "workorders.json").write_text(
        json.dumps({"list": rows}),
        encoding="utf-8",
    )

    assert load_workorder_fixture(fixture_dir=tmp_path) == rows


def test_live_history_uses_one_bounded_query_for_distinct_assets() -> None:
    calls: list[tuple[tuple[str, ...], date, date]] = []

    def query_runner(_settings: AppSettings, assetnums, start_date: date, end_date: date):
        calls.append((tuple(assetnums), start_date, end_date))
        return [
            {
                "WONUM": f"WO-{assetnum}",
                "ASSETNUM": assetnum,
                "REPORTDATE": date(2025, 7, 10),
                "DESCRIPTION": "Live row",
                "WORKTYPE": "CM",
                "STATUS": "APPR",
            }
            for assetnum in assetnums
        ]

    payload = load_asset_history(
        settings=AppSettings(),
        assetnums=[" asset-b ", "ASSET-A", "asset-b", ""],
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
        source="api",
        query_runner=query_runner,
    )

    assert calls == [
        (("ASSET-A", "ASSET-B"), date(2025, 7, 9), date(2025, 7, 11)),
    ]
    assert [row["wonum"] for row in payload["rows"]] == ["WO-ASSET-B", "WO-ASSET-A"]


def test_live_history_skips_overlength_waites_asset_values() -> None:
    calls: list[tuple[str, ...]] = []

    def query_runner(_settings: AppSettings, assetnums, _start_date: date, _end_date: date):
        calls.append(tuple(assetnums))
        return [
            {
                "WONUM": "WO-104068",
                "ASSETNUM": "A104068",
                "REPORTDATE": "2026-07-10",
                "DESCRIPTION": "Valid work order",
                "WORKTYPE": "CM",
                "STATUS": "APPR",
            }
        ]

    payload = load_asset_history(
        settings=AppSettings(maximo_assetnum_max_length=12),
        assetnums=["1PA - P-13-1", "A104068"],
        start_date=date(2026, 7, 9),
        end_date=date(2026, 7, 11),
        source="api",
        query_runner=query_runner,
    )

    assert calls == [("A104068",)]
    assert payload["status"] == "partial"
    assert payload["queried_assetnums"] == ["A104068"]
    assert payload["skipped_assets"] == [
        {
            "assetnum": "1PA - P-13-1",
            "reason": "contains whitespace and is not a Maximo asset identifier",
        }
    ]
    assert payload["rows"][0]["wonum"] == "WO-104068"


def test_live_history_isolates_unexpected_single_asset_data_errors() -> None:
    calls: list[tuple[str, ...]] = []

    def query_runner(_settings: AppSettings, assetnums, _start_date: date, _end_date: date):
        assets = tuple(assetnums)
        calls.append(assets)
        if "BAD" in assets:
            raise MaximoDatabaseError("SQLSTATE=22001 String data right truncation")
        return [
            {
                "WONUM": "WO-104068",
                "ASSETNUM": "A104068",
                "REPORTDATE": "2026-07-10",
                "DESCRIPTION": "Valid work order",
                "WORKTYPE": "CM",
                "STATUS": "APPR",
            }
        ]

    payload = load_asset_history(
        settings=AppSettings(),
        assetnums=["BAD", "A104068"],
        start_date=date(2026, 7, 9),
        end_date=date(2026, 7, 11),
        source="api",
        query_runner=query_runner,
    )

    assert calls == [("A104068", "BAD"), ("A104068",), ("BAD",)]
    assert payload["status"] == "partial"
    assert payload["rows"][0]["wonum"] == "WO-104068"
    assert payload["skipped_assets"] == [
        {"assetnum": "BAD", "reason": "SQLSTATE=22001 String data right truncation"}
    ]


def test_query_loader_validates_names_and_schema_substitution() -> None:
    template = render_query("wo.sql", "MAXIMO")
    sql = render_workorder_query("MAXIMO", 2)

    assert "{{maximo_schema}}" not in template
    assert "FROM MAXIMO.WORKORDER" in sql
    assert "'?" not in sql
    assert "{{asset_placeholders}}" not in sql
    assert sql.count("?") == 10
    assert "UNION" in sql
    assert read_query("wo.sql")

    with pytest.raises(ValueError, match="single .sql filename"):
        read_query("../wo.sql")
    with pytest.raises(ValueError, match="SQL identifier"):
        render_query("wo.sql", "MAXIMO; DROP TABLE WORKORDER")


def test_live_db_runner_binds_site_asset_and_half_open_dates(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeError(Exception):
        pass

    class FakeCursor:
        description = [("WONUM",), ("ASSETNUM",), ("REPORTDATE",)]

        def __setattr__(self, name, value):
            if name == "timeout":
                raise FakeError("statement timeout unsupported")
            super().__setattr__(name, value)

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return [("WO-1", "ASSET-1", date(2025, 7, 10))]

    class FakeConnection:
        timeout = None

        def cursor(self):
            return FakeCursor()

        def close(self):
            captured["closed"] = True

    def connect(connection_string):
        captured["connection_string"] = connection_string
        return FakeConnection()

    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=connect, Error=FakeError))
    settings = AppSettings(maximo_schema="MAXIMO", maximo_site_id="HMA", maximo_query_timeout_seconds=13)

    rows = fetch_asset_workorders(
        settings=settings,
        assetnums=["ASSET-1"],
        start_date=date(2025, 7, 9),
        end_date=date(2025, 7, 11),
    )

    assert rows == [{"wonum": "WO-1", "assetnum": "ASSET-1", "reportdate": date(2025, 7, 10)}]
    assert captured["connection_string"] == "DSN=MaximoMAS9"
    assert captured["params"] == (
        "HMA",
        "ASSET-1",
        date(2025, 7, 9),
        date(2025, 7, 12),
        "HMA",
        "ASSET-1",
        date(2025, 7, 9),
        date(2025, 7, 12),
    )
    assert captured["closed"] is True

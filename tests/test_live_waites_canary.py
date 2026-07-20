from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import os

import pytest

from insy_sensor_data.config import AppSettings
from insy_sensor_data.waites.fetch import fetch_waites


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("INSY_RUN_LIVE_TESTS") != "1",
        reason="Set INSY_RUN_LIVE_TESTS=1 to run live Waites canary tests.",
    ),
]


def test_live_waites_canary_fetches_raw_evidence(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["INSY_DATA_DIR"] = str(tmp_path / "data")
    settings = AppSettings.from_env(environ=env)
    run_date = date.fromisoformat(
        env.get("INSY_LIVE_WAITES_DATE", (date.today() - timedelta(days=1)).isoformat())
    )

    summary = fetch_waites(
        settings=settings,
        run_date=run_date,
        facility_id=settings.waites_facility_id,
        source="api",
    )

    assert summary["source"] == "api"
    assert summary["endpoint_count"] == 6
    assert Path(summary["manifest_path"]).exists()

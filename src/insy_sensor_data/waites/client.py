from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any


ENDPOINT_FILENAMES = {
    "equipment": "equipment.json",
    "installation-points": "installation-points.json",
    "readings-rms": "readings-rms.json",
    "readings-impact-vue": "readings-impact-vue.json",
    "readings-temperature": "readings-temperature.json",
    "action-items": "action-items.json",
}

READING_ENDPOINTS = {
    "readings-rms",
    "readings-impact-vue",
    "readings-temperature",
}


@dataclass(frozen=True)
class WaitesRequest:
    endpoint: str
    filename: str
    params: dict[str, Any]


def build_waites_requests(run_date: date, facility_id: int) -> list[WaitesRequest]:
    return [
        WaitesRequest("equipment", ENDPOINT_FILENAMES["equipment"], _facility_params(facility_id)),
        WaitesRequest(
            "installation-points",
            ENDPOINT_FILENAMES["installation-points"],
            _facility_params(facility_id),
        ),
        WaitesRequest(
            "readings-rms",
            ENDPOINT_FILENAMES["readings-rms"],
            _reading_params(run_date, facility_id),
        ),
        WaitesRequest(
            "readings-impact-vue",
            ENDPOINT_FILENAMES["readings-impact-vue"],
            _reading_params(run_date, facility_id),
        ),
        WaitesRequest(
            "readings-temperature",
            ENDPOINT_FILENAMES["readings-temperature"],
            _reading_params(run_date, facility_id),
        ),
        WaitesRequest(
            "action-items",
            ENDPOINT_FILENAMES["action-items"],
            {
                **_facility_params(facility_id),
                "action_item_type": "regular",
                "action_item_status": "active",
            },
        ),
    ]


def utc_day_bounds(run_date: date) -> tuple[str, str]:
    start = datetime.combine(run_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(run_date, time.max, tzinfo=timezone.utc)
    return _format_utc(start), _format_utc(end)


def _facility_params(facility_id: int) -> dict[str, Any]:
    return {"facility[]": facility_id}


def _reading_params(run_date: date, facility_id: int) -> dict[str, Any]:
    start_date, end_date = utc_day_bounds(run_date)
    return {
        **_facility_params(facility_id),
        "start_date": start_date,
        "end_date": end_date,
    }


def _format_utc(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")

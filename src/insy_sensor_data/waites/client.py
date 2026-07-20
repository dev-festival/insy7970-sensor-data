from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import time as timer


_TRUSTSTORE_INJECTED = False

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

ENDPOINT_PATHS = {
    "equipment": "equipment",
    "installation-points": "installation-points",
    "readings-rms": "readings/rms",
    "readings-impact-vue": "readings/impact-vue",
    "readings-temperature": "readings/temperature",
    "action-items": "action-items",
}


@dataclass(frozen=True)
class WaitesRequest:
    endpoint: str
    filename: str
    params: dict[str, Any]


@dataclass(frozen=True)
class WaitesApiResponse:
    endpoint: str
    status_code: int
    elapsed_ms: int
    payload: dict[str, Any]


class WaitesApiError(RuntimeError):
    def __init__(
        self,
        endpoint: str,
        message: str,
        status_code: int | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.elapsed_ms = elapsed_ms


class WaitesApiClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        timeout_seconds: int = 30,
        transport: Any = urlopen,
        use_system_truststore: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.use_system_truststore = use_system_truststore

    def fetch(self, request: WaitesRequest) -> WaitesApiResponse:
        if self.use_system_truststore:
            _enable_system_truststore()

        started = timer.perf_counter()
        http_request = Request(
            build_waites_url(self.base_url, request, self.access_token),
            headers={"Accept": "application/json"},
        )
        try:
            with self.transport(http_request, timeout=self.timeout_seconds) as response:
                raw_body = response.read()
                status_code = int(getattr(response, "status", None) or response.getcode())
        except HTTPError as exc:
            elapsed_ms = _elapsed_ms(started)
            detail = _decode_error_body(exc)
            raise WaitesApiError(
                request.endpoint,
                _http_error_message(request.endpoint, exc.code, detail),
                status_code=exc.code,
                elapsed_ms=elapsed_ms,
            ) from exc
        except URLError as exc:
            raise WaitesApiError(
                request.endpoint,
                f"Waites API request failed for {request.endpoint}: {exc.reason}",
                elapsed_ms=_elapsed_ms(started),
            ) from exc

        elapsed_ms = _elapsed_ms(started)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WaitesApiError(
                request.endpoint,
                f"Waites API returned invalid JSON for {request.endpoint}.",
                status_code=status_code,
                elapsed_ms=elapsed_ms,
            ) from exc

        if not isinstance(payload, dict):
            raise WaitesApiError(
                request.endpoint,
                f"Waites API returned unsupported JSON shape for {request.endpoint}: expected object.",
                status_code=status_code,
                elapsed_ms=elapsed_ms,
            )

        return WaitesApiResponse(
            endpoint=request.endpoint,
            status_code=status_code,
            elapsed_ms=elapsed_ms,
            payload=payload,
        )


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


def build_waites_url(base_url: str, request: WaitesRequest, access_token: str) -> str:
    if request.endpoint not in ENDPOINT_PATHS:
        raise ValueError(f"Unknown Waites endpoint: {request.endpoint}")

    params = {**request.params, "access-token": access_token}
    query = urlencode(params, doseq=True)
    return f"{base_url.rstrip('/')}/{ENDPOINT_PATHS[request.endpoint]}?{query}"


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


def _elapsed_ms(started: float) -> int:
    return round((timer.perf_counter() - started) * 1000)


def _enable_system_truststore() -> None:
    global _TRUSTSTORE_INJECTED
    if _TRUSTSTORE_INJECTED:
        return

    import truststore

    truststore.inject_into_ssl()
    _TRUSTSTORE_INJECTED = True


def _decode_error_body(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    return body[:500]


def _http_error_message(endpoint: str, status_code: int, detail: str) -> str:
    if status_code in {401, 403}:
        return f"Waites API authorization failed for {endpoint} with HTTP {status_code}."
    if status_code == 404:
        return f"Waites API endpoint not found for {endpoint}."
    if detail:
        return f"Waites API request failed for {endpoint} with HTTP {status_code}: {detail}"
    return f"Waites API request failed for {endpoint} with HTTP {status_code}."
